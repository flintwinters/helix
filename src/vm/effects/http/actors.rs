//! Serialized actor turns; persistence remains an ordinary captured Helix function.
use super::*;
use std::collections::{BTreeMap, BTreeSet};
use tokio::sync::{mpsc, oneshot};

type Answer = Result<Value, (String, String)>;
struct Message { value: Value, answer: oneshot::Sender<Answer> }
pub(super) struct Actors {
    mailboxes: BTreeMap<String, mpsc::Sender<Message>>,
    pub(super) stopping: std::sync::atomic::AtomicBool,
    waits: std::sync::Mutex<BTreeMap<String, String>>,
}
impl Actors {
    pub(super) async fn call(&self, owner: Option<&str>, target: &str, value: Value, deadline: Instant) -> Answer {
        let Some(mailbox) = self.mailboxes.get(target) else { return Err(fault("actor", "unknown actor")) };
        if let Some(owner) = owner {
            let mut waits = self.waits.lock().map_err(|_| fault("actor", "actor wait graph unavailable"))?;
            let mut next = target;
            let mut seen = BTreeSet::new();
            while seen.insert(next.to_owned()) {
                if next == owner { return Err(fault("actor", "cyclic actor wait")); }
                let Some(target) = waits.get(next) else { break };
                next = target;
            }
            waits.insert(owner.into(), target.into());
        }
        let (answer, receive) = oneshot::channel();
        let sent = mailbox.try_send(Message { value, answer }).map_err(|error| match error {
            mpsc::error::TrySendError::Full(_) => fault("actor", "actor mailbox overflow"),
            mpsc::error::TrySendError::Closed(_) => fault("actor", "actor unavailable until restart"),
        });
        let result = match sent {
            Err(error) => Err(error),
            Ok(()) => match tokio::time::timeout_at(deadline, receive).await {
                Ok(Ok(result)) => result,
                Ok(Err(_)) => Err(fault("actor", "actor unavailable until restart")),
                Err(_) => Err(fault("actor", "actor reply deadline exceeded; transition outcome may be uncertain")),
            },
        };
        if let Some(owner) = owner { self.waits.lock().unwrap().remove(owner); }
        result
    }
}

pub(super) async fn start(options: &Value, parent_promises: &[Value], host: &EffectHost, policy: &ServerPolicy,
    tasks: &mut JoinSet<()>) -> Result<Arc<Actors>, super::super::Failure> {
    let Value::Hash(options) = options else { unreachable!() };
    let definitions = match options.get(&s("actors")) {
        Some(Value::Hash(definitions)) => definitions.iter().map(|(k,v)| (k.as_str().unwrap().to_owned(),v.clone())).collect::<Vec<_>>(),
        _ => Vec::new(),
    };
    if definitions.len() > policy.actors { return Err(fault("resource", "actor capacity exceeded")); }
    let mut mailboxes = BTreeMap::new();
    let mut pending = Vec::new();
    for (name, definition) in definitions {
        if name.is_empty() { return Err(fault("type", "actor name must be nonempty")); }
        let Value::Hash(fields) = &definition else { return Err(fault("type", "actor definition must be a map")) };
        if fields.iter().any(|(key,_)| !matches!(key.as_str(),Some("initial" | "receive" | "initialize" | "promise"))) {
            return Err(fault("type", "unknown actor definition field"));
        }
        let mut state = fields.get(&s("initial")).cloned().ok_or_else(|| fault("type", "actor requires initial state"))?;
        if state.plain_yaml().is_none() { return Err(fault("type", "actor state must be ordinary data")); }
        let Some(Value::Function(receive)) = fields.get(&s("receive")) else { return Err(fault("type", "actor receive must be a function")) };
        let mut promises = parent_promises.to_vec();
        if let Some(promise) = fields.get(&s("promise")) {
            crate::builtins::typing::promise::validate(promise).map_err(|why| fault("type", &why))?;
            promises.push(promise.clone());
        }
        if let Some(initialize) = fields.get(&s("initialize")) {
            let Value::Function(initialize) = initialize else { return Err(fault("type", "actor initialize must be a function")) };
            let machine = invoke(initialize.clone(),vec![state],&promises).map_err(|why| fault("promise", &why))?;
            state = execute(machine,worker_host(host),Instant::now()+Duration::from_millis(policy.deadline_ms),None,None).await
                .map_err(|why| fault("actor", &why))?;
            if state.plain_yaml().is_none() { return Err(fault("type", "loaded actor state must be ordinary data")); }
        }
        let (send, receive_messages) = mpsc::channel(policy.mailbox);
        mailboxes.insert(name.clone(),send);
        pending.push((name,state,receive.clone(),promises,receive_messages,worker_host(host)));
    }
    let actors = Arc::new(Actors { mailboxes, waits: Default::default(), stopping: Default::default() });
    for (name,mut state,receive,promises,mut messages,host) in pending {
        let actors = actors.clone(); let policy = policy.clone();
        tasks.spawn(async move {
            loop {
                if actors.stopping.load(Ordering::Relaxed) && messages.is_empty() { break; }
                let message = tokio::select! {
                    message = messages.recv() => match message { Some(message) => message, None => break },
                    _ = tokio::time::sleep(Duration::from_millis(10)) => {
                        if cancelled(&host) { break; }
                        continue;
                    }
                };
                let result = match invoke(receive.clone(),vec![state.clone(),message.value],&promises) {
                    Err(why) => Err(why),
                    Ok(vm) => execute(vm,worker_host(&host),Instant::now()+Duration::from_millis(policy.deadline_ms),Some(actors.clone()),Some(name.clone())).await,
                }.and_then(|value| {
                    let Value::Hash(fields) = value else { return Err("actor turn must return state and reply".into()) };
                    if fields.len() != 2 { return Err("actor turn must return exactly state and reply".into()); }
                    let next = fields.get(&s("state")).filter(|v| v.plain_yaml().is_some()).cloned().ok_or("actor state must be ordinary data")?;
                    let reply = fields.get(&s("reply")).filter(|v| v.plain_yaml().is_some()).cloned().ok_or("actor reply must be ordinary data")?;
                    Ok((next,reply))
                });
                match result {
                    Ok((next,reply)) => { state = next; let _ = message.answer.send(Ok(reply)); },
                    Err(why) => {
                        messages.close();
                        let _ = message.answer.send(Err(fault("actor", &why)));
                        while let Some(message) = messages.recv().await {
                            let _ = message.answer.send(Err(fault("actor", "actor unavailable until restart")));
                        }
                        break;
                    }
                }
            }
        });
    }
    Ok(actors)
}

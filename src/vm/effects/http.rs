//! Live HTTP transport. Authority and ceilings belong to the caller, never source.
use super::{EffectHost, Reply, fault, io_fault, object};
use crate::model::{Value, Function, Request, Vm, Status, string as s};
use std::{sync::{Arc, atomic::{AtomicU64, Ordering}}, time::Duration, net::SocketAddr, convert::Infallible};
use bytes::Bytes;
use hyper::{Request as HttpRequest, Response, body::Incoming, server::conn::http1, service::service_fn};
use http_body_util::{BodyExt, Full};
use hyper_util::rt::{TokioIo, TokioTimer};
use tokio::{sync::Semaphore, task::JoinSet, time::Instant};
use yaml_rust2::Yaml;


#[derive(Clone, Debug)]
pub struct ServerPolicy {
    pub connections: usize,
    pub handlers: usize,
    pub actors: usize,
    pub mailbox: usize,
    pub headers: usize,
    pub body: usize,
    pub static_file: usize,
    pub deadline_ms: u64,
}
impl Default for ServerPolicy {
    fn default() -> Self {
        Self { connections: 32, handlers: 8, actors: 8, mailbox: 64, headers: 16 * 1024,
            body: 256 * 1024, static_file: 1024 * 1024, deadline_ms: 10_000 }
    }
}
impl ServerPolicy {
    pub fn validate(&self) -> Result<(), String> {
        if self.connections == 0 || self.connections > 4096 || self.handlers == 0 || self.handlers > 256
            || self.actors > 256 || self.mailbox == 0 || self.mailbox > 4096 || self.headers < 8192
            || self.headers > 1024 * 1024 || self.body == 0 || self.body > 1024 * 1024
            || self.static_file == 0 || self.static_file > 16 * 1024 * 1024
            || self.deadline_ms == 0 || self.deadline_ms > 86_400_000 {
            Err("invalid HTTP server policy bounds".into())
        } else { Ok(()) }
    }
}
pub type ServerProgress = Box<dyn FnMut(&serde_json::Value) -> Reply + Send>;

pub(crate) fn validate_options(options: &Value) -> Result<(), String> {
    let Value::Hash(fields) = options else { return Err("HTTP options must be a map".into()) };
    let Some(Value::String(bind)) = fields.get(&s("bind")) else { return Err("HTTP options require bind".into()) };
    let address: SocketAddr = bind.parse().map_err(|_| "bind must be a numeric loopback address")?;
    if !address.ip().is_loopback() { return Err("only numeric loopback listeners are supported".into()); }
    for (key, value) in fields.iter() {
        match key.as_str() {
            Some("bind") => {},
            Some("requests") if matches!(value, Value::Integer(n) if *n > 0) => {},
            Some("promise") => crate::builtins::typing::promise::validate(value)?,
            Some("actors") if matches!(value, Value::Hash(_)) => {},
            _ => return Err("unknown or invalid HTTP option".into()),
        }
    }
    Ok(())
}

fn worker_host(parent: &EffectHost) -> EffectHost {
    EffectHost {
        read_roots: parent.read_roots.clone(), write_roots: parent.write_roots.clone(),
        env_names: parent.env_names.clone(), commands: parent.commands.clone(),
        secret_files: parent.secret_files.clone(), command_policy: parent.command_policy.clone(),
        cancellation: parent.cancellation.clone(), cancellation_signal: parent.cancellation_signal,
        http_origins: parent.http_origins.clone(), ssh_targets: parent.ssh_targets.clone(),
        ssh_control_dir: parent.ssh_control_dir.clone(), allow_stdout: parent.allow_stdout,
        allow_time: parent.allow_time, allow_random: parent.allow_random, args: parent.args.clone(),
        fixed_time: parent.fixed_time, random_seed: parent.random_seed,
        ..EffectHost::default()
    }
}
fn cancelled(host: &EffectHost) -> bool {
    host.cancellation.load(Ordering::Relaxed) || host.cancellation_signal.is_some_and(|flag| flag.load(Ordering::Relaxed))
}
fn invoke(function: Arc<Function>, args: Vec<Value>, promises: &[Value]) -> Result<Vm, String> {
    let mut vm = Vm::from_function(function.clone());
    vm.evaluator = Some(Arc::new(crate::eval::Evaluator::invoke(function, args)));
    for promise in promises { vm = vm.with_promise_value(promise.clone())?; }
    Ok(vm)
}
async fn execute(mut vm: Vm, mut host: EffectHost, deadline: Instant) -> Result<Value, String> {
    loop {
        if cancelled(&host) || Instant::now() >= deadline { return Err("worker cancelled or deadline exceeded".into()); }
        // Bounded quanta let transport and other workers progress even on one core.
        for _ in 0..128 {
            if !matches!(vm.status(), Status::Ready | Status::Running) { break; }
            vm = vm.step();
        }
        match vm.status() {
            Status::Finished => return vm.result.clone().ok_or("missing worker result".into()),
            Status::Error => return Err(vm.state_string()),
            Status::Signaled => {
                let Some(request) = vm.pending_effect() else { return Err("worker interrupted".into()) };
                if matches!(request.operation.as_str(), "stdin" | "exit" | "http_serve") {
                    vm = vm.resolve_effect(Err(fault("effect", "operation forbidden in supervised worker")));
                } else {
                    // Keep the worker's capacity until any started blocking action settles.
                    (vm, host) = tokio::task::spawn_blocking(move || {
                        host.resolve_pending(&mut vm);
                        (vm, host)
                    }).await.map_err(|why| why.to_string())?;
                }
            }
            _ => tokio::task::yield_now().await,
        }
    }
}
fn response(status: u16, body: impl Into<Bytes>) -> Response<Full<Bytes>> {
    Response::builder().status(status).header("connection", "close").body(Full::new(body.into())).unwrap()
}
fn checked_response(value: Value, limit: usize, head: bool) -> Result<Response<Full<Bytes>>, String> {
    let data = value.plain_yaml().ok_or("response must be ordinary data")?;
    let status = data["status"].as_i64().filter(|n| (200..=599).contains(n)).ok_or("invalid HTTP status")? as u16;
    let headers = data["headers"].as_vec().ok_or("response headers must be ordered pairs")?;
    let text = data["body"].as_str().ok_or("response body must be text")?;
    if text.len() > limit { return Err("response body exceeds policy".into()); }
    let mut result = response(status, if head { Bytes::new() } else { Bytes::copy_from_slice(text.as_bytes()) });
    for pair in headers {
        let pair = pair.as_vec().filter(|p| p.len() == 2).ok_or("header must be a pair")?;
        let name = hyper::header::HeaderName::from_bytes(pair[0].as_str().ok_or("header name must be text")?.as_bytes()).map_err(|e| e.to_string())?;
        if matches!(name.as_str(), "connection" | "content-length" | "transfer-encoding" | "trailer" | "upgrade") {
            return Err("transport framing headers are caller-owned".into());
        }
        let value = hyper::header::HeaderValue::from_str(pair[1].as_str().ok_or("header value must be text")?).map_err(|e| e.to_string())?;
        result.headers_mut().append(name, value);
    }
    result.headers_mut().insert(hyper::header::CONTENT_LENGTH, text.len().into());
    Ok(result)
}

pub(super) fn serve(host: &mut EffectHost, vm: &Vm) -> Reply {
    host.server_policy.validate().map_err(|why| fault("resource", &why))?;
    let Some(Request::Serve(_, options, handler)) = vm.evaluator.as_ref().and_then(|e| e.requests.last()) else {
        return Err(fault("effect", "http_serve requires the validated engine continuation"));
    };
    validate_options(options).map_err(|why| fault("type", &why))?;
    let Value::Hash(fields) = options else { unreachable!() };
    let address: SocketAddr = fields.get(&s("bind")).and_then(Value::as_str).unwrap().parse().unwrap();
    if !host.listen_addresses.contains(&address) { return Err(fault("permission", "listener address is not granted")); }
    let mut promises = vm.promises.clone();
    if let Some(promise) = fields.get(&s("promise")) { promises.push(promise.clone()); }
    let max_requests = match fields.get(&s("requests")) { Some(Value::Integer(n)) => Some(*n as u64), _ => None };
    let handler = handler.clone();
    let policy = host.server_policy.clone();
    let template = worker_host(host);
    let listener = std::net::TcpListener::bind(address).map_err(io_fault)?;
    listener.set_nonblocking(true).map_err(io_fault)?;
    let actual = listener.local_addr().map_err(io_fault)?;
    if let Some(progress) = host.server_progress.as_mut() {
        progress(&serde_json::json!({"version":1,"event":"ready","address":actual.to_string()}))?;
    }
    let runtime = tokio::runtime::Builder::new_multi_thread().worker_threads(policy.handlers.min(8))
        .max_blocking_threads(policy.handlers).enable_all().build().map_err(io_fault)?;
    let count = Arc::new(AtomicU64::new(0));
    let failures = Arc::new(AtomicU64::new(0));
    let result = runtime.block_on(async {
        let listener = tokio::net::TcpListener::from_std(listener).map_err(io_fault)?;
        let capacity = Arc::new(Semaphore::new(policy.handlers));
        let mut connections = JoinSet::new();
        let mut workers = JoinSet::new();
        let (work_tx, mut work_rx) = tokio::sync::mpsc::channel::<(Vm, EffectHost, Instant, tokio::sync::oneshot::Sender<Result<Value, String>>, tokio::sync::OwnedSemaphorePermit)>(policy.handlers);
        loop {
            if cancelled(&template) || max_requests.is_some_and(|max| count.load(Ordering::Relaxed) >= max) { break; }
            tokio::select! {
                Some((machine, worker, deadline, reply, permit)) = work_rx.recv() => {
                    workers.spawn(async move { let result = execute(machine, worker, deadline).await; let _ = reply.send(result); drop(permit); });
                }
                Some(_) = connections.join_next(), if !connections.is_empty() => {},
                Some(_) = workers.join_next(), if !workers.is_empty() => {},
                accepted = listener.accept() => {
                    let (stream, _) = accepted.map_err(io_fault)?;
                    if connections.len() >= policy.connections {
                        use tokio::io::AsyncWriteExt;
                        let mut stream = stream;
                        let _ = tokio::time::timeout(Duration::from_millis(100), stream.write_all(b"HTTP/1.1 503 Service Unavailable\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")).await;
                        continue;
                    }
                    let capacity = capacity.clone(); let tx = work_tx.clone(); let handler = handler.clone();
                    let promises = promises.clone(); let worker_template = worker_host(&template);
                    let policy = policy.clone(); let count = count.clone(); let failures = failures.clone();
                    connections.spawn(async move {
                        let connection_policy = policy.clone();
                        let service = service_fn(move |request: HttpRequest<Incoming>| {
                            let capacity = capacity.clone(); let tx = tx.clone(); let handler = handler.clone();
                            let promises = promises.clone(); let worker = worker_host(&worker_template);
                            let count = count.clone(); let failures = failures.clone(); let policy = policy.clone();
                            async move {
                                let deadline = Instant::now() + Duration::from_millis(policy.deadline_ms);
                                let ordinal = count.fetch_add(1, Ordering::Relaxed);
                                if max_requests.is_some_and(|max| ordinal >= max) { return Ok::<_, Infallible>(response(503, "capacity exhausted")); }
                                let Ok(permit) = capacity.try_acquire_owned() else { return Ok(response(503, "capacity exhausted")) };
                                let head = request.method() == hyper::Method::HEAD;
                                let headers = request.headers().iter().map(|(name,value)| Ok(Yaml::Array(vec![Yaml::String(name.to_string()),Yaml::String(value.to_str().map_err(|_| ())?.into())]))).collect::<Result<Vec<_>, ()>>();
                                let Ok(headers) = headers else { return Ok(response(400, "invalid headers")) };
                                let target = request.uri().to_string(); let path = request.uri().path().to_owned();
                                let query = request.uri().query().unwrap_or("").to_owned(); let method = request.method().to_string();
                                let collected = tokio::time::timeout_at(deadline, http_body_util::Limited::new(request.into_body(), policy.body).collect()).await;
                                let body = match collected { Err(_) => return Ok(response(504,"deadline exceeded")), Ok(Err(_)) => return Ok(response(413,"body too large or invalid")), Ok(Ok(body)) => body.to_bytes() };
                                let Ok(body) = String::from_utf8(body.to_vec()) else { return Ok(response(400,"body must be UTF-8")) };
                                let request = Value::from_yaml(&object([("method",Yaml::String(method)),("target",Yaml::String(target)),("path",Yaml::String(path)),("query",Yaml::String(query)),("headers",Yaml::Array(headers)),("body",Yaml::String(body))]));
                                let machine = match invoke(handler, vec![request], &promises) { Ok(machine) => machine, Err(_) => return Ok(response(500,"handler failed")) };
                                let (reply, receive) = tokio::sync::oneshot::channel();
                                if tx.send((machine,worker,deadline,reply,permit)).await.is_err() { return Ok(response(503,"server stopping")) }
                                let result = match tokio::time::timeout_at(deadline,receive).await {
                                    Err(_) => response(504,"deadline exceeded"),
                                    Ok(Ok(Ok(value))) => checked_response(value,policy.body,head).unwrap_or_else(|_| response(500,"invalid response")),
                                    _ => { failures.fetch_add(1,Ordering::Relaxed); response(500,"handler failed") }
                                };
                                Ok(result)
                            }
                        });
                        let mut builder = http1::Builder::new();
                        let policy = connection_policy;
                        builder.keep_alive(false).half_close(false).max_headers(128).max_buf_size(policy.headers)
                            .max_header_size(policy.headers).timer(TokioTimer::new()).header_read_timeout(Duration::from_millis(policy.deadline_ms));
                        let _ = tokio::time::timeout(Duration::from_millis(policy.deadline_ms), builder.serve_connection(TokioIo::new(stream), service)).await;
                    });
                }
                _ = tokio::time::sleep(Duration::from_millis(10)) => {},
            }
        }
        drop(listener);
        // Accepted work retains capacity until blocking effects settle, even after a timeout.
        while !connections.is_empty() || !workers.is_empty() || !work_rx.is_empty() {
            tokio::select! {
                Some((machine,worker,deadline,reply,permit)) = work_rx.recv(), if !work_rx.is_empty() => {
                    workers.spawn(async move { let _ = reply.send(execute(machine,worker,deadline).await); drop(permit); });
                }
                Some(_) = connections.join_next(), if !connections.is_empty() => {},
                Some(_) = workers.join_next(), if !workers.is_empty() => {},
                _ = tokio::time::sleep(Duration::from_millis(10)) => {},
            }
        }
        Ok::<_, super::Failure>(())
    });
    result?;
    let counts = serde_json::json!({"version":1,"event":"shutdown","address":actual.to_string(),"requests":count.load(Ordering::Relaxed),"failures":failures.load(Ordering::Relaxed)});
    if let Some(progress) = host.server_progress.as_mut() { progress(&counts)?; }
    Ok(object([("address",Yaml::String(actual.to_string())),("requests",Yaml::Integer(count.load(Ordering::Relaxed) as i64)),("failures",Yaml::Integer(failures.load(Ordering::Relaxed) as i64))]))
}

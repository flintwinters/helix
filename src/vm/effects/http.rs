//! Live HTTP transport. Authority and ceilings belong to the caller, never source.
use super::host::{EffectHost, ServerPolicy, object};
use super::failure::{Reply, Failure, fault, io_fault};
use crate::model::{Value, Function, Request, Vm, Status, string as s};
use std::{sync::{Arc, atomic::{AtomicU64, Ordering}}, time::Duration, net::SocketAddr, convert::Infallible};
use bytes::Bytes;
use hyper::{Request as HttpRequest, Response, body::Incoming, server::conn::http1, service::service_fn};
use http_body_util::{BodyExt, Full};
use hyper_util::rt::{TokioIo, TokioTimer};
use tokio::{sync::Semaphore, task::JoinSet, time::Instant};
use yaml_rust2::Yaml;
use crate::graph::http::{validate_function, validate_options};
#[path = "http/actors.rs"]
mod actors;
#[path = "http/static_files.rs"]
mod static_files;

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
    validate_function(&function,args.len())?;
    let mut vm = Vm::from_function(function.clone());
    vm.evaluator = Some(Arc::new(crate::eval::Evaluator::invoke(function, args)));
    for promise in promises { vm = vm.with_promise_value(promise.clone())?; }
    Ok(vm)
}
async fn execute(mut vm: Vm, mut host: EffectHost, deadline: Instant, actors: Option<Arc<actors::Actors>>, owner: Option<String>) -> Result<Value, String> {
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
                if request.operation == "actor_call" {
                    let result = if !vm.can_dispatch_effect("actor_call") {
                        Err(fault("promise", "actor calls violate worker promise"))
                    } else if let Some(actors) = &actors {
                        match request.payload["name"].as_str() {
                            Some(name) => actors.call(owner.as_deref(),name,Value::from_yaml(&request.payload["message"]),deadline).await
                                .and_then(|value| value.plain_yaml().ok_or_else(|| fault("type", "actor reply must be ordinary data"))),
                            None => Err(fault("type", "actor call requires a name")),
                        }
                    } else { Err(fault("actor", "actors unavailable outside server workers")) };
                    vm = vm.resolve_effect(result);
                } else if matches!(request.operation.as_str(), "stdin" | "exit" | "http_serve") {
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
fn checked_response(value: Value, policy: &ServerPolicy, roots: &[std::path::PathBuf], head: bool) -> Result<Response<Full<Bytes>>, String> {
    let data = value.plain_yaml().ok_or("response must be ordinary data")?;
    let fields = data.as_hash().ok_or("response must be a map")?;
    if fields.len() != 3 { return Err("response requires exactly status, headers and body".into()); }
    let status = data["status"].as_i64().filter(|n| (200..=599).contains(n)).ok_or("invalid HTTP status")? as u16;
    let headers = data["headers"].as_vec().ok_or("response headers must be ordered pairs")?;
    let bytes = if let Some(text) = data["body"].as_str() {
        if text.len() > policy.body { return Err("response body exceeds policy".into()); }
        text.as_bytes().to_vec()
    } else {
        let body = data["body"].as_hash().ok_or("invalid response body")?;
        if body.len() != 1 || data["body"]["file"].as_hash().is_none_or(|fields| fields.len() != 2) {
            return Err("invalid static descriptor".into());
        }
        match static_files::read(&data["body"]["file"],roots,policy.static_file) {
            Ok(bytes) => bytes,
            Err(_) => return Ok(response(403,"static file unavailable")),
        }
    };
    let length = bytes.len();
    let mut result = response(status, if head { Bytes::new() } else { Bytes::from(bytes) });
    let mut header_bytes = 0;
    for pair in headers {
        let pair = pair.as_vec().filter(|p| p.len() == 2).ok_or("header must be a pair")?;
        header_bytes += pair.iter().filter_map(Yaml::as_str).map(str::len).sum::<usize>() + 4;
        if header_bytes > policy.headers { return Err("response headers exceed policy".into()); }
        let name = hyper::header::HeaderName::from_bytes(pair[0].as_str().ok_or("header name must be text")?.as_bytes()).map_err(|e| e.to_string())?;
        if matches!(name.as_str(), "connection" | "content-length" | "transfer-encoding" | "trailer" | "upgrade") {
            return Err("transport framing headers are caller-owned".into());
        }
        let value = hyper::header::HeaderValue::from_str(pair[1].as_str().ok_or("header value must be text")?).map_err(|e| e.to_string())?;
        result.headers_mut().append(name, value);
    }
    result.headers_mut().insert(hyper::header::CONTENT_LENGTH, length.into());
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
    let parent_promises = vm.promises.clone();
    let mut promises = parent_promises.clone();
    if let Some(promise) = fields.get(&s("promise")) { promises.push(promise.clone()); }
    let max_requests = match fields.get(&s("requests")) { Some(Value::Integer(n)) => Some(*n as u64), _ => None };
    let handler = handler.clone();
    let policy = host.server_policy.clone();
    let template = worker_host(host);
    let runtime = tokio::runtime::Builder::new_multi_thread().worker_threads(policy.handlers.min(8))
        .max_blocking_threads(policy.handlers).enable_all().build().map_err(io_fault)?;
    let mut actor_tasks = JoinSet::new();
    let actors = runtime.block_on(actors::start(options,&parent_promises,&template,&policy,&mut actor_tasks))?;
    let listener = std::net::TcpListener::bind(address).map_err(io_fault)?;
    listener.set_nonblocking(true).map_err(io_fault)?;
    let actual = listener.local_addr().map_err(io_fault)?;
    if let Some(progress) = host.server_progress.as_mut() {
        progress(&serde_json::json!({"version":1,"event":"ready","address":actual.to_string()}))?;
    }
    let count = Arc::new(AtomicU64::new(0));
    let failures = Arc::new(AtomicU64::new(0));
    let result = runtime.block_on(async {
        let listener = tokio::net::TcpListener::from_std(listener).map_err(io_fault)?;
        let capacity = Arc::new(Semaphore::new(policy.handlers));
        let mut connections = JoinSet::new();
        let mut workers = JoinSet::new();
        let (work_tx, mut work_rx) = tokio::sync::mpsc::channel::<(Vm, EffectHost, Instant, tokio::sync::oneshot::Sender<Result<(Value, tokio::sync::OwnedSemaphorePermit), String>>, tokio::sync::OwnedSemaphorePermit)>(policy.handlers);
        loop {
            if cancelled(&template) || max_requests.is_some_and(|max| count.load(Ordering::Relaxed) >= max) { break; }
            tokio::select! {
                Some((machine, worker, deadline, reply, permit)) = work_rx.recv() => {
                    let actors = actors.clone();
                    workers.spawn(async move { let result = execute(machine, worker, deadline, Some(actors), None).await; let _ = reply.send(result.map(|value| (value,permit))); });
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
                                let read_roots = worker.read_roots.clone();
                                let machine = match invoke(handler, vec![request], &promises) { Ok(machine) => machine, Err(_) => return Ok(response(500,"handler failed")) };
                                let (reply, receive) = tokio::sync::oneshot::channel();
                                if tx.send((machine,worker,deadline,reply,permit)).await.is_err() { return Ok(response(503,"server stopping")) }
                                let result = match tokio::time::timeout_at(deadline,receive).await {
                                    Err(_) => response(504,"deadline exceeded"),
                                    Ok(Ok(Ok((value,permit)))) => match tokio::time::timeout_at(deadline,tokio::task::spawn_blocking(move || { let _permit = permit; checked_response(value,&policy,&read_roots,head) })).await {
                                        Ok(Ok(Ok(response))) => response,
                                        Err(_) => response(504,"deadline exceeded"),
                                        _ => { failures.fetch_add(1,Ordering::Relaxed); response(500,"invalid response") },
                                    },
                                    _ => { failures.fetch_add(1,Ordering::Relaxed); response(500,"handler failed") }
                                };
                                Ok(result)
                            }
                        });
                        let mut builder = http1::Builder::new();
                        let policy = connection_policy;
                        builder.keep_alive(false).half_close(false).max_headers(128).max_buf_size(policy.headers)
                            .max_header_size(policy.headers).timer(TokioTimer::new()).header_read_timeout(Duration::from_millis(policy.deadline_ms));
                        let _ = tokio::time::timeout(Duration::from_millis(policy.deadline_ms + 100), builder.serve_connection(TokioIo::new(stream), service)).await;
                    });
                }
                _ = tokio::time::sleep(Duration::from_millis(10)) => {},
            }
        }
        drop(listener);
        // Accepted work retains capacity until blocking effects settle, even after a timeout.
        while !connections.is_empty() || !workers.is_empty() || !work_rx.is_empty() || capacity.available_permits() != policy.handlers {
            tokio::select! {
                Some((machine,worker,deadline,reply,permit)) = work_rx.recv(), if !work_rx.is_empty() => {
                    let actors = actors.clone();
                    workers.spawn(async move { let _ = reply.send(execute(machine,worker,deadline,Some(actors),None).await.map(|value| (value,permit))); });
                }
                Some(_) = connections.join_next(), if !connections.is_empty() => {},
                Some(_) = workers.join_next(), if !workers.is_empty() => {},
                _ = tokio::time::sleep(Duration::from_millis(10)) => {},
            }
        }
        actors.stopping.store(true,Ordering::Relaxed);
        while actor_tasks.join_next().await.is_some() {}
        Ok::<_, Failure>(())
    });
    result?;
    let counts = serde_json::json!({"version":1,"event":"shutdown","address":actual.to_string(),"requests":count.load(Ordering::Relaxed),"failures":failures.load(Ordering::Relaxed)});
    if let Some(progress) = host.server_progress.as_mut() { progress(&counts)?; }
    Ok(object([("address",Yaml::String(actual.to_string())),("requests",Yaml::Integer(count.load(Ordering::Relaxed) as i64)),("failures",Yaml::Integer(failures.load(Ordering::Relaxed) as i64))]))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn reversible_request_invocation_preserves_synthesized_arguments() {
        let mut source = Vm::load(r#"
http: !call [import, 'std:http']
actor: !call [import, 'std:actor']
json: !call [import, 'std:json']
sqlite: !call [import, 'std:sqlite']
handler: !call [fn, [request], {status: 200, headers: [], body: pure}]
"#);
        source.run();
        let Value::Hash(fields) = source.result.as_ref().unwrap() else { panic!("missing source result") };
        let Value::Function(function) = fields.get(&s("handler")).unwrap() else { panic!("missing handler") };
        let promise = Value::from_yaml(&yaml_rust2::YamlLoader::load_from_str("{type: promise, name: reversible, requires: [reversible]}").unwrap().remove(0));
        let mut worker = invoke(function.clone(),vec![Value::Null],&[promise]).unwrap();
        worker.run();
        assert_eq!(worker.status(),Status::Finished,"{}",worker.state_string());
    }
}

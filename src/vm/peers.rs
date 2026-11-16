//! Coordinated reversible communication. Only the complete system is a restore boundary.
use super::{Status, Vm};
use std::{collections::{BTreeMap, BTreeSet}, sync::Arc};
use yaml_rust2::Yaml;
use serde_json::{Value as J, json};

const MAX_PARTICIPANTS: usize = 128;
const MAX_DELIVERIES: usize = 100_000;
const MAX_BYTES: usize = 32 * 1024 * 1024;

/// Immutable participant versions and their committed message deliveries.
#[derive(Clone, Debug)]
pub struct PromisedSystem {
    machines: BTreeMap<String, Vm>,
    deliveries: Vec<J>,
    cursor: usize,
}
impl PromisedSystem {
    pub fn new(machines: BTreeMap<String, Vm>) -> Result<Self, String> {
        validate_machines(&machines)?;
        let result = Self { machines, deliveries: Vec::new(), cursor: 0 };
        result.validate_deliveries()?;
        result.snapshot()?;
        Ok(result)
    }
    pub fn machine(&self, name: &str) -> Option<&Vm> { self.machines.get(name) }
    pub fn finished(&self) -> bool { self.machines.values().all(|vm| vm.status() == Status::Finished) }
    pub fn state(&self) -> Yaml {
        let mut machines = yaml_rust2::yaml::Hash::new();
        for (name, vm) in &self.machines { machines.insert(Yaml::String(name.clone()), vm.state()); }
        let mut fields = yaml_rust2::yaml::Hash::new();
        let status = if self.finished() { "finished" }
            else if self.machines.values().any(|vm| vm.status() == Status::Error) { "error" }
            else if self.machines.values().any(|vm| matches!(vm.status(), Status::Ready | Status::Running)) { "running" }
            else if self.rendezvous().is_some() { "running" }
            else { "blocked" };
        fields.insert(Yaml::String("status".into()), Yaml::String(status.into()));
        fields.insert(Yaml::String("machines".into()), Yaml::Hash(machines));
        fields.insert(Yaml::String("delivery_count".into()), Yaml::Integer(self.deliveries.len() as i64));
        Yaml::Hash(fields)
    }
    /// One deterministic transition; None means finished or blocked, never host dispatch.
    pub fn advance(&self) -> Result<Option<Self>, String> {
        let names = self.machines.keys().collect::<Vec<_>>();
        for offset in 0..names.len() {
            let name = names[(self.cursor + offset) % names.len()];
            let vm = &self.machines[name];
            if matches!(vm.status(), Status::Ready | Status::Running) { return self.step(name).map(Some); }
            if let Some((sender, send_id, receiver, receive_id)) = self.rendezvous_from(name) {
                return self.deliver(sender, send_id, receiver, receive_id).map(Some);
            }
        }
        Ok(None)
    }
    fn rendezvous(&self) -> Option<(&str, u64, &str, u64)> {
        self.machines.keys().find_map(|name| self.rendezvous_from(name))
    }
    fn rendezvous_from<'a>(&'a self, name: &'a str) -> Option<(&'a str, u64, &'a str, u64)> {
        let vm = self.machines.get(name)?;
        if vm.status() != Status::Signaled || vm.is_interrupted() { return None; }
        let send = vm.pending_effect().filter(|r| r.operation == "peer_send")?;
        let target = send.payload["to"].as_str()?;
        let peer = self.machines.get(target)?;
        if peer.status() != Status::Signaled || peer.is_interrupted() { return None; }
        let receive = peer.pending_effect()?;
        (receive.operation == "peer_receive" && receive.payload["from"].as_str() == Some(name))
            .then_some((name, send.id, target, receive.id))
    }
    pub fn delivery_count(&self) -> usize { self.deliveries.len() }
    /// Advance one participant without exposing any host authority.
    pub fn step(&self, name: &str) -> Result<Self, String> {
        let vm = self.machines.get(name).ok_or("unknown participant")?;
        self.replace(name, vm.step())
    }
    pub fn signal(&self, name: &str, event: Yaml) -> Result<Self, String> {
        let vm = self.machines.get(name).ok_or("unknown participant")?;
        self.replace(name, vm.signal(event))
    }
    pub fn resume_signal(&self, name: &str) -> Result<Self, String> {
        let vm = self.machines.get(name).ok_or("unknown participant")?;
        self.replace(name, vm.resume_signal())
    }
    fn replace(&self, name: &str, vm: Vm) -> Result<Self, String> {
        let mut next = self.clone();
        next.machines.insert(name.into(), vm);
        next.cursor = (self.machines.keys().position(|n| n == name).ok_or("unknown participant")? + 1) % self.machines.len();
        next.snapshot()?;
        Ok(next)
    }
    /// Commit a rendezvous exactly once within this retained system history.
    /// Expected request IDs make stale or duplicated delivery attempts explicit.
    pub fn deliver(&self, sender: &str, send_id: u64, receiver: &str, receive_id: u64) -> Result<Self, String> {
        if sender == receiver { return Err("a rendezvous requires distinct participants".into()); }
        let receipt = json!({"sender":sender,"send_id":send_id,"receiver":receiver,"receive_id":receive_id});
        if self.deliveries.contains(&receipt) { return Ok(self.clone()); }
        if self.deliveries.len() >= MAX_DELIVERIES { return Err("delivery history exceeds limit".into()); }
        let send = self.machines.get(sender).ok_or("unknown sender")?;
        let receive = self.machines.get(receiver).ok_or("unknown receiver")?;
        if send.status() != Status::Signaled || receive.status() != Status::Signaled
            || send.is_interrupted() || receive.is_interrupted() {
            return Err("both participants must be waiting for communication".into());
        }
        let request = send.pending_effect().ok_or("sender has no request")?;
        let target = receive.pending_effect().ok_or("receiver has no request")?;
        if request.id != send_id || target.id != receive_id
            || request.operation != "peer_send" || target.operation != "peer_receive"
            || request.payload["to"].as_str() != Some(receiver)
            || target.payload["from"].as_str() != Some(sender)
            || request.payload.as_hash().is_none_or(|h| h.len() != 2)
            || target.payload.as_hash().is_none_or(|h| h.len() != 1)
            || matches!(request.payload["value"], Yaml::BadValue) {
            return Err("rendezvous requests do not match".into());
        }
        let mut next = self.clone();
        next.machines.insert(sender.into(), resolve(send, Yaml::Null)?);
        next.machines.insert(receiver.into(), resolve(receive, request.payload["value"].clone())?);
        next.deliveries.push(receipt);
        next.cursor = (self.machines.keys().position(|n| n == receiver).unwrap() + 1) % self.machines.len();
        next.validate_deliveries()?;
        next.snapshot()?;
        Ok(next)
    }
    fn validate_deliveries(&self) -> Result<(), String> {
        if self.deliveries.len() > MAX_DELIVERIES { return Err("delivery history exceeds limit".into()); }
        let mut accounted = BTreeSet::new();
        for receipt in &self.deliveries {
            let sender = receipt["sender"].as_str().ok_or("invalid sender")?;
            let receiver = receipt["receiver"].as_str().ok_or("invalid receiver")?;
            if !accounted.insert((sender.to_owned(), receipt["send_id"].as_u64().ok_or("invalid send id")?))
                || !accounted.insert((receiver.to_owned(), receipt["receive_id"].as_u64().ok_or("invalid receive id")?)) {
                return Err("conflicting delivery receipt".into());
            }
            let send = record(&self.machines[sender], receipt["send_id"].as_u64().ok_or("invalid send id")?)?;
            let receive = record(&self.machines[receiver], receipt["receive_id"].as_u64().ok_or("invalid receive id")?)?;
            if send["operation"].as_str() != Some("peer_send") || receive["operation"].as_str() != Some("peer_receive")
                || send["payload"]["to"].as_str() != Some(receiver) || receive["payload"]["from"].as_str() != Some(sender)
                || send["payload"].as_hash().is_none_or(|h| h.len()!=2)
                || receive["payload"].as_hash().is_none_or(|h| h.len()!=1)
                || send["status"].as_str() != Some("finished") || receive["status"].as_str() != Some("finished")
                || send["outcome"] != Yaml::Null
                || !crate::value::from_host_yaml(&send["payload"]["value"])?
                    .same_view(&crate::value::from_host_yaml(&receive["outcome"])?) {
                return Err("delivery receipt disagrees with participant outcomes".into());
            }
        }
        for (name, vm) in &self.machines {
            if let Some(evaluator) = &vm.evaluator {
                for entry in &evaluator.effects {
                    let row = entry.plain_yaml().ok_or("invalid effect history")?;
                    if matches!(row["operation"].as_str(), Some("peer_send" | "peer_receive")) {
                        let id = row["id"].as_i64().filter(|id| *id > 0).ok_or("invalid effect id")? as u64;
                        if !accounted.contains(&(name.clone(), id)) { return Err("peer effect lacks coordinated delivery receipt".into()); }
                    }
                }
            }
        }
        Ok(())
    }
    pub fn snapshot(&self) -> Result<String, String> {
        let mut machines = serde_json::Map::new();
        for (name, vm) in &self.machines {
            machines.insert(name.clone(), vm.snapshot_tree()?);
        }
        let text = serde_json::to_string_pretty(&json!({"format":"helix-promised-system", "version":1,
            "machines":machines,"deliveries":self.deliveries,"cursor":self.cursor})).map_err(|e| e.to_string())?;
        if text.len() >= MAX_BYTES { return Err("promised system exceeds snapshot byte budget".into()); }
        let _: J = serde_json::from_str(&text).map_err(|_| "promised system exceeds JSON nesting budget")?;
        Ok(text + "\n")
    }
    pub fn restore(text: &str) -> Result<Self, String> {
        if text.len() > MAX_BYTES { return Err("promised system exceeds snapshot byte budget".into()); }
        let document: J = serde_json::from_str(text).map_err(|e| e.to_string())?;
        if document.as_object().is_none_or(|o| o.len() != 5)
            || document["format"] != "helix-promised-system" || document["version"] != 1 {
            return Err("unsupported promised system format".into());
        }
        let mut machines = BTreeMap::new();
        let saved_machines = document["machines"].as_object().ok_or("expected participant map")?;
        if saved_machines.is_empty() || saved_machines.len() > MAX_PARTICIPANTS { return Err("invalid participant count".into()); }
        for (name, saved) in saved_machines {
            machines.insert(name.clone(), Vm::restore_tree(saved.clone())?);
        }
        validate_machines(&machines)?;
        let mut system = Self { machines, deliveries: Vec::new(), cursor: 0 };
        system.cursor = document["cursor"].as_u64().and_then(|n| usize::try_from(n).ok()).filter(|n| *n < system.machines.len()).ok_or("invalid scheduler cursor")?;
        if document["deliveries"].as_array().is_none_or(|rows| rows.len()>MAX_DELIVERIES) {
            return Err("invalid delivery list or history limit".into());
        }
        for receipt in document["deliveries"].as_array().ok_or("expected delivery list")? {
            if receipt.as_object().is_none_or(|o| o.len()!=4) { return Err("invalid delivery receipt".into()); }
            let sender = receipt["sender"].as_str().ok_or("invalid sender")?;
            let receiver = receipt["receiver"].as_str().ok_or("invalid receiver")?;
            let _send_id = receipt["send_id"].as_u64().filter(|n| *n>0).ok_or("invalid send id")?;
            let _receive_id = receipt["receive_id"].as_u64().filter(|n| *n>0).ok_or("invalid receive id")?;
            if sender == receiver || !system.machines.contains_key(sender) || !system.machines.contains_key(receiver)
 {
                return Err("conflicting delivery receipt".into());
            }
            system.deliveries.push(receipt.clone());
        }
        system.validate_deliveries()?;
        system.snapshot()?;
        Ok(system)
    }
}
fn resolve(vm: &Vm, value: Yaml) -> Result<Vm, String> {
    let mut result = vm.clone();
    Arc::make_mut(result.evaluator.as_mut().ok_or("missing evaluator")?).resolve_effect(Ok(value));
    result.status = Status::Running;
    result.verify_promises()?;
    Ok(result)
}

fn record(vm: &Vm, id: u64) -> Result<Yaml, String> {
    let index = usize::try_from(id).ok().and_then(|id| id.checked_sub(1)).ok_or("invalid delivery request id")?;
    let record = vm.evaluator.as_ref().and_then(|e| e.effects.get(index)).and_then(|v| v.plain_yaml())
        .ok_or("delivery receipt has no completed request")?;
    if record["id"].as_i64().and_then(|id| u64::try_from(id).ok()) != Some(id) {
        return Err("delivery request identity mismatch".into());
    }
    Ok(record)
}

fn validate_machines(machines: &BTreeMap<String, Vm>) -> Result<(), String> {
        if machines.len() > MAX_PARTICIPANTS || machines.is_empty() || machines.keys().any(|name| name.is_empty()) {
            return Err("a promised system needs named participants".into());
        }
        for vm in machines.values() {
            if vm.promises().is_empty() { return Err("every participant must carry a promise".into()); }
            vm.verify_promises()?;
        }
    Ok(())
}

//! Immutable machine values and the iterative nested-machine scheduler.
use crate::model::HelixError;
use crate::eval::{Evaluator, Function};
use crate::graph::{Graph, Kind, ROOT};
use crate::{emit, value::Value};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use yaml_rust2::Yaml;

pub use crate::model::{EffectRequest, Status, Vm};
#[path = "vm/effects.rs"]
pub mod effects;
pub mod peers;
impl Status {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Ready => "ready",
            Self::Running => "running",
            Self::Finished => "finished",
            Self::Error => "error",
            Self::Signaled => "signaled",
        }
    }
}
impl Vm {
    /// Attach a validated, composable behavioral promise to this immutable version.
    pub(crate) fn with_promise_value(&self, promise: Value) -> Result<Self, String> {
        crate::builtins::typing::promise::validate(&promise)?;
        let mut next = self.clone();
        if !next.promises.iter().any(|old| old.same_view(&promise)) { next.promises.push(promise); }
        next.verify_promises()?;
        Ok(next)
    }
    pub fn with_promise(&self, promise: Yaml) -> Result<Self, String> {
        self.with_promise_value(crate::value::from_host_yaml(&promise)?)
    }
    pub(crate) fn promises(&self) -> &[Value] { &self.promises }

    pub fn load(src: &str) -> Self {
        Self::load_from(src, None, None)
    }
    pub fn load_file(src: &str, file: &Path) -> Self {
        Self::load_from(src, Some(file), None)
    }
    pub fn load_sandboxed(src: &str, file: Option<&Path>, roots: &[PathBuf]) -> Self {
        Self::load_from(src, file, Some(roots))
    }
    fn load_from(src: &str, file: Option<&Path>, roots: Option<&[PathBuf]>) -> Self {
        let mut vm = Self {
            promises: Vec::new(),
            evaluator: None,
            source_file: file.map(|p| p.canonicalize().unwrap_or_else(|_| p.to_path_buf())),
            status: Status::Ready,
            result: None,
            error: None,
            checking: false,
            errors: Vec::new(),
        };
        match Graph::parse(src).and_then(|graph| Evaluator::new(graph, file, roots)) {
            Ok(e) => vm.evaluator = Some(Arc::new(e)),
            Err(e) => vm.fail(e),
        }
        vm
    }
    pub(crate) fn from_function(function: Arc<Function>) -> Self {
        Self {
            promises: Vec::new(),
            evaluator: Some(Arc::new(Evaluator::child(function))),
            source_file: None,
            status: Status::Ready,
            result: None,
            error: None,
            checking: false,
            errors: Vec::new(),
        }
    }
    /// Invoke a completed function value with ordinary host data in fresh execution state.
    pub fn call(&self, arguments: &[Yaml]) -> Result<Self, String> {
        let Some(Value::Function(function)) = &self.result else {
            return Err("completed result must be a function".into());
        };
        let arguments = arguments.iter().map(crate::value::from_host_yaml).collect::<Result<Vec<_>, _>>()?;
        let mut next = Self::from_function(function.clone());
        next.evaluator = Some(Arc::new(Evaluator::invoke(function.clone(), arguments)));
        Ok(next)
    }

    pub fn status(&self) -> Status {
        self.status
    }
    /// Advance one transition. The previous version remains an independent machine value.
    pub fn step(&self) -> Self {
        let mut next = self.clone();
        next.advance();
        next
    }
    fn terminal(&self) -> bool {
        !matches!(self.status, Status::Ready | Status::Running)
    }
    fn promise_failure(&mut self) {
        let graph = self.evaluator.as_ref().map(|e| &e.program.graph);
        if let Some(graph) = graph {
            self.fail(graph.error(ROOT, "promise", "operation violates reversible state bounds or host-effect policy".into()));
        }
    }
    pub(crate) fn verify_promises(&self) -> Result<(), String> {
        if self.promises.is_empty() { return Ok(()); }
        if let Some(evaluator) = &self.evaluator {
            for record in &evaluator.effects {
                let Value::Hash(fields) = record else { return Err("invalid effect history".into()) };
                let Some(Value::String(operation)) = fields.get(&crate::model::string("operation")) else {
                    return Err("invalid effect history operation".into());
                };
                if !self.allows_effect(operation) { return Err("effect history violates promise".into()); }
            }
            if let Some(request) = self.pending_effect() {
                if !self.allows_effect(&request.operation) { return Err("pending effect violates promise".into()); }
            }
        }
        if !self.promises.iter().any(crate::builtins::typing::promise::reversible) { return Ok(()); }
        self.snapshot()?;
        // Reserve enough representable state to report refusal without discarding
        // the last complete continuation or creating an unserializable error.
        let mut refused = self.clone();
        refused.promise_failure();
        refused.snapshot()?;
        Ok(())
    }
    pub(crate) fn allows_effect(&self, operation: &str) -> bool {
        self.promises.iter().all(|promise| crate::builtins::typing::promise::allows(promise, operation))
    }
    pub(crate) fn can_dispatch_effect(&self, operation: &str) -> bool {
        self.allows_effect(operation) && !self.promises.iter().any(crate::builtins::typing::promise::reversible)
    }
    fn enforce_promises(&mut self, previous: Option<Self>) {
        if let Some(previous) = previous {
          if self.verify_promises().is_err() {
            *self = previous;
            self.promise_failure();
          }
        }
    }
    fn advance(&mut self) {
        if self.terminal() {
            return;
        }
        // Descend through child requests using a host vector, never nested interpreter calls.
        let mut parents = Vec::new();
        loop {
            if let Err(message) = crate::snapshot::validate::next(self.evaluator.as_ref().unwrap()) {
                self.fail(self.evaluator.as_ref().unwrap().program.graph.error(ROOT, "snapshot", message));
                break;
            }
            let rollback = (!self.promises.is_empty()).then(|| self.clone());
            let child = Arc::make_mut(
                self.evaluator
                    .as_mut()
                    .expect("an active machine has an evaluator"),
            )
            .take_child();
            if let Some(mut child) = child {
                let parent_state = self.evaluator.as_ref().unwrap();
                let previous = child.evaluator.as_ref().map(|e| (e.inherited_type_time, e.effects_allowed));
                if let Some(evaluator) = &mut child.evaluator {
                    let evaluator = Arc::make_mut(evaluator);
                    evaluator.inherited_type_time |= parent_state.type_time || parent_state.inherited_type_time;
                    evaluator.effects_allowed &= parent_state.effects_allowed;
                }
                self.status = Status::Running;
                parents.push((std::mem::replace(self, child), previous, rollback));
                if self.terminal() {
                    break;
                }
            } else {
                self.status = Status::Running;
                let evaluator = Arc::make_mut(self.evaluator.as_mut().unwrap());
                evaluator.transition();
                if evaluator.pending_effect.is_some() {
                    self.status = Status::Signaled;
                }
                if let Some(outcome) = evaluator.outcome().cloned() {
                    match outcome {
                        Ok(value) => {
                            self.result = Some(value);
                            self.status = Status::Finished
                        }
                        Err(error) => self.fail(error),
                    }
                }
                self.enforce_promises(rollback);
                break;
            }
        }
        while let Some((mut parent, previous, rollback)) = parents.pop() {
            if let (Some(evaluator), Some((inherited_type_time, effects_allowed))) = (&mut self.evaluator, previous) {
                let evaluator = Arc::make_mut(evaluator);
                evaluator.inherited_type_time = inherited_type_time;
                evaluator.effects_allowed = effects_allowed;
            }
            Arc::make_mut(parent.evaluator.as_mut().unwrap()).return_child(self.clone());
            parent.enforce_promises(rollback);
            *self = parent;
        }
    }
    /// Yielded requests have no authority until a host explicitly handles them.
    pub fn pending_effect(&self) -> Option<&EffectRequest> {
        self.evaluator.as_ref()?.pending_effect.as_ref().map(|(_, request)| request)
    }
    /// Resolve only the current request; stepping or running a completed version never repeats it.
    pub fn resolve_effect(&self, result: Result<Yaml, (String, String)>) -> Self {
        let mut next = self.clone();
        if self.pending_effect().is_some_and(|request| !self.can_dispatch_effect(&request.operation)) {
            next.promise_failure();
            return next;
        }
        if next.status == Status::Signaled && next.pending_effect().is_some() {
            Arc::make_mut(next.evaluator.as_mut().unwrap()).resolve_effect(result);
            next.status = if next.evaluator.as_ref().unwrap().signal.is_some() { Status::Signaled } else { Status::Running };
        }
        next.enforce_promises((!self.promises.is_empty()).then(|| self.clone()));
        next
    }
    pub fn is_interrupted(&self) -> bool {
        self.evaluator.as_ref().is_some_and(|e| e.signal.is_some())
    }
    /// Interrupt an active machine without discarding its continuation or pending request.
    pub fn signal(&self, event: Yaml) -> Self {
        let mut next = self.clone();
        if matches!(next.status, Status::Ready | Status::Running | Status::Signaled) {
            match crate::value::from_host_yaml(&event) {
                Ok(event) => {
                    Arc::make_mut(next.evaluator.as_mut().unwrap()).signal = Some(event);
                    next.status = Status::Signaled;
                }
                Err(message) => next.fail(next.evaluator.as_ref().unwrap().program.graph.error(ROOT, "type", message)),
            }
        }
        next.enforce_promises((!self.promises.is_empty()).then(|| self.clone()));
        next
    }
    pub fn resume_signal(&self) -> Self {
        let mut next = self.clone();
        if next.status == Status::Signaled {
            let evaluator = Arc::make_mut(next.evaluator.as_mut().unwrap());
            evaluator.signal = None;
            if evaluator.pending_effect.is_none() { next.status = Status::Running; }
        }
        next.enforce_promises((!self.promises.is_empty()).then(|| self.clone()));
        next
    }
    pub fn run(&mut self) {
        while !self.terminal() {
            self.advance();
        }
    }
    /// Evaluate the program collecting independent errors instead of stopping at the first.
    /// This does not enter type time or change import policy. A clean program ends `finished`.
    pub fn check(&mut self) {
        if !self.promises.is_empty() {
            self.promise_failure();
            return;
        }
        if self.status != Status::Ready {
            if self.evaluator.is_none() {
                self.checking = true
            }
            return;
        }
        self.checking = true;
        // Collection and final root assembly use ordinary evaluation throughout.
        // Retaining the load-time queue would run preflight only when collection succeeds.
        let mut evaluator = Arc::new(self.evaluator.as_ref().unwrap().for_node(ROOT));
        for node in evaluator.check_nodes() {
            let mut machine = Self {
                promises: Vec::new(),
            evaluator: Some(Arc::new(evaluator.for_node(node))),
                source_file: self.source_file.clone(),
                status: Status::Ready,
                result: None,
                error: None,
                checking: false,
                errors: Vec::new(),
            };
            machine.run();
            if let Some(runner) = &machine.evaluator {
                let state = Arc::make_mut(&mut evaluator);
                state.remaining_work = runner.remaining_work;
                state.remaining_values = runner.remaining_values;
                state.scopes = runner.scopes;
                state.type_inputs = runner.type_inputs.clone();
                state.annotations = runner.annotations.clone();
                state.annotation_units = runner.annotation_units;
                state.annotations_truncated = runner.annotations_truncated;
                state.memo.extend(runner.memo.clone());
            }
            if let Some(error) = machine.error.take() {
                if !self.errors.iter().any(|e| {
                    e.path == error.path
                        && e.kind == error.kind
                        && e.message == error.message
                        && e.phase == error.phase
                        && e.file == error.file
                        // A shared rule can fail for distinct values. Preserve their
                        // exact data, including integer/float representations, while
                        // still coalescing the same failed dependency.
                        && match (&e.data, &error.data) {
                            (None, None) => true,
                            (Some(a), Some(b)) => a.same_view(b),
                            _ => false,
                        }
                }) {
                    self.errors.push(error)
                }
            }
        }
        self.evaluator = Some(evaluator);
        if self.errors.is_empty() {
            self.run()
        } else {
            self.status = Status::Error
        }
    }
    /// Check states carry `mode: check` and preserve each error's own evaluation phase.
    /// Run states carry a single error and, when present, its phase at the top level too.
    pub fn state(&self) -> Yaml {
        use crate::model::string as s;
        use crate::value::Hash;
        let mut fields = Hash::new();
        fields.insert(s("status"), s(self.status.as_str()));
        if let Some(result) = &self.result {
            fields.insert(s("root"), result.clone());
        }
        self.effect_fields(&mut fields);
        if self.checking {
            fields.insert(s("mode"), s("check"));
            let errors = if self.errors.is_empty() {
                self.error.iter().collect::<Vec<_>>()
            } else {
                self.errors.iter().collect()
            };
            if !errors.is_empty() {
                fields.insert(
                    s("errors"),
                    Value::Array(errors.into_iter().map(HelixError::to_value).collect()),
                );
            }
        } else if let Some(error) = &self.error {
            if let Some(phase) = error.phase {
                fields.insert(s("phase"), s(phase));
            }
            fields.insert(s("error"), error.to_value());
        }
        crate::inspect::state(&Value::Hash(fields))
            .plain_yaml()
            .expect("state inspection contains only data")
    }
    /// A deterministic view; this is not a restoration or snapshot compatibility format.
    pub fn inspect(&self) -> Yaml {
        crate::inspect::machine(self)
            .plain_yaml()
            .expect("machine inspection is data")
    }
    /// Latest completed value at each producing source location. Repeated local
    /// calls replace earlier evidence; this bounded view is not an execution trace.
    /// Function and machine values are summarized without retaining live captures.
    pub fn evaluated_values(&self) -> Yaml {
        use crate::model::string as s;
        let (values, truncated) = self.evaluator.as_ref().map(|e|
            (e.evaluated.clone(), e.evaluated_truncated)).unwrap_or_default();
        Value::Hash([
            (s("values"), Value::Array(values)),
            (s("truncated"), Value::Boolean(truncated)),
        ].into_iter().collect()).plain_yaml().expect("evaluated evidence is ordinary data")
    }

    fn effect_fields(&self, fields: &mut crate::value::Hash) {
        use crate::model::string as s;
        if let Some(evaluator) = &self.evaluator {
            if let Some((site, request)) = &evaluator.pending_effect {
                fields.insert(s("pending_effect"), evaluator.effect_view(*site, request));
            }
            if !evaluator.effects.is_empty() {
                fields.insert(s("effects"), Value::Array(evaluator.effects.clone().into()));
            }
            if !evaluator.type_inputs.is_empty() {
                fields.insert(s("type_time_inputs"), evaluator.type_input_names());
            }
            if !evaluator.annotations.is_empty() {
                fields.insert(s("annotations"), Value::Array(evaluator.annotations.clone().into()));
            }
            if evaluator.annotations_truncated {
                fields.insert(s("annotations_truncated"), Value::Boolean(true));
            }
            if let Some(event) = &evaluator.signal {
                fields.insert(s("signal"), event.clone());
            }
        }
    }
    pub(crate) fn inspection(&self) -> Value {
        let mut fields = crate::value::Hash::new();
        if !self.promises.is_empty() {
            fields.insert(crate::model::string("promises"), Value::Array(self.promises.clone().into()));
        }
        fields.insert(
            crate::model::string("status"),
            crate::model::string(self.status.as_str()),
        );
        if let Some(evaluator) = &self.evaluator {
            if let Value::Hash(state) = evaluator.inspection() {
                fields.extend(state)
            }
        }
        self.effect_fields(&mut fields);
        if let Some(evaluator) = self.evaluator.as_ref().filter(|e| !e.type_inputs.is_empty()) {
            fields.insert(crate::model::string("type_time_inputs"), Value::Array(evaluator.type_inputs.clone().into()));
        }
        fields.insert(crate::model::string("checking"), Value::Boolean(self.checking));
        fields.insert(
            crate::model::string("errors"),
            Value::Array(self.errors.iter().map(HelixError::to_value).collect()),
        );
        fields.insert(
            crate::model::string("source_file"),
            self.source_file
                .as_ref()
                .map(|p| crate::model::string(&p.to_string_lossy()))
                .unwrap_or(Value::Null),
        );
        fields.insert(
            crate::model::string("outcome"),
            self.result
                .clone()
                .or_else(|| self.error.as_ref().map(HelixError::to_value))
                .unwrap_or(Value::Null),
        );
        Value::Hash(fields)
    }
    pub(crate) fn logical_eq(&self, other: &Self) -> bool {
        std::ptr::eq(self, other) || self == other
    }
    pub fn state_string(&self) -> String {
        emit::document(&self.state())
    }
    pub fn recovery_state_string(&self) -> String {
        let mut state = self.state();
        if self.status == Status::Error && self.evaluator.as_ref().is_none_or(|e| e.effects.is_empty()) {
            if let (Some(evaluator), Yaml::Hash(fields)) = (&self.evaluator, &mut state) {
                fields.insert(text("recovery"), text(&emit::recovery(&evaluator.program.graph, &evaluator.memo)));
                if let Some(file) = &self.source_file {
                    fields.insert(text("recovery_file"), text(&file.to_string_lossy()));
                }
            }
        }
        emit::document(&state)
    }
    pub fn resume(src: &str) -> Self {
        if let Ok(graph) = Graph::parse(src) {
            if let Kind::Map(entries) = graph.kind(ROOT) {
                let field = |name: &str| {
                    entries
                        .iter()
                        .find(|(key, _)| key == name)
                        .and_then(|(_, id)| match graph.kind(*id) {
                            Kind::Scalar {
                                value: Yaml::String(value),
                                ..
                            } => Some(value.as_str()),
                            _ => None,
                        })
                };
                if field("status") == Some("error") {
                    if let Some(source) = field("recovery") {
                        return Self::load_from(
                            source,
                            field("recovery_file").map(Path::new),
                            None,
                        );
                    }
                }
            }
        }
        let mut vm = Self::load("null");
        let graph = Graph::parse("null").unwrap();
        vm.fail(graph.error(
            ROOT,
            "recovery",
            "expected an error state with string recovery source".into(),
        ));
        vm.evaluator = None;
        vm
    }
    fn fail(&mut self, error: HelixError) {
        self.status = Status::Error;
        self.error = Some(error)
    }
}
fn text(s: &str) -> Yaml {
    Yaml::String(s.into())
}

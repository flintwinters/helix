//! Immutable machine values and the iterative nested-machine scheduler.
use crate::error::HelixError;
use crate::eval::{Evaluator, Function};
use crate::graph::{Graph, Kind, ROOT};
use crate::{emit, value::Value};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use yaml_rust2::Yaml;

pub use crate::model::{Status, Vm};
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
            evaluator: Some(Arc::new(Evaluator::child(function))),
            source_file: None,
            status: Status::Ready,
            result: None,
            error: None,
            checking: false,
            errors: Vec::new(),
        }
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
    fn advance(&mut self) {
        if self.terminal() {
            return;
        }
        // Descend through child requests using a host vector, never nested interpreter calls.
        let mut parents = Vec::new();
        loop {
            let child = Arc::make_mut(
                self.evaluator
                    .as_mut()
                    .expect("an active machine has an evaluator"),
            )
            .take_child();
            if let Some(child) = child {
                self.status = Status::Running;
                parents.push(std::mem::replace(self, child));
                if self.terminal() {
                    break;
                }
            } else {
                self.status = Status::Running;
                let evaluator = Arc::make_mut(self.evaluator.as_mut().unwrap());
                evaluator.transition();
                if let Some(outcome) = evaluator.outcome().cloned() {
                    match outcome {
                        Ok(value) => {
                            self.result = Some(value);
                            self.status = Status::Finished
                        }
                        Err(error) => self.fail(error),
                    }
                }
                break;
            }
        }
        while let Some(mut parent) = parents.pop() {
            Arc::make_mut(parent.evaluator.as_mut().unwrap()).return_child(self.clone());
            *self = parent;
        }
    }
    pub fn run(&mut self) {
        while !self.terminal() {
            self.advance();
        }
    }
    pub fn check(&mut self) {
        if self.status != Status::Ready {
            if self.evaluator.is_none() {
                self.checking = true
            }
            return;
        }
        self.checking = true;
        let mut evaluator = self.evaluator.as_ref().unwrap().clone();
        for node in evaluator.check_nodes() {
            let mut machine = Self {
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
                state.memo.extend(runner.memo.clone());
            }
            if let Some(error) = machine.error {
                if !self.errors.iter().any(|e| {
                    e.path == error.path
                        && e.kind == error.kind
                        && e.message == error.message
                        && e.phase == error.phase
                        && e.file == error.file
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
    pub fn state(&self) -> Yaml {
        use crate::error::s;
        use crate::value::Hash;
        let mut fields = Hash::new();
        fields.insert(s("status"), s(self.status.as_str()));
        if let Some(result) = &self.result {
            fields.insert(s("root"), result.clone());
        }
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
    pub(crate) fn inspection(&self) -> Value {
        let mut fields = crate::value::Hash::new();
        fields.insert(
            crate::error::s("status"),
            crate::error::s(self.status.as_str()),
        );
        if let Some(evaluator) = &self.evaluator {
            if let Value::Hash(state) = evaluator.inspection() {
                fields.extend(state)
            }
        }
        fields.insert(crate::error::s("checking"), Value::Boolean(self.checking));
        fields.insert(
            crate::error::s("errors"),
            Value::Array(self.errors.iter().map(HelixError::to_value).collect()),
        );
        fields.insert(
            crate::error::s("source_file"),
            self.source_file
                .as_ref()
                .map(|p| crate::error::s(&p.to_string_lossy()))
                .unwrap_or(Value::Null),
        );
        fields.insert(
            crate::error::s("outcome"),
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
        if self.status == Status::Error {
            if let (Some(evaluator), Yaml::Hash(fields)) = (&self.evaluator, &mut state) {
                fields.insert(text("recovery"), text(&evaluator.recovery_source()));
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

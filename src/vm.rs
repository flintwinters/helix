//! The VM: lifecycle status around an evaluator, and the serialized state document.

use std::path::Path;

use yaml_rust2::yaml::Hash;
use yaml_rust2::Yaml;

use crate::emit;
use crate::error::{s, HelixError};
use crate::eval::Evaluator;
use crate::graph::{Graph, ROOT};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Status {
    Ready,
    Running,
    Finished,
    Error,
    Signaled,
}

impl Status {
    pub fn as_str(self) -> &'static str {
        match self {
            Status::Ready => "ready",
            Status::Running => "running",
            Status::Finished => "finished",
            Status::Error => "error",
            Status::Signaled => "signaled",
        }
    }
}

pub struct Vm {
    evaluator: Option<Evaluator>,
    status: Status,
    result: Option<Yaml>,
    error: Option<HelixError>,
    /// Set by `check`: the state then records the `type` phase and lists every error found.
    checking: bool,
    errors: Vec<HelixError>,
}

impl Vm {
    /// Load a program that has no file (stdin); relative imports resolve from the working directory.
    pub fn load(src: &str) -> Vm {
        Vm::load_from(src, None)
    }

    /// Load the program in `file`, whose imports resolve relative to it.
    pub fn load_file(src: &str, file: &Path) -> Vm {
        Vm::load_from(src, Some(file))
    }

    fn load_from(src: &str, file: Option<&Path>) -> Vm {
        let mut vm = Vm { evaluator: None, status: Status::Ready, result: None, error: None, checking: false, errors: Vec::new() };
        match Graph::parse(src).and_then(|graph| Evaluator::new(graph, file)) {
            Ok(evaluator) => vm.evaluator = Some(evaluator),
            Err(e) => vm.fail(e),
        }
        vm
    }

    pub fn status(&self) -> Status {
        self.status
    }

    pub fn run(&mut self) {
        if self.status != Status::Ready {
            return;
        }
        self.status = Status::Running;
        let evaluator = self.evaluator.as_mut().expect("a ready VM has a graph");
        match evaluator.eval(ROOT) {
            Ok(v) => {
                self.result = Some(v);
                self.status = Status::Finished;
            }
            Err(e) => self.fail(e),
        }
    }

    /// Type phase: evaluate the program (pure, bounded, no effects) collecting every error instead
    /// of stopping at the first. A clean program ends `finished` with its resolved `root`.
    pub fn check(&mut self) {
        if self.status != Status::Ready {
            // Load failures still need check-mode error output; completed execution is immutable.
            if self.evaluator.is_none() { self.checking = true; }
            return;
        }
        self.checking = true;
        self.status = Status::Running;
        let evaluator = self.evaluator.as_mut().expect("a ready VM has a graph");
        self.errors = evaluator.collect_errors();
        if !self.errors.is_empty() {
            self.status = Status::Error;
        } else {
            match evaluator.eval(ROOT) {
                Ok(v) => {
                    self.result = Some(v);
                    self.status = Status::Finished;
                }
                Err(e) => self.fail(e),
            }
        }
    }

    /// The serialized VM state: `status`, `phase: type` after a `check`, then `root` (finished) or
    /// `error` / `errors` (error).
    pub fn state(&self) -> Yaml {
        let mut h = Hash::new();
        h.insert(s("status"), s(self.status.as_str()));
        if let Some(r) = &self.result {
            h.insert(s("root"), r.clone());
        }
        if self.checking {
            h.insert(s("phase"), s("type"));
            let all = if self.errors.is_empty() { self.error.iter().collect::<Vec<_>>() } else { self.errors.iter().collect() };
            if !all.is_empty() {
                h.insert(s("errors"), Yaml::Array(all.into_iter().map(HelixError::to_yaml).collect()));
            }
        } else if let Some(e) = &self.error {
            h.insert(s("error"), e.to_yaml());
        }
        Yaml::Hash(h)
    }

    pub fn state_string(&self) -> String {
        emit::document(&self.state())
    }

    fn fail(&mut self, e: HelixError) {
        self.status = Status::Error;
        self.error = Some(e);
    }
}

//! The VM: lifecycle status around an evaluator, and the serialized state document.

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
}

impl Vm {
    pub fn load(src: &str) -> Vm {
        let mut vm = Vm { evaluator: None, status: Status::Ready, result: None, error: None };
        match Graph::parse(src) {
            Ok(graph) => vm.evaluator = Some(Evaluator::new(graph)),
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

    /// The serialized VM state: `status` plus `root` (finished) or `error` (error).
    pub fn state(&self) -> Yaml {
        let mut h = Hash::new();
        h.insert(s("status"), s(self.status.as_str()));
        if let Some(r) = &self.result {
            h.insert(s("root"), r.clone());
        }
        if let Some(e) = &self.error {
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

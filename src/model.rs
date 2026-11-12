//! Shared immutable syntax and execution state. Algorithms live in sibling modules.
mod span;
pub(crate) use span::Span;
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::Arc;
use yaml_rust2::Yaml;
pub(crate) type NodeId = usize;
pub(crate) const ROOT: NodeId = 0;
// Source, computed values and host data share these structural bounds.
pub(crate) const MAX_NODES: usize = 100_000;
pub(crate) const MAX_NESTING: usize = 64;
pub(crate) const MAX_VALUE_SIZE: usize = 1_000_000;
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum Segment {
    Key(String),
    Index(usize),
}
pub(crate) type Outcome = Result<Value, HelixError>;

#[derive(Clone, Debug)]
pub(crate) enum Value {
    Null,
    Boolean(bool),
    Integer(i64),
    Float(f64),
    String(String),
    Array(List),
    Hash(Hash),
    Function(Arc<Function>),
    Machine(Arc<Vm>),
}

/// Ordered, string-keyed objects. The language never permits arbitrary value keys.
#[derive(Clone, Debug, Default, PartialEq)]
pub(crate) struct Hash(pub(crate) Arc<std::collections::BTreeMap<MapKey, Value>>);

#[derive(Clone, Debug)]
pub(crate) struct MapKey(pub(crate) Value);

#[derive(Clone, Debug, Default, PartialEq)]
pub(crate) struct List(pub(crate) Arc<Vec<Value>>);

#[derive(Clone, Debug)]
pub(crate) struct Program {
    pub graph: Graph,
    pub(crate) modules: Modules,
    pub(crate) code: Vec<Value>,
}
/// One call frame. `born` orders frames by creation so type time can tell inputs that existed
/// before a region from frames the region made; it is not part of a frame's identity.
#[derive(Clone, Debug)]
pub(crate) struct Scope {
    pub function: NodeId,
    pub args: Vec<Value>,
    pub born: usize,
}
impl PartialEq for Scope {
    fn eq(&self, other: &Self) -> bool {
        self.function == other.function && self.args == other.args
    }
}
/// An outermost type-time region in progress: frames older than `mark` are outside the region,
/// so each value read from one is an input the region's result depends on.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Region {
    pub(crate) site: NodeId,
    pub(crate) mark: usize,
    pub(crate) inputs: Vec<Value>,
}
#[derive(Clone, Debug)]
pub(crate) struct Function {
    pub program: Arc<Program>,
    pub node: NodeId,
    pub environment: Vec<Scope>,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) enum Binding {
    Node(NodeId),
    Value(Value),
}
#[derive(Clone, Debug, PartialEq)]
pub(crate) enum Actor {
    Builtin(String),
    Function(Arc<Function>),
}
/// Each pending operation is plain, owned state; error handlers are continuations too.
#[derive(Clone, Debug, PartialEq)]
pub(crate) enum Request {
    TypeEnd {
        previous: bool,
        memo: Option<HashMap<NodeId, Value>>,
    },
    Eval(NodeId),
    Finish(NodeId, usize, bool),
    Actor(NodeId, Vec<NodeId>),
    Collect {
        site: NodeId,
        actor: Option<Actor>,
        nodes: Vec<NodeId>,
        keys: Option<Vec<String>>,
        values: Vec<Value>,
    },
    Apply(NodeId, Actor, Vec<NodeId>, Vec<Value>),
    Call(NodeId, Arc<Function>, Vec<Value>),
    CallTrace(NodeId, Arc<Function>),
    CallArgument(NodeId, Vec<Value>),
    CallResolved(NodeId, Vec<Value>),
    CallEnd {
        environment: Vec<Scope>,
        program: Arc<Program>,
    },
    BodyEnd {
        result_type: Option<NodeId>,
        body: NodeId,
    },
    ResultAnnotation(NodeId, Value),
    ResultChecked(NodeId, Value),
    Parameters {
        site: NodeId,
        function: Arc<Function>,
        args: Vec<Value>,
        index: usize,
        found: Vec<Value>,
    },
    ParameterType {
        site: NodeId,
        function: Arc<Function>,
        args: Vec<Value>,
        index: usize,
        found: Vec<Value>,
        annotation: NodeId,
    },
    ParameterChecked {
        site: NodeId,
        function: Arc<Function>,
        args: Vec<Value>,
        index: usize,
        found: Vec<Value>,
    },
    Branch {
        name: String,
        args: Vec<NodeId>,
        index: usize,
    },
    Try(NodeId),
    Finally(NodeId),
    Cleanup(Outcome),
    Iterate(Iteration),
    Iterated {
        iteration: Iteration,
        item: Value,
    },
    UntilTest(Iteration),
    UntilNext(Iteration),
    FormValue {
        site: NodeId,
        name: String,
        args: Vec<NodeId>,
        values: Vec<Value>,
        index: usize,
    },
    Refine(NodeId, Vec<NodeId>, Value),
    Refined(NodeId, Vec<NodeId>, Value),
    Atomic(Atomic),
    PredicateEnd(Atomic),
    AdvanceChild,
}

/// Owned iteration progress; continuations cannot contain an unrelated request.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Iteration {
    pub(crate) site: NodeId,
    pub(crate) name: String,
    pub(crate) args: Vec<NodeId>,
    pub(crate) values: Vec<Value>,
    pub(crate) index: usize,
    pub(crate) output: Vec<Value>,
    pub(crate) state: Value,
    pub(crate) count: usize,
}

/// A pure host operation can request a predicate. Completed answers are part of its continuation.
/// Replaying the deterministic host computation never re-executes a completed Helix predicate.
#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Atomic {
    pub(crate) site: NodeId,
    pub(crate) nodes: Vec<NodeId>,
    pub(crate) operation: Operation,
    pub(crate) answers: Vec<Result<bool, String>>,
}
#[derive(Clone, Debug, PartialEq)]
pub(crate) enum Operation {
    Builtin(String, Vec<Value>),
    Violations(Value, Value),
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct Limits {
    pub max_depth: usize,
    pub max_iterations: usize,
    pub max_work: usize,
    pub max_values: usize,
    pub max_value_size: usize,
    pub max_eval_depth: usize,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Evaluator {
    pub program: Arc<Program>,
    pub environment: Vec<Scope>,
    pub(crate) memo: HashMap<NodeId, Value>,
    pub(crate) active: HashSet<(NodeId, usize)>,
    pub(crate) requests: Vec<Request>,
    pub(crate) answer: Option<Outcome>,
    pub(crate) depth: usize,
    pub(crate) limits: Limits,
    pub(crate) type_time: bool,
    pub(crate) inherited_type_time: bool,
    pub(crate) pending_effect: Option<(NodeId, EffectRequest)>,
    pub(crate) effects: List,
    /// Values that outermost type-time regions read from outside themselves, by region.
    pub(crate) type_inputs: List,
    pub(crate) region: Option<Region>,
    /// Frames created so far; see `Scope::born`.
    pub(crate) scopes: usize,
    pub(crate) effects_allowed: bool,
    pub(crate) signal: Option<Value>,
    pub(crate) remaining_work: usize,
    pub(crate) remaining_values: usize,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) enum Kind {
    /// Resolved YAML scalar value; presentation style has no semantic identity.
    Scalar {
        value: Yaml,
    },
    Seq(Vec<NodeId>),
    Map(Vec<(String, NodeId)>),
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Node {
    pub(crate) kind: Kind,
    pub(crate) parent: Option<NodeId>,
    pub(crate) span: Span,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub(crate) struct Graph {
    pub(crate) nodes: Vec<Node>,
    /// Imported files by the root of the subtree they were grafted as.
    pub(crate) sources: HashMap<NodeId, String>,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct HelixError {
    /// Stable machine-readable category; the full list is in `LANGUAGE.md`.
    pub kind: String,
    pub(crate) phase: Option<&'static str>,
    pub message: String,
    /// The imported file the error lies in; absent for the program itself. `path`, `line` and
    /// `column` are relative to that file.
    pub file: Option<String>,
    /// Root-relative object path of the offending node; absent for parse errors.
    pub path: Option<String>,
    pub(crate) reference: Option<Vec<Segment>>,
    pub span: Span,
    /// Plain-value payload attached by user code via `raise`.
    pub data: Option<Value>,
    /// Call sites ordered from the innermost invocation outward.
    pub(crate) calls: Vec<Value>,
    /// Complete original error supplied to a map-form re-raise.
    pub(crate) cause: Option<Value>,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub(crate) struct Modules {
    pub(crate) linked: HashMap<NodeId, NodeId>,
    pub(crate) policies: HashMap<String, NodeId>,
    pub(crate) roots: HashSet<NodeId>,
    pub(crate) visible_roots: HashSet<NodeId>,
    pub(crate) first_file_import: Option<NodeId>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Status {
    Ready,
    Running,
    Finished,
    Error,
    Signaled,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Vm {
    pub(crate) evaluator: Option<Arc<Evaluator>>,
    pub(crate) source_file: Option<PathBuf>,
    pub(crate) status: Status,
    pub(crate) result: Option<Value>,
    pub(crate) error: Option<HelixError>,
    pub(crate) checking: bool,
    pub(crate) errors: Vec<HelixError>,
}

pub(crate) fn string(text: &str) -> Value {
    Value::String(text.into())
}

/// An explicit host request. IDs are local to an immutable machine branch, not global deduplication keys.
#[derive(Clone, Debug, PartialEq)]
pub struct EffectRequest {
    pub id: u64,
    pub operation: String,
    pub payload: Yaml,
}

impl Default for Limits {
    fn default() -> Self {
        Self {
            max_depth: 200,
            max_iterations: 100_000,
            max_work: 2_000_000,
            max_values: 128_000_000,
            max_value_size: crate::model::MAX_VALUE_SIZE,
            max_eval_depth: 512,
        }
    }
}

/// An ordinary embedded Helix function implementing a builtin's pure policy.
pub(crate) struct PolicyCall {
    pub function: &'static str,
    pub args: Vec<Value>,
}

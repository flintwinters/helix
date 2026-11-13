//! Retained closures, machine versions and evaluator continuations.
use super::*;
use std::collections::HashSet;

impl Wire for Arc<Function> {
    fn put(&self, e: &mut Encoder) -> R<J> {
        e.object("function",self,|f,e|{
        let program=e.v(&f.program)?;let previous=e.program.replace(f.program.clone());
        let result=Ok(serde_json::json!({"program":program,"node":e.n(&f.node)?,"environment":e.v(&f.environment)?}));
        e.program=previous;result
    })
    }
    fn get(j: &J, d: &mut Decoder) -> R<Self> {
        match d.object(j, "function", |j, d| {
            keys(j, &["program", "node", "environment"])?;
            let program: Arc<Program> = d.v(field(j, "program")?)?;
            let previous = d.program.replace(program.clone());
            let node = d.n(field(j, "node")?)?;
            let environment = d.v(field(j, "environment")?)?;
            if program.graph.form(node) != Some("fn") {
                return Err("function reference is not a function declaration".into());
            }
            let function = Function {
                program,
                node,
                environment,
            };
            super::validate::function(&function)?;
            d.program = previous;
            Ok(Object::Function(Arc::new(function)))
        })? {
            Object::Function(f) => Ok(f),
            _ => Err("expected function reference".into()),
        }
    }
}
impl Wire for CallOrigin {
    fn put(&self, e: &mut Encoder) -> R<J> {
        let program = e.v(&self.program)?;
        let previous = e.program.replace(self.program.clone());
        let result = Ok(
            serde_json::json!({"program":program,"site":e.n(&self.site)?,"args":e.ns(&self.args)?}),
        );
        e.program = previous;
        result
    }
    fn get(j: &J, d: &mut Decoder) -> R<Self> {
        keys(j, &["program", "site", "args"])?;
        let program = d.v(field(j, "program")?)?;
        let previous = d.program.replace(program);
        let site = d.n(field(j, "site")?)?;
        let args = d.ns(field(j, "args")?)?;
        let program = std::mem::replace(&mut d.program, previous).unwrap();
        Ok(Self {
            program,
            site,
            args,
        })
    }
}
impl Wire for Arc<Vm> {
    fn put(&self, e: &mut Encoder) -> R<J> {
        e.object("machine",self,|v,e|Ok(serde_json::json!({
        "evaluator":e.v(&v.evaluator)?,"source_file":e.v(&v.source_file)?,"status":e.v(&v.status)?,
        "result":e.v(&v.result)?,"error":e.v(&v.error)?,"checking":e.v(&v.checking)?,"errors":e.v(&v.errors)?
    })))
    }
    fn get(j: &J, d: &mut Decoder) -> R<Self> {
        match d.object(j, "machine", |j, d| {
            keys(
                j,
                &[
                    "evaluator",
                    "source_file",
                    "status",
                    "result",
                    "error",
                    "checking",
                    "errors",
                ],
            )?;
            let v = Vm {
                evaluator: d.v(field(j, "evaluator")?)?,
                source_file: d.v(field(j, "source_file")?)?,
                status: d.v(field(j, "status")?)?,
                result: d.v(field(j, "result")?)?,
                error: d.v(field(j, "error")?)?,
                checking: d.v(field(j, "checking")?)?,
                errors: d.v(field(j, "errors")?)?,
            };
            if matches!(v.status, Status::Ready | Status::Running | Status::Signaled)
                && v.evaluator.is_none()
            {
                return Err("active machine requires evaluator".into());
            }
            if matches!(v.status, Status::Ready | Status::Running)
                && v.evaluator
                    .as_ref()
                    .is_some_and(|e| e.requests.is_empty() && e.answer.is_none())
            {
                return Err("active machine has no remaining execution".into());
            }
            if v.status == Status::Signaled
                && v.evaluator
                    .as_ref()
                    .is_some_and(|e| e.signal.is_none() && e.pending_effect.is_none())
            {
                return Err("signaled machine has no signal or effect".into());
            }
            if v.status == Status::Finished && v.result.is_none() {
                return Err("finished machine requires result".into());
            }
            if v.status == Status::Error && v.error.is_none() && v.errors.is_empty() {
                return Err("error machine requires diagnostic".into());
            }
            Ok(Object::Machine(Arc::new(v)))
        })? {
            Object::Machine(v) => Ok(v),
            _ => Err("expected machine reference".into()),
        }
    }
}
fn put_evaluator(v: &Evaluator, e: &mut Encoder) -> R<J> {
    let program = e.v(&v.program)?;
    let previous = e.program.replace(v.program.clone());
    let mut requests = Vec::new();
    for request in v.requests.iter().rev() {
        if let Request::CallEnd { program, .. } = request {
            e.program = Some(program.clone());
        }
        let caller = match request {
            Request::Parameters { origin, .. }
            | Request::ParameterType { origin, .. }
            | Request::ParameterChecked { origin, .. } => Some(origin.program.clone()),
            _ => None,
        };
        let old = std::mem::replace(&mut e.caller, caller);
        requests.push(e.v(request)?);
        e.caller = old;
    }
    requests.reverse();
    e.program = Some(v.program.clone());
    let mut active = v.active.iter().copied().collect::<Vec<_>>();
    active.sort_unstable();
    let active = active
        .iter()
        .map(|(n, depth)| Ok(serde_json::json!({"node":e.n(n)?,"depth":depth})))
        .collect::<R<Vec<_>>>()?;
    let pending = match &v.pending_effect {
        Some((n, r)) => serde_json::json!({"node":e.n(n)?,"request":e.v(r)?}),
        None => J::Null,
    };
    let result = Ok(
        serde_json::json!({"program":program,"environment":e.v(&v.environment)?,"memo":e.memo(&v.memo)?,"active":active,
        "requests":requests,"answer":e.v(&v.answer)?,"depth":v.depth,"limits":e.v(&v.limits)?,"type_time":v.type_time,
        "inherited_type_time":v.inherited_type_time,"pending_effect":pending,"effects":e.v(&v.effects)?,"type_inputs":e.v(&v.type_inputs)?,
        "annotations":e.v(&v.annotations)?,"annotation_units":v.annotation_units,"annotations_truncated":v.annotations_truncated,
        "region":e.v(&v.region)?,"scopes":v.scopes,"effects_allowed":v.effects_allowed,"signal":e.v(&v.signal)?,
        "remaining_work":v.remaining_work,"remaining_values":v.remaining_values}),
    );
    e.program = previous;
    result
}
fn get_evaluator(j: &J, d: &mut Decoder) -> R<Evaluator> {
    keys(
        j,
        &[
            "program",
            "environment",
            "memo",
            "active",
            "requests",
            "answer",
            "depth",
            "limits",
            "type_time",
            "inherited_type_time",
            "pending_effect",
            "effects",
            "type_inputs",
            "annotations",
            "annotation_units",
            "annotations_truncated",
            "region",
            "scopes",
            "effects_allowed",
            "signal",
            "remaining_work",
            "remaining_values",
        ],
    )?;
    let program: Arc<Program> = d.v(field(j, "program")?)?;
    let previous = d.program.replace(program.clone());
    let mut requests = Vec::new();
    for row in array(field(j, "requests")?)?.iter().rev() {
        if row.get("kind").and_then(J::as_str) == Some("CallEnd") {
            d.program = Some(d.v(field(row, "program")?)?);
        }
        let caller = if let Some(origin) = row.get("origin") {
            Some(d.v(field(origin, "program")?)?)
        } else {
            None
        };
        let old = std::mem::replace(&mut d.caller, caller);
        requests.push(d.v(row)?);
        d.caller = old;
    }
    requests.reverse();
    d.program = Some(program.clone());
    let mut active = HashSet::new();
    for row in array(field(j, "active")?)? {
        keys(row, &["node", "depth"])?;
        let n = d.n(field(row, "node")?)?;
        let depth = d.v(field(row, "depth")?)?;
        if !active.insert((n, depth)) {
            return Err("duplicate active node".into());
        }
    }
    let pending = field(j, "pending_effect")?;
    let pending_effect = if pending.is_null() {
        None
    } else {
        keys(pending, &["node", "request"])?;
        Some((
            d.n(field(pending, "node")?)?,
            d.v(field(pending, "request")?)?,
        ))
    };
    let v = Evaluator {
        program,
        environment: d.v(field(j, "environment")?)?,
        memo: d.memo(field(j, "memo")?)?,
        active,
        requests,
        answer: d.v(field(j, "answer")?)?,
        depth: d.v(field(j, "depth")?)?,
        limits: d.v(field(j, "limits")?)?,
        type_time: d.v(field(j, "type_time")?)?,
        inherited_type_time: d.v(field(j, "inherited_type_time")?)?,
        pending_effect,
        effects: d.v(field(j, "effects")?)?,
        type_inputs: d.v(field(j, "type_inputs")?)?,
        annotations: d.v(field(j, "annotations")?)?,
        annotation_units: d.v(field(j, "annotation_units")?)?,
        annotations_truncated: d.v(field(j, "annotations_truncated")?)?,
        region: d.v(field(j, "region")?)?,
        scopes: d.v(field(j, "scopes")?)?,
        effects_allowed: d.v(field(j, "effects_allowed")?)?,
        signal: d.v(field(j, "signal")?)?,
        remaining_work: d.v(field(j, "remaining_work")?)?,
        remaining_values: d.v(field(j, "remaining_values")?)?,
    };
    let limits = Limits::default();
    if v.limits.max_depth > limits.max_depth
        || v.limits.max_work > limits.max_work
        || v.limits.max_values > limits.max_values
        || v.limits.max_value_size > limits.max_value_size
        || v.limits.max_eval_depth > limits.max_eval_depth
        || v.limits.max_iterations > limits.max_iterations
        || v.remaining_work > v.limits.max_work
        || v.remaining_values > v.limits.max_values
        || v.depth > v.limits.max_depth
    {
        return Err("snapshot exceeds execution limits".into());
    }
    super::validate::evaluator(&v)?;
    d.program = previous;
    Ok(v)
}
impl Wire for Arc<Evaluator> {
    fn put(&self, e: &mut Encoder) -> R<J> {
        e.object("evaluator", self, put_evaluator)
    }
    fn get(j: &J, d: &mut Decoder) -> R<Self> {
        match d.object(j, "evaluator", |j, d| {
            Ok(Object::Evaluator(Arc::new(get_evaluator(j, d)?)))
        })? {
            Object::Evaluator(e) => Ok(e),
            _ => Err("expected evaluator reference".into()),
        }
    }
}

//! Structural continuation checks before accepting externally supplied execution state.
use super::*;
use crate::builtins::{Evaluation, descriptor};
fn arity(name: &str, count: usize) -> R<()> {
    let actor = descriptor(name).ok_or_else(|| format!("unknown actor {name}"))?;
    if count < actor.arity.0 || actor.arity.1.is_some_and(|max| count > max) {
        return Err(format!("invalid {name} continuation arity"));
    }
    Ok(())
}
fn strict(name: &str, count: usize) -> R<()> {
    arity(name, count)?;
    if matches!(descriptor(name).unwrap().evaluation, Evaluation::Form(_)) {
        return Err("form cannot be a strict continuation".into());
    }
    Ok(())
}
fn iteration(i: &Iteration) -> R<()> {
    if !matches!(
        i.name.as_str(),
        "map" | "filter" | "each" | "fold" | "until"
    ) {
        return Err("unknown iteration form".into());
    }
    arity(&i.name, i.args.len())?;
    if i.index > i.values.len() || i.count > Limits::default().max_iterations {
        return Err("iteration cursor outside bounds".into());
    }
    Ok(())
}
fn atomic(a: &Atomic) -> R<()> {
    if let Operation::Builtin(name, values) = &a.operation {
        strict(name, values.len())?;
        if matches!(descriptor(name).unwrap().evaluation, Evaluation::Engine) {
            return Err("engine operation cannot be an atomic continuation".into());
        }
        if a.nodes.len() != values.len() {
            return Err("builtin argument sources disagree with values".into());
        }
    }
    Ok(())
}
fn scopes(scopes: &[Scope], p: &Program) -> R<()> {
    for scope in scopes {
        if p.graph.form(scope.function) != Some("fn") {
            return Err("scope does not reference a function".into());
        }
        let params = p.graph.parameters(scope.function).map_err(|e| e.message)?;
        if params.len() != scope.args.len() {
            return Err("captured argument count disagrees with function".into());
        }
    }
    Ok(())
}
pub(super) fn function(f: &Function) -> R<()> {
    f.program.graph.parameters(f.node).map_err(|e| e.message)?;
    scopes(&f.environment, &f.program)
}
pub(super) fn evaluator(e: &Evaluator) -> R<()> {
    scopes(&e.environment, &e.program)?;
    if e.scopes > e.limits.max_work {
        return Err("scope generation exceeds work budget".into());
    }
    if e.requests.len() > MAX_NODES {
        return Err("too many continuations".into());
    }
    let returns = e
        .requests
        .iter()
        .filter(|r| matches!(r, Request::CallEnd { .. }))
        .count();
    if returns != e.depth {
        return Err("call depth disagrees with return continuations".into());
    }
    for (position, request) in e.requests.iter().enumerate() {
        match request {
            Request::ShareBinding { owner, .. }
                if *owner >= position
                    || !matches!(e.requests.get(*owner), Some(Request::SharedBodyEnd { .. })) =>
            {
                return Err("invalid pending shared binding".into());
            }
            Request::SharedBodyEnd { born, bindings, .. }
                if *born >= e.scopes || bindings.values().any(|v| v.measure().opaque) =>
            {
                return Err("invalid shared binding activation".into());
            }
            Request::Actor(_, nodes) if nodes.is_empty() => {
                return Err("actor continuation requires operands".into());
            }
            Request::Collect {
                nodes,
                keys,
                values,
                actor,
                ..
            } => {
                if let Some(Actor::Builtin(name)) = actor {
                    strict(name, nodes.len())?;
                }
                if values.len() >= nodes.len()
                    || keys.as_ref().is_some_and(|k| k.len() != nodes.len())
                {
                    return Err("invalid collection cursor".into());
                }
            }
            Request::Apply(_, actor, nodes, values) => {
                if nodes.len() != values.len() {
                    return Err("apply argument sources disagree with values".into());
                }
                if let Actor::Builtin(name) = actor {
                    strict(name, values.len())?;
                }
            }
            Request::Call(_, _, args, nodes)
            | Request::CallArgument(_, args, nodes)
            | Request::CallResolved(_, args, nodes) => {
                if !nodes.is_empty() && nodes.len() != args.len() {
                    return Err("call argument sources disagree with values".into());
                }
            }
            Request::Parameters {
                function,
                args,
                index,
                ..
            }
            | Request::ParameterType {
                function,
                args,
                index,
                ..
            }
            | Request::ParameterChecked {
                function,
                args,
                index,
                ..
            } => {
                let params = function
                    .program
                    .graph
                    .parameters(function.node)
                    .map_err(|e| e.message)?;
                if args.len() != params.len() || *index > args.len() {
                    return Err("parameter cursor outside arguments".into());
                }
                if !matches!(request, Request::Parameters { .. })
                    && (*index >= args.len() || params[*index].1.is_none())
                {
                    return Err("invalid annotated parameter cursor".into());
                }
            }
            Request::CallEnd {
                environment,
                program,
            } => scopes(environment, program)?,
            Request::Branch { name, args, index } => {
                if !matches!(name.as_str(), "if" | "cond" | "and" | "or") {
                    return Err("unknown branch form".into());
                }
                arity(name, args.len())?;
                if *index >= args.len()
                    || (name == "if" && *index != 0)
                    || (name == "cond"
                        && (args.len() % 2 != 1 || index % 2 != 0 || *index + 2 >= args.len()))
                {
                    return Err("branch cursor outside operands".into());
                }
            }
            Request::FormValue {
                name,
                args,
                values,
                index,
                ..
            } => {
                if !matches!(
                    name.as_str(),
                    "call" | "vm" | "map" | "filter" | "each" | "fold" | "until"
                ) {
                    return Err("unknown operand form".into());
                }
                arity(name, args.len())?;
                if *index >= args.len() || values.len() != *index {
                    return Err("invalid operand cursor".into());
                }
            }
            Request::Serve(_, options, handler) => {
                crate::graph::http::validate_function(handler,1)?;
                crate::graph::http::validate_options(options)?;
                if let Some((_, pending)) = &e.pending_effect {
                    let Value::Hash(fields) = options else { unreachable!() };
                    if pending.operation != "http_serve" || fields.get(&crate::model::string("bind")).and_then(Value::as_str) != pending.payload.as_str() {
                        return Err("server continuation does not match startup request".into());
                    }
                }
            }
            Request::Refine(_, args, _) | Request::Refined(_, args, _) => {
                arity("refine", args.len())?
            }
            Request::Iterate(i)
            | Request::UntilTest(i)
            | Request::UntilNext(i)
            | Request::Iterated { iteration: i, .. } => iteration(i)?,
            Request::Atomic(a) | Request::PredicateEnd(a, _) => atomic(a)?,
            Request::SignatureSlot(f, found, _) => {
                let params = f.program.graph.parameters(f.node).map_err(|e| e.message)?;
                if found.len() > params.len() {
                    return Err("signature cursor outside slots".into());
                }
            }
            _ => {}
        }
    }
    if let Some((_, pending)) = &e.pending_effect {
        if pending.id != e.effects.len() as u64 + 1
            || e.type_time
            || e.inherited_type_time
            || !e.effects_allowed
        {
            return Err("invalid pending effect identity or phase".into());
        }
    }
    Ok(())
}

/// A snapshot can edit data, but cannot make an internal continuation consume an
/// absent or incompatible intermediate result. Apply this at transition time too,
/// since an edited pending handler may produce that result several steps later.
pub(crate) fn next(e: &Evaluator) -> R<()> {
    let Some(r) = e.requests.last() else {
        return if e.answer.is_some() {
            Ok(())
        } else {
            Err("machine has no continuation or result".into())
        };
    };
    if matches!(r, Request::AdvanceChild) && !matches!(e.answer, Some(Ok(Value::Machine(_)))) {
        return Err("child continuation requires a machine".into());
    }
    if matches!(e.answer, Some(Err(_))) {
        return Ok(());
    }
    let needs = !matches!(
        r,
        Request::Eval(_)
            | Request::Finish(..)
            | Request::CallTrace(..)
            | Request::CallEnd { .. }
            | Request::Apply(..)
            | Request::Call(..)
            | Request::CallArgument(..)
            | Request::Parameters { .. }
            | Request::Iterate(_)
            | Request::Atomic(_)
            | Request::SignatureOf(..)
            | Request::TypeEnd { .. }
    );
    if needs && e.answer.is_none() {
        return Err("continuation requires an intermediate result".into());
    }
    if matches!(
        r,
        Request::ParameterChecked { .. } | Request::ResultChecked(..)
    ) && !matches!(e.answer, Some(Ok(Value::Array(_))))
    {
        return Err("annotation continuation requires violation data".into());
    }
    if matches!(r, Request::AdvanceChild) && !matches!(e.answer, Some(Ok(Value::Machine(_)))) {
        return Err("child continuation requires a machine".into());
    }
    Ok(())
}

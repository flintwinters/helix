//! Source expressions initiate requests; the parent engine resumes their continuations.
use super::arguments;
use crate::builtins;
use crate::model::{
    Actor, Binding, Evaluator, HelixError, Kind, Mode, NodeId, Request, Value, string as s,
};

impl Evaluator {
    pub(super) fn evaluate(&mut self, id: NodeId, mode: Mode) -> Result<(), HelixError> {
        self.remaining_work = self.remaining_work.checked_sub(1).ok_or_else(|| {
            self.err(
                id,
                "limit",
                format!("evaluation exceeds {} node visits", self.limits.max_work),
            )
        })?;
        if self.active.len() >= self.limits.max_eval_depth {
            return Err(self.err(
                id,
                "depth",
                format!(
                    "evaluation nested deeper than {} nodes",
                    self.limits.max_eval_depth
                ),
            ));
        }
        if mode == Mode::Argument {
            if let Some(name) = self.graph().symbol(id) {
                match self.resolve(id, name) {
                    Some(Binding::Value(value)) => {
                        self.charge_value(id, &value)?;
                        self.finish(value);
                        return Ok(());
                    }
                    Some(Binding::Node(target)) => {
                        self.eval(target, false);
                        return Ok(());
                    }
                    None if builtins::is_primitive(name) => {
                        self.finish(s(name));
                        return Ok(());
                    }
                    None => {
                        return Err(self.err(id, "unbound", format!("unbound symbol `{name}`")));
                    }
                }
            }
        }
        if let Some(value) = self.memo.get(&id).cloned() {
            self.charge_value(id, &value)?;
            self.finish(value);
            return Ok(());
        }
        let local = self.in_function(id);
        let depth = if local { self.depth } else { 0 };
        if !self.active.insert((id, depth)) {
            return Err(self.err(id, "cycle", "value depends on itself"));
        }
        self.push(Request::Finish(id, depth, local));
        match self.graph().kind(id).clone() {
            Kind::Scalar { value, .. } => self.finish(Value::from_yaml(&value)),
            Kind::Map(entries) => {
                let (keys, nodes) = entries.into_iter().unzip();
                self.collect(id, None, nodes, Some(keys), false);
            }
            Kind::Seq(items) => {
                let head = items.first().copied();
                if let Some(name) = head
                    .and_then(|h| self.graph().symbol(h))
                    .map(str::to_string)
                {
                    if builtins::arity(&name).is_some() {
                        self.apply(id, Actor::Builtin(name), items[1..].to_vec())?;
                    } else if let Some(binding) = self.resolve(head.unwrap(), &name) {
                        self.push(Request::Actor(id, items));
                        match binding {
                            Binding::Value(v) => self.finish(v),
                            Binding::Node(n) => self.eval(n, false),
                        }
                    } else {
                        self.collect(id, None, items, None, false);
                    }
                } else if head.is_some_and(|h| matches!(self.graph().kind(h), Kind::Seq(_))) {
                    self.push(Request::Actor(id, items.clone()));
                    self.eval(head.unwrap(), true);
                } else {
                    self.collect(id, None, items, None, false);
                }
            }
        }
        Ok(())
    }
    pub(super) fn collect(
        &mut self,
        site: NodeId,
        actor: Option<Actor>,
        nodes: Vec<NodeId>,
        keys: Option<Vec<String>>,
        argument: bool,
    ) {
        self.collect_next(site, actor, nodes, keys, Vec::new(), argument);
    }
    pub(super) fn collect_next(
        &mut self,
        site: NodeId,
        actor: Option<Actor>,
        nodes: Vec<NodeId>,
        keys: Option<Vec<String>>,
        values: Vec<Value>,
        argument: bool,
    ) {
        let index = values.len();
        if let Some(&node) = nodes.get(index) {
            self.push(Request::Collect {
                site,
                actor,
                nodes,
                keys,
                values,
                argument,
            });
            self.eval(node, argument);
        } else if let Some(actor) = actor {
            self.push(Request::Apply(site, actor, nodes, values));
        } else if let Some(keys) = keys {
            self.finish(Value::Hash(
                keys.into_iter().map(|k| s(&k)).zip(values).collect(),
            ));
        } else {
            self.finish(Value::Array(values.into()));
        }
    }
    pub(super) fn apply(
        &mut self,
        id: NodeId,
        actor: Actor,
        args: Vec<NodeId>,
    ) -> Result<(), HelixError> {
        if let Actor::Builtin(name) = &actor {
            let descriptor = builtins::descriptor(name).unwrap();
            let (min, max) = descriptor.arity;
            if args.len() < min || max.is_some_and(|max| args.len() > max) {
                let expected = match max {
                    Some(m) if m == min => arguments(m),
                    Some(m) => format!("{min} to {}", arguments(m)),
                    None => format!("at least {}", arguments(min)),
                };
                return Err(self.err(
                    id,
                    "arity",
                    format!("`{name}` takes {expected}, got {}", args.len()),
                ));
            }
            if matches!(descriptor.evaluation, builtins::Evaluation::Form(_)) {
                return self.form(id, descriptor, args);
            }
        }
        self.collect(id, Some(actor), args, None, true);
        Ok(())
    }
}

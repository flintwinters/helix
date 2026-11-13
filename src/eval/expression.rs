//! Source expressions initiate requests; the parent engine resumes their continuations.
use super::arguments;
use crate::builtins;
use crate::model::{Actor, Binding, Evaluator, HelixError, NodeId, Request, Value, string as s};
use crate::graph::Syntax;

impl Evaluator {
    pub(super) fn evaluate(&mut self, id: NodeId) -> Result<(), HelixError> {
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
        let program = self.program.clone();
        if let Syntax::Reference(name) = program.graph.syntax(id) {
            match self.resolve_input(id, name) {
                Some(found) => self.read_resolution(id, name, false, found)?,
                None if builtins::is_primitive(name) => self.finish(s(name)),
                None => return Err(self.err(id, "unbound", format!("unbound symbol `{name}`"))),
            }
            return Ok(());
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
        match self.graph().syntax(id) {
            Syntax::Invalid(message) => return Err(self.err(id, "syntax", message)),
            Syntax::Reference(_) => unreachable!(),
            Syntax::Scalar(value) => self.finish(Value::from_yaml(value)),
            Syntax::Literal(value) => {
                let value = self.graph().data(value);
                self.charge_value(id, &value)?;
                self.finish(value);
            }
            Syntax::Mapping(entries) => {
                let (keys, nodes) = entries.iter().cloned().unzip();
                self.collect(id, None, nodes, Some(keys));
            }
            Syntax::Sequence(items) => self.collect(id, None, items.to_vec(), None),
            Syntax::Call(items) => {
                let items = items.to_vec();
                let head = items[0];
                if let Some(name) = self.graph().identifier(head).map(str::to_string) {
                    if builtins::arity(&name).is_some() {
                        self.apply(id, Actor::Builtin(name), items[1..].to_vec())?;
                    } else if let Some(found) = self.resolve_input(id, &name) {
                        self.push(Request::Actor(id, items));
                        self.read_resolution(head, &name, true, found)?;
                    } else {
                        return Err(self.err(head, "unbound", format!("unbound actor `{name}`")));
                    }
                } else {
                    self.push(Request::Actor(id, items));
                    self.eval(head);
                }
            }
        }
        Ok(())
    }
    fn read_resolution(
        &mut self,
        site: NodeId,
        name: &str,
        actor: bool,
        found: super::lexical::Resolution,
    ) -> Result<(), HelixError> {
        if !found.members.is_empty() {
            self.push(Request::Project {
                site, name: name.to_string(), members: found.members, actor,
            });
        }
        match found.binding {
            Binding::Value(value) => {
                if !actor { self.charge_value(site, &value)?; }
                self.finish(value);
            }
            Binding::Node(node) => self.eval(node),
        }
        Ok(())
    }
    pub(super) fn collect(
        &mut self,
        site: NodeId,
        actor: Option<Actor>,
        nodes: Vec<NodeId>,
        keys: Option<Vec<String>>,
    ) {
        self.collect_next(site, actor, nodes, keys, Vec::new());
    }
    pub(super) fn collect_next(
        &mut self,
        site: NodeId,
        actor: Option<Actor>,
        nodes: Vec<NodeId>,
        keys: Option<Vec<String>>,
        values: Vec<Value>,
    ) {
        let index = values.len();
        if let Some(&node) = nodes.get(index) {
            self.push(Request::Collect {
                site,
                actor,
                nodes,
                keys,
                values,
            });
            self.eval(node);
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
        self.collect(id, Some(actor), args, None);
        Ok(())
    }
}

//! Source expressions initiate requests; the parent engine resumes their continuations.
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
        if let Syntax::Reference(_) = program.graph.syntax(id) {
            return self.reference(id);
        }
        if let Some(value) = self.memo.get(&id).cloned() {
            self.charge_value(id, &value)?;
            self.finish(value);
            return Ok(());
        }
        let local = self.in_function(id);
        let depth = if local { self.depth } else { 0 };
        if !self.active.insert((id, depth)) {
            return Err(self.err(id, "cycle", "value depends on itself").with_hint(Some(
                "evaluating this value reads it again through the references it contains; compute one value in the loop from inputs that do not depend on it, or recurse through a function".into(),
            )));
        }
        self.push(Request::Finish(id, depth, local));
        match program.graph.syntax(id) {
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
                let head = items[0];
                if let Some(descriptor) = program.graph.builtin_call(id) {
                    self.apply_builtin(id, descriptor, items[1..].to_vec())?;
                } else if let Some(name) = program.graph.identifier(head) {
                    if let Some(found) = self.resolve_input(id, name) {
                        self.push(Request::Actor(id, items.to_vec()));
                        self.read_resolution(head, name, true, found)?;
                    } else {
                        return Err(self.unbound(head, name, true));
                    }
                } else {
                    self.push(Request::Actor(id, items.to_vec()));
                    self.eval(head);
                }
            }
        }
        Ok(())
    }
    /// Follow a chain of references that alias other references without entering the cycle
    /// guard per hop; revisiting a reference in the chain is a cycle.
    fn reference(&mut self, id: NodeId) -> Result<(), HelixError> {
        let program = self.program.clone();
        let mut chain = vec![id];
        let mut site = id;
        loop {
            let Syntax::Reference(name) = program.graph.syntax(site) else { unreachable!() };
            let found = match self.resolve_input(site, name) {
                Some(found) => found,
                None if builtins::is_primitive(name) => {
                    self.finish(s(name));
                    return Ok(());
                }
                None => return Err(self.unbound(site, name, false)),
            };
            match found.binding {
                Binding::Node(next) if found.members.is_empty() && matches!(program.graph.syntax(next), Syntax::Reference(_)) => {
                    if chain.contains(&next) {
                        return Err(self.reference_cycle(id, &chain));
                    }
                    chain.push(next);
                    site = next;
                }
                _ => return self.read_resolution(site, name, false, found),
            }
        }
    }
    fn reference_cycle(&self, id: NodeId, chain: &[NodeId]) -> HelixError {
        // The names read around the loop, closed by repeating the first.
        let names = chain
            .iter()
            .chain(chain.first())
            .filter_map(|node| match self.graph().syntax(*node) {
                Syntax::Reference(name) => Some(format!("`{name}`")),
                _ => None,
            })
            .collect::<Vec<_>>();
        self.err(id, "cycle", format!("references only name each other: {}", names.join(" -> ")))
            .with_hint(Some("give one of these names a value instead of another reference".into()))
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
    /// A builtin call whose descriptor is already resolved. Arity and form policy are checked
    /// here, the one place a builtin call is admitted.
    fn apply_builtin(
        &mut self,
        id: NodeId,
        descriptor: &'static builtins::Descriptor,
        args: Vec<NodeId>,
    ) -> Result<(), HelixError> {
        if let Some((message, hint)) = descriptor.misfit(args.len()) {
            return Err(self.err(id, "arity", message).with_hint(Some(hint)));
        }
        if matches!(descriptor.evaluation, builtins::Evaluation::Form(_)) {
            return self.form(id, descriptor, args);
        }
        self.collect(id, Some(Actor::Builtin(descriptor.name.to_string())), args, None);
        Ok(())
    }
}

//! Iteration progress advances through the shared request queue, never host recursion.
use crate::model::{Evaluator, HelixError, Iteration, Request, Value};

impl Evaluator {
    pub(super) fn iterate(&mut self, iteration: Iteration) -> Result<(), HelixError> {
        if iteration.name == "until" {
            if iteration.count >= self.limits.max_iterations {
                return Err(self.err(
                    iteration.site,
                    "limit",
                    format!(
                        "`until` did not finish within {} steps",
                        self.limits.max_iterations
                    ),
                ));
            }
            let callback = iteration.args[0];
            let arg = iteration.state.clone();
            self.push(Request::UntilTest(iteration));
            self.push(Request::CallArgument(callback, vec![arg]));
        } else if let Some(item) = iteration.values.get(iteration.index).cloned() {
            let callback = iteration.args[0];
            let arguments = if iteration.name == "fold" {
                vec![iteration.state.clone(), item.clone()]
            } else {
                vec![item.clone()]
            };
            self.push(Request::Iterated { iteration, item });
            self.push(Request::CallArgument(callback, arguments));
        } else {
            self.finish(match iteration.name.as_str() {
                "fold" => iteration.state,
                "each" => Value::Null,
                _ => Value::Array(iteration.output.into()),
            });
        }
        Ok(())
    }

    pub(super) fn iterated(
        &mut self,
        mut iteration: Iteration,
        item: Value,
    ) -> Result<(), HelixError> {
        let value = self.value();
        match iteration.name.as_str() {
            "map" => iteration.output.push(value),
            "filter" => {
                if self.truth(&value, iteration.args[0])? {
                    iteration.output.push(item);
                }
            }
            "fold" => iteration.state = value,
            _ => {}
        }
        iteration.index += 1;
        self.push(Request::Iterate(iteration));
        Ok(())
    }

    pub(super) fn until_test(&mut self, iteration: Iteration) -> Result<(), HelixError> {
        let verdict = self.value();
        if self.truth(&verdict, iteration.args[0])? {
            self.finish(iteration.state);
        } else {
            let callback = iteration.args[1];
            let arg = iteration.state.clone();
            self.push(Request::UntilNext(iteration));
            self.push(Request::CallArgument(callback, vec![arg]));
        }
        Ok(())
    }
}

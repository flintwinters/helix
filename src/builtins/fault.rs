//! The failure value builtins return; the VM maps it onto the blamed argument node.

use crate::value::Value;

use super::value::described;

/// A builtin failure; `arg` is the index of the offending argument, or `None` for the whole expression.
pub struct Fault {
    pub kind: String,
    pub message: String,
    pub arg: Option<usize>,
    /// Payload of a user `raise`; builtins never set it.
    pub data: Option<Value>,
    /// The original error map when user code re-raises it.
    pub cause: Option<Value>,
    /// An advisory correction, carried into the error's `hint`.
    pub hint: Option<String>,
}

impl Fault {
    pub(super) fn at(arg: usize, kind: &'static str, message: String) -> Fault {
        Fault { kind: kind.into(), message, arg: Some(arg), data: None, cause: None, hint: None }
    }

    pub(super) fn whole(kind: &'static str, message: String) -> Fault {
        Fault { kind: kind.into(), message, arg: None, data: None, cause: None, hint: None }
    }

    pub(super) fn with_hint(self, hint: String) -> Fault {
        Fault { hint: Some(hint), ..self }
    }

    pub(super) fn expected(arg: usize, wanted: &str, got: &Value) -> Fault {
        Fault::at(arg, "type", format!("expected {wanted}, got {}", described(got)))
    }
}

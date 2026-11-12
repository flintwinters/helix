//! The failure value builtins return; the VM maps it onto the blamed argument node.

use yaml_rust2::Yaml;

use super::value::type_name;

/// A builtin failure; `arg` is the index of the offending argument, or `None` for the whole expression.
pub struct Fault {
    pub kind: String,
    pub message: String,
    pub arg: Option<usize>,
    /// Payload of a user `raise`; builtins never set it.
    pub data: Option<Yaml>,
    /// The original error map when user code re-raises it.
    pub cause: Option<Yaml>,
}

impl Fault {
    pub(super) fn at(arg: usize, kind: &'static str, message: String) -> Fault {
        Fault { kind: kind.into(), message, arg: Some(arg), data: None, cause: None }
    }

    pub(super) fn whole(kind: &'static str, message: String) -> Fault {
        Fault { kind: kind.into(), message, arg: None, data: None, cause: None }
    }

    pub(super) fn expected(arg: usize, wanted: &str, got: &Yaml) -> Fault {
        Fault::at(arg, "type", format!("expected {wanted}, got {}", type_name(got)))
    }
}

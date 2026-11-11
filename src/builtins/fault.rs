//! The failure value builtins return; the VM maps it onto the blamed argument node.

use yaml_rust2::Yaml;

use super::value::type_name;

/// A builtin failure; `arg` is the index of the offending argument, or `None` for the whole expression.
pub struct Fault {
    pub kind: &'static str,
    pub message: String,
    pub arg: Option<usize>,
}

impl Fault {
    pub(super) fn at(arg: usize, kind: &'static str, message: String) -> Fault {
        Fault { kind, message, arg: Some(arg) }
    }

    pub(super) fn whole(kind: &'static str, message: String) -> Fault {
        Fault { kind, message, arg: None }
    }

    pub(super) fn expected(arg: usize, wanted: &str, got: &Yaml) -> Fault {
        Fault::at(arg, "type", format!("expected {wanted}, got {}", type_name(got)))
    }
}

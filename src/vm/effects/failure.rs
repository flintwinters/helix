//! Shared host-boundary failures use the VM effect-reply contract.
pub(super) type Failure = (String, String);
pub(super) fn fault(kind: &str, message: &str) -> Failure { (kind.into(), message.into()) }
pub(super) fn io_fault(error: impl std::fmt::Display) -> Failure { fault("io", &error.to_string()) }

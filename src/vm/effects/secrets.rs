//! Resolve opaque handles only inside a caller-owned host operation. Neither
//! credential bytes nor captured output from that operation enter machine state.
use super::{fault, object, EffectHost, Reply, LIMIT};
use std::io::Read;
use yaml_rust2::Yaml;

pub(super) fn input<'a>(operation: &str, payload: &'a Yaml) -> Option<(&'static str, &'a Yaml)> {
    let key = match operation {
        "command" | "ssh_command" => "stdin",
        "ssh_file_write" => "text",
        _ => return None,
    };
    let value = &payload[key];
    (value["type"].as_str() == Some("secret")).then_some((key, value))
}

// Applied to both host replies and operator reconciliation: neither path may
// introduce echoed credentials into a durable continuation.
pub(super) fn protect(operation: &str, payload: &Yaml, reply: Reply) -> Reply {
    if input(operation, payload).is_none() { return reply; }
    match reply {
        Ok(value) if matches!(operation, "command" | "ssh_command") => {
            let code = value["code"].as_i64().ok_or_else(|| fault("secret", "secret command outcome requires an exit code"))?;
            Ok(object([("code", Yaml::Integer(code)), ("stdout", Yaml::String(String::new())), ("stderr", Yaml::String(String::new()))]))
        }
        Ok(_) => Ok(Yaml::Null),
        Err(_) => Err(fault("secret", "secret input operation failed; diagnostic content suppressed")),
    }
}

impl EffectHost {
    pub(super) fn handle_secret(&mut self, id: u64, operation: &str, payload: &Yaml) -> Reply {
        let Some((key, handle)) = input(operation, payload) else {
            return self.handle_plain(id, operation, payload);
        };
        let reply = (|| {
            if handle.as_hash().is_none_or(|fields| fields.len() != 2) {
                return Err(fault("secret", "invalid secret handle"));
            }
            let name = handle["ref"].as_str().ok_or_else(|| fault("secret", "invalid secret handle"))?;
            let path = self.secret_files.get(name).ok_or_else(|| fault("capability", "secret capability not granted"))?;
            let mut options = std::fs::OpenOptions::new();
            options.read(true);
            #[cfg(unix)] {
                use std::os::unix::fs::OpenOptionsExt;
                options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
            }
            let file = options.open(path).map_err(super::io_fault)?;
            let metadata = file.metadata().map_err(super::io_fault)?;
            if !metadata.is_file() { return Err(fault("secret", "secret source must be a regular file")); }
            #[cfg(unix)] {
                use std::os::unix::fs::MetadataExt;
                if metadata.mode() & 0o077 != 0 {
                    return Err(fault("secret", "secret source must be private to its owner"));
                }
            }
            let mut text = String::new();
            file.take(LIMIT as u64 + 1).read_to_string(&mut text).map_err(super::io_fault)?;
            if text.len() > LIMIT { return Err(fault("resource", "secret source exceeds input budget")); }
            let mut resolved = payload.clone();
            let Yaml::Hash(fields) = &mut resolved else { unreachable!() };
            fields.insert(Yaml::String(key.into()), Yaml::String(text));
            if operation == "ssh_file_write" && matches!(fields.get(&Yaml::String("mode".into())), None | Some(Yaml::Null)) {
                fields.insert(Yaml::String("mode".into()), Yaml::String("0600".into()));
            }
            self.handle_plain(id, operation, &resolved)
        })();
        protect(operation, payload, reply)
    }
}

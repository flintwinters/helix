//! SQLite adapter for the `sqlite` effect: one statement per request on a database confined to caller roots.
use std::path::Path;
use rusqlite::{Connection, OpenFlags, hooks::{AuthAction, Authorization}, types::{Value as Sql, ValueRef}};
use yaml_rust2::Yaml;
use super::failure::{Failure, fault, io_fault};

/// Bounds the bytes a result may carry so a query cannot exceed the VM data budget unchecked.
pub(super) const RESULT_LIMIT: usize = 1024 * 1024;

pub(super) struct Statement<'a> { pub sql: &'a str, pub params: Vec<Sql> }

impl<'a> Statement<'a> {
    pub(super) fn parse(payload: &'a Yaml) -> Result<Self, Failure> {
        let sql = payload["sql"].as_str().ok_or_else(|| fault("type", "sqlite sql must be a string"))?;
        let params = match &payload["params"] {
            Yaml::BadValue | Yaml::Null => Vec::new(),
            Yaml::Array(items) => items.iter().map(parameter).collect::<Result<_, _>>()?,
            _ => return Err(fault("type", "sqlite params must be a list")),
        };
        Ok(Self { sql, params })
    }
}

fn parameter(value: &Yaml) -> Result<Sql, Failure> {
    match value {
        Yaml::Null => Ok(Sql::Null),
        Yaml::Boolean(flag) => Ok(Sql::Integer(*flag as i64)),
        Yaml::Integer(number) => Ok(Sql::Integer(*number)),
        Yaml::Real(text) => text.parse().map(Sql::Real).map_err(|_| fault("type", "sqlite parameter is not a finite number")),
        Yaml::String(text) => Ok(Sql::Text(text.clone())),
        _ => Err(fault("type", "sqlite parameters must be null, boolean, number or string")),
    }
}

/// `write` selects a read-write-create connection; otherwise SQLite itself refuses mutation.
/// ATTACH and DETACH are denied so a statement cannot reach a file outside the authorized path.
pub(super) fn run(path: &Path, write: bool, statement: &Statement) -> Result<Yaml, Failure> {
    let flags = if write { OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_CREATE } else { OpenFlags::SQLITE_OPEN_READ_ONLY };
    let mut connection = Connection::open_with_flags(path, flags | OpenFlags::SQLITE_OPEN_NO_MUTEX).map_err(sql_fault)?;
    connection.busy_timeout(std::time::Duration::from_secs(10)).map_err(sql_fault)?;
    connection.authorizer(Some(|context: rusqlite::hooks::AuthContext<'_>| match context.action {
        AuthAction::Attach { .. } | AuthAction::Detach { .. } => Authorization::Deny,
        _ => Authorization::Allow,
    })).map_err(sql_fault)?;
    let mut prepared = connection.prepare(statement.sql).map_err(sql_fault)?;
    let columns: Vec<String> = prepared.column_names().into_iter().map(String::from).collect();
    let mut rows = Vec::new();
    let mut size = 0;
    let mut cursor = prepared.query(rusqlite::params_from_iter(&statement.params)).map_err(sql_fault)?;
    while let Some(row) = cursor.next().map_err(sql_fault)? {
        let mut cells = Vec::with_capacity(columns.len());
        for index in 0..columns.len() {
            let cell = cell(row.get_ref(index).map_err(sql_fault)?)?;
            size += match &cell { Yaml::String(text) => text.len(), _ => 8 };
            cells.push(cell);
        }
        if size > RESULT_LIMIT { return Err(fault("resource", "sqlite result exceeds 1 MiB")); }
        rows.push(Yaml::Array(cells));
    }
    drop(cursor);
    drop(prepared);
    let changes = connection.changes() as i64;
    Ok(Yaml::Hash([
        ("columns", Yaml::Array(columns.into_iter().map(Yaml::String).collect())),
        ("rows", Yaml::Array(rows)),
        ("changes", Yaml::Integer(if write { changes } else { 0 })),
    ].into_iter().map(|(key, value)| (Yaml::String(key.into()), value)).collect()))
}

fn cell(value: ValueRef) -> Result<Yaml, Failure> {
    Ok(match value {
        ValueRef::Null => Yaml::Null,
        ValueRef::Integer(number) => Yaml::Integer(number),
        ValueRef::Real(number) if number.is_finite() => Yaml::Real(format!("{number:?}")),
        ValueRef::Real(_) => return Err(fault("conversion", "sqlite result contains a non-finite number")),
        ValueRef::Text(bytes) => Yaml::String(String::from_utf8(bytes.to_vec()).map_err(io_fault)?),
        ValueRef::Blob(_) => return Err(fault("conversion", "sqlite blob results are unsupported; cast to text or hex")),
    })
}

fn sql_fault(error: rusqlite::Error) -> Failure { fault("sqlite", &error.to_string()) }

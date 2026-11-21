//! SQLite adapter for the `sqlite` effect: one statement, or one atomic transaction, per request on a database confined to caller roots.
use std::{path::Path, sync::{Arc, atomic::{AtomicBool, Ordering}}};
use rusqlite::{Connection, OpenFlags, TransactionBehavior, hooks::{AuthAction, Authorization}, types::{Value as Sql, ValueRef}};
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

/// Upper bound on statements in one transaction, keeping the request a bounded unit of work.
const BATCH_LIMIT: usize = 256;

/// Either one statement, or an ordered list that commits or rolls back as one transaction.
pub(super) enum Request<'a> { One(Statement<'a>), Transaction(Vec<Statement<'a>>) }

impl<'a> Request<'a> {
    pub(super) fn parse(payload: &'a Yaml) -> Result<Self, Failure> {
        match (&payload["sql"], &payload["statements"]) {
            (Yaml::BadValue, Yaml::BadValue) => Err(fault("type", "sqlite request needs `sql` or `statements`")),
            (_, Yaml::BadValue) => Statement::parse(payload).map(Self::One),
            (Yaml::BadValue, Yaml::Array(items)) if (1..=BATCH_LIMIT).contains(&items.len()) =>
                items.iter().map(Statement::parse).collect::<Result<_, _>>().map(Self::Transaction),
            (Yaml::BadValue, Yaml::Array(_)) =>
                Err(fault("range", &format!("sqlite statements must hold 1 to {BATCH_LIMIT} entries"))),
            (Yaml::BadValue, _) => Err(fault("type", "sqlite statements must be a list of {sql, params} maps")),
            _ => Err(fault("type", "sqlite request takes `sql` or `statements`, not both")),
        }
    }
}

/// `write` selects a read-write-create connection; otherwise SQLite itself refuses mutation.
/// ATTACH and DETACH are denied so a statement cannot reach a file outside the authorized path.
/// Inside a transaction, statements cannot issue transaction control to end it early;
/// the first failure rolls every statement back and a write commits only when all succeeded.
pub(super) fn run(path: &Path, write: bool, request: &Request) -> Result<Yaml, Failure> {
    let flags = if write { OpenFlags::SQLITE_OPEN_READ_WRITE | OpenFlags::SQLITE_OPEN_CREATE } else { OpenFlags::SQLITE_OPEN_READ_ONLY };
    let mut connection = Connection::open_with_flags(path, flags | OpenFlags::SQLITE_OPEN_NO_MUTEX).map_err(sql_fault)?;
    connection.busy_timeout(std::time::Duration::from_secs(10)).map_err(sql_fault)?;
    // Transaction control is permitted only while this function itself issues BEGIN and COMMIT.
    let control = Arc::new(AtomicBool::new(true));
    let gate = control.clone();
    connection.authorizer(Some(move |context: rusqlite::hooks::AuthContext<'_>| match context.action {
        AuthAction::Attach { .. } | AuthAction::Detach { .. } => Authorization::Deny,
        AuthAction::Transaction { .. } | AuthAction::Savepoint { .. } if !gate.load(Ordering::Relaxed) => Authorization::Deny,
        _ => Authorization::Allow,
    })).map_err(sql_fault)?;
    let mut size = 0;
    match request {
        Request::One(statement) => statement.run(&connection, write, &mut size),
        Request::Transaction(statements) => {
            let behavior = if write { TransactionBehavior::Immediate } else { TransactionBehavior::Deferred };
            let transaction = connection.transaction_with_behavior(behavior).map_err(sql_fault)?;
            control.store(false, Ordering::Relaxed);
            let results = statements.iter().map(|statement| statement.run(&transaction, write, &mut size))
                .collect::<Result<Vec<_>, _>>()?;
            control.store(true, Ordering::Relaxed);
            transaction.commit().map_err(sql_fault)?;
            Ok(Yaml::Hash([(Yaml::String("results".into()), Yaml::Array(results))].into_iter().collect()))
        }
    }
}

impl Statement<'_> {
    /// `size` accumulates across a transaction so the whole reply stays within the result limit.
    fn run(&self, connection: &Connection, write: bool, size: &mut usize) -> Result<Yaml, Failure> {
        let mut prepared = connection.prepare(self.sql).map_err(sql_fault)?;
        let columns: Vec<String> = prepared.column_names().into_iter().map(String::from).collect();
        let before = connection.total_changes();
        let mut rows = Vec::new();
        let mut cursor = prepared.query(rusqlite::params_from_iter(&self.params)).map_err(sql_fault)?;
        while let Some(row) = cursor.next().map_err(sql_fault)? {
            let mut cells = Vec::with_capacity(columns.len());
            for index in 0..columns.len() {
                let cell = cell(row.get_ref(index).map_err(sql_fault)?)?;
                *size += match &cell { Yaml::String(text) => text.len(), _ => 8 };
                cells.push(cell);
            }
            if *size > RESULT_LIMIT { return Err(fault("resource", "sqlite result exceeds 1 MiB")); }
            rows.push(Yaml::Array(cells));
        }
        drop(cursor);
        let changes = if write { (connection.total_changes() - before) as i64 } else { 0 };
        Ok(Yaml::Hash([
            ("columns", Yaml::Array(columns.into_iter().map(Yaml::String).collect())),
            ("rows", Yaml::Array(rows)),
            ("changes", Yaml::Integer(changes)),
        ].into_iter().map(|(key, value)| (Yaml::String(key.into()), value)).collect()))
    }
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

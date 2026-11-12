//! Minimal LSP 3.17 transport: full document synchronization, diagnostics and formatting.
//! No workspace files are opened and no Helix expressions are evaluated.
use std::collections::HashMap;
use std::io::{self, BufRead, Read, Write};

use serde_json::{Value, json};

const MAX_MESSAGE: usize = 8 * 1024 * 1024;
const MAX_DOCUMENTS: usize = 256;

fn invalid(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}

fn read_message(input: &mut impl BufRead) -> io::Result<Option<Value>> {
    let mut length = None;
    let mut header_bytes = 0;
    loop {
        let mut line = String::new();
        // Bound header reads as well as the body; an unterminated header cannot grow forever.
        let bytes = input.by_ref().take(8193).read_line(&mut line)?;
        if bytes == 0 {
            return if header_bytes == 0 {
                Ok(None)
            } else {
                Err(invalid("truncated LSP header"))
            };
        }
        header_bytes += bytes;
        if header_bytes > 8192 {
            return Err(invalid("LSP header exceeds 8192 bytes"));
        }
        if line == "\r\n" || line == "\n" {
            break;
        }
        let (key, value) = line
            .split_once(':')
            .ok_or_else(|| invalid("invalid LSP header"))?;
        if key.eq_ignore_ascii_case("Content-Length") {
            if length.is_some() {
                return Err(invalid("duplicate Content-Length"));
            }
            length = Some(
                value
                    .trim()
                    .parse::<usize>()
                    .map_err(|_| invalid("invalid Content-Length"))?,
            );
        }
    }
    let length = length
        .filter(|length| *length <= MAX_MESSAGE)
        .ok_or_else(|| invalid("missing or excessive Content-Length"))?;
    let mut body = vec![0; length];
    input.read_exact(&mut body)?;
    serde_json::from_slice(&body)
        .map(Some)
        .map_err(|_| invalid("invalid JSON-RPC body"))
}

fn send(output: &mut impl Write, value: Value) -> io::Result<()> {
    let body = value.to_string();
    write!(output, "Content-Length: {}\r\n\r\n{body}", body.len())?;
    output.flush()
}

fn error(output: &mut impl Write, id: Value, code: i64, message: &str) -> io::Result<()> {
    send(
        output,
        json!({"jsonrpc":"2.0", "id":id, "error":{"code":code,"message":message}}),
    )
}

/// LSP columns use UTF-16 code units; graph spans use one-based Unicode scalar columns.
fn position(source: &str, line: usize, column: usize) -> Value {
    let character = source
        .lines()
        .nth(line)
        .unwrap_or("")
        .chars()
        .take(column)
        .map(char::len_utf16)
        .sum::<usize>();
    json!({"line":line,"character":character})
}

fn publish(output: &mut impl Write, uri: &str, version: i64, source: &str) -> io::Result<()> {
    let diagnostics: Vec<_> = super::diagnostics(source)
        .into_iter()
        .map(|diagnostic| {
            let line = diagnostic["line"].as_i64().unwrap_or(1).saturating_sub(1) as usize;
            let column = diagnostic["column"].as_i64().unwrap_or(1).saturating_sub(1) as usize;
            let start = position(source, line, column);
            json!({"range":{"start":start,"end":position(source,line,column+1)},
            "severity":1,"source":"helix","code":diagnostic["kind"].as_str().unwrap_or("parse"),
            "message":diagnostic["message"].as_str().unwrap_or("invalid source")})
        })
        .collect();
    send(
        output,
        json!({"jsonrpc":"2.0","method":"textDocument/publishDiagnostics",
        "params":{"uri":uri,"version":version,"diagnostics":diagnostics}}),
    )
}

/// Run the stdio server. stdout is reserved exclusively for framed JSON-RPC messages.
pub fn serve() -> io::Result<()> {
    let input = io::stdin();
    let output = io::stdout();
    session(&mut input.lock(), &mut output.lock())
}

fn session(input: &mut impl BufRead, output: &mut impl Write) -> io::Result<()> {
    let mut initialized = false;
    let mut shutdown = false;
    let mut documents: HashMap<String, (i64, String)> = HashMap::new();
    while let Some(message) = read_message(input)? {
        let id = message.get("id").cloned();
        let method = message["method"].as_str().unwrap_or("");
        if message["jsonrpc"] != "2.0" || method.is_empty() {
            error(
                output,
                id.unwrap_or(Value::Null),
                -32600,
                "invalid JSON-RPC request",
            )?;
            continue;
        }
        if method == "exit" {
            return if shutdown {
                Ok(())
            } else {
                Err(invalid("exit before shutdown"))
            };
        }
        if shutdown {
            if let Some(id) = id {
                error(output, id, -32600, "server has shut down")?;
            }
            continue;
        }
        if method == "initialize" {
            if let Some(id) = id {
                if initialized {
                    error(output, id, -32600, "already initialized")?;
                    continue;
                }
                initialized = true;
                send(
                    output,
                    json!({"jsonrpc":"2.0","id":id,"result":{
                    "capabilities":{"positionEncoding":"utf-16","textDocumentSync":{"openClose":true,"change":1},"documentFormattingProvider":true},
                    "serverInfo":{"name":"helix","version":env!("CARGO_PKG_VERSION")}}}),
                )?;
            }
            continue;
        }
        if !initialized {
            if let Some(id) = id {
                error(output, id, -32002, "server not initialized")?;
            }
            continue;
        }
        let params = &message["params"];
        let document = &params["textDocument"];
        let uri = document["uri"].as_str().unwrap_or("");
        match method {
            "shutdown" => {
                if let Some(id) = id {
                    shutdown = true;
                    send(output, json!({"jsonrpc":"2.0","id":id,"result":null}))?;
                }
            }
            "initialized" | "$/cancelRequest" | "$/setTrace" => {}
            "textDocument/didOpen" | "textDocument/didChange" => {
                let text = if method.ends_with("didOpen") {
                    document["text"].as_str()
                } else {
                    params["contentChanges"]
                        .as_array()
                        .filter(|changes| {
                            changes.iter().all(|change| change.get("range").is_none())
                        })
                        .and_then(|changes| changes.last())
                        .and_then(|change| change["text"].as_str())
                };
                if let (Some(text), Some(version)) = (text, document["version"].as_i64()) {
                    if uri.is_empty() {
                        continue;
                    }
                    if method.ends_with("didChange")
                        && documents.get(uri).is_none_or(|(old, _)| version <= *old)
                    {
                        continue;
                    }
                    if !documents.contains_key(uri) && documents.len() >= MAX_DOCUMENTS {
                        return Err(invalid("too many open documents"));
                    }
                    let total: usize = documents
                        .iter()
                        .filter(|(key, _)| key.as_str() != uri)
                        .map(|(_, (_, text))| text.len())
                        .sum();
                    if total + text.len() > MAX_MESSAGE {
                        return Err(invalid("open documents exceed memory limit"));
                    }
                    documents.insert(uri.into(), (version, text.into()));
                    publish(output, uri, version, text)?;
                }
            }
            "textDocument/didClose" => {
                documents.remove(uri);
                send(
                    output,
                    json!({"jsonrpc":"2.0","method":"textDocument/publishDiagnostics","params":{"uri":uri,"diagnostics":[]}}),
                )?;
            }
            "textDocument/formatting" => {
                if let Some(id) = id {
                    if let Some((_, source)) = documents.get(uri) {
                        match super::format(source) {
                            Ok(text) => {
                                let line = source.bytes().filter(|byte| *byte == b'\n').count();
                                let column = source
                                    .rsplit('\n')
                                    .next()
                                    .unwrap_or("")
                                    .encode_utf16()
                                    .count();
                                send(
                                    output,
                                    json!({"jsonrpc":"2.0","id":id,"result":[{"range":{"start":{"line":0,"character":0},"end":{"line":line,"character":column}},"newText":text}]}),
                                )?;
                            }
                            Err(message) => error(output, id, -32602, &message)?,
                        }
                    } else {
                        error(output, id, -32602, "document is not open")?;
                    }
                }
            }
            _ => {
                if let Some(id) = id {
                    error(output, id, -32601, "method not supported")?;
                }
            }
        }
    }
    Ok(())
}

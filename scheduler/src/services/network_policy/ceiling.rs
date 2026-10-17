// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

//! The execution's network ceiling: wire form, validation and storage columns.

use serde_json::{Map, Value, json};

use super::entry::{Entry, EntryError, check_authorable};
use crate::api::problem::{Problem, ProblemCode};
use crate::error::SchedulerError;

/// Network mode of a sandboxed ceiling.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    None,
    Allowlist,
    Egress,
}

impl Mode {
    pub fn as_str(&self) -> &'static str {
        match self {
            Mode::None => "none",
            Mode::Allowlist => "allowlist",
            Mode::Egress => "egress",
        }
    }

    fn parse(s: &str) -> Option<Mode> {
        match s {
            "none" => Some(Mode::None),
            "allowlist" => Some(Mode::Allowlist),
            "egress" => Some(Mode::Egress),
            _ => None,
        }
    }
}

/// A network mode and its entries: canonical, sorted, deduplicated.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Policy {
    mode: Mode,
    entries: Vec<Entry>,
}

/// An execution's network ceiling.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Ceiling {
    Unsandboxed,
    Sandboxed(Policy),
}

/// A refused ceiling: a one-sentence detail and every refused entry.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CeilingError {
    pub detail: String,
    pub entries: Vec<EntryError>,
}

impl CeilingError {
    pub(crate) fn shape(detail: impl Into<String>) -> Self {
        Self {
            detail: detail.into(),
            entries: Vec::new(),
        }
    }

    /// The `request.invalid` problem for this refusal.
    pub fn into_problem(self) -> Problem {
        let mut problem = Problem::new(ProblemCode::RequestInvalid).with_detail(self.detail);
        if !self.entries.is_empty() {
            let entries: Vec<Value> = self
                .entries
                .into_iter()
                .map(|e| json!({ "entry": e.entry, "reason": e.reason }))
                .collect();
            problem = problem.with_extension("entries", entries);
        }
        problem
    }
}

impl From<CeilingError> for SchedulerError {
    fn from(e: CeilingError) -> Self {
        SchedulerError::Problem(Box::new(e.into_problem()))
    }
}

const ENTRIES_SHAPE: &str = "network.entries must be an array of strings";

/// Parse a list of authored entries, reporting every refused one as written.
pub fn parse_authored_entries(raw: &[Value], shape: &str) -> Result<Vec<Entry>, CeilingError> {
    let mut entries = Vec::with_capacity(raw.len());
    let mut refused = Vec::new();
    for value in raw {
        let Some(text) = value.as_str() else {
            return Err(CeilingError::shape(shape));
        };
        let checked = Entry::parse(text).and_then(|entry| match check_authorable(&entry) {
            Ok(()) => Ok(entry),
            Err(reason) => Err(EntryError {
                entry: text.to_string(),
                reason: reason.to_string(),
            }),
        });
        match checked {
            Ok(entry) => entries.push(entry),
            Err(e) => refused.push(e),
        }
    }
    if !refused.is_empty() {
        let detail = if refused.len() == 1 {
            "1 network entry is invalid".to_string()
        } else {
            format!("{} network entries are invalid", refused.len())
        };
        return Err(CeilingError {
            detail,
            entries: refused,
        });
    }
    Ok(canonical_order(entries))
}

fn canonical_order(entries: Vec<Entry>) -> Vec<Entry> {
    let mut keyed: Vec<(String, Entry)> = entries.into_iter().map(|e| (e.to_string(), e)).collect();
    keyed.sort_by(|a, b| a.0.cmp(&b.0));
    keyed.dedup_by(|a, b| a.0 == b.0);
    keyed.into_iter().map(|(_, e)| e).collect()
}

impl Ceiling {
    /// Validate a ceiling as authored on the wire.
    pub fn parse_authored(value: &Value) -> Result<Ceiling, CeilingError> {
        let Some(obj) = value.as_object() else {
            return Err(CeilingError::shape("network must be an object with a kind"));
        };
        let kind = match obj.get("kind") {
            None => return Err(CeilingError::shape("network.kind is required")),
            Some(Value::String(k)) => k.as_str(),
            Some(_) => {
                return Err(CeilingError::shape(
                    "network.kind must be \"unsandboxed\" or \"sandboxed\"",
                ));
            }
        };
        match kind {
            "unsandboxed" => {
                if obj.len() > 1 {
                    return Err(CeilingError::shape(
                        "a network of kind unsandboxed takes no other fields",
                    ));
                }
                Ok(Ceiling::Unsandboxed)
            }
            "sandboxed" => parse_sandboxed(obj).map(Ceiling::Sandboxed),
            _ => Err(CeilingError::shape(
                "network.kind must be \"unsandboxed\" or \"sandboxed\"",
            )),
        }
    }

    /// The wire form.
    pub fn to_wire(&self) -> Value {
        match self {
            Ceiling::Unsandboxed => json!({ "kind": "unsandboxed" }),
            Ceiling::Sandboxed(p) => json!({
                "kind": "sandboxed",
                "mode": p.mode.as_str(),
                "entries": p.entries.iter().map(|e| e.to_string()).collect::<Vec<_>>(),
            }),
        }
    }

    /// The `ceiling_mode` and `ceiling_entries` column values.
    pub fn to_columns(&self) -> (&'static str, String) {
        match self {
            Ceiling::Unsandboxed => ("unsandboxed", "[]".to_string()),
            Ceiling::Sandboxed(p) => {
                let entries: Vec<String> = p.entries.iter().map(|e| e.to_string()).collect();
                (
                    p.mode.as_str(),
                    serde_json::to_string(&entries).unwrap_or_else(|_| "[]".to_string()),
                )
            }
        }
    }

    /// Read a ceiling from its columns. Anything but the exact stored form is an error.
    pub fn from_columns(mode: &str, entries: &str) -> Result<Ceiling, SchedulerError> {
        let malformed = || {
            SchedulerError::Database(format!(
                "malformed ceiling in DB: mode {mode:?}, entries {entries:?}"
            ))
        };
        let texts: Vec<String> = serde_json::from_str(entries).map_err(|_| malformed())?;
        if mode == "unsandboxed" {
            return if texts.is_empty() {
                Ok(Ceiling::Unsandboxed)
            } else {
                Err(malformed())
            };
        }
        let mode = Mode::parse(mode).ok_or_else(malformed)?;
        let mut parsed = Vec::with_capacity(texts.len());
        for text in &texts {
            let entry = Entry::parse(text).map_err(|_| malformed())?;
            if entry.to_string() != *text || check_authorable(&entry).is_err() {
                return Err(malformed());
            }
            parsed.push(entry);
        }
        let ordered = canonical_order(parsed.clone());
        if ordered != parsed || (mode == Mode::None && !parsed.is_empty()) {
            return Err(malformed());
        }
        Ok(Ceiling::Sandboxed(Policy {
            mode,
            entries: parsed,
        }))
    }
}

fn parse_sandboxed(obj: &Map<String, Value>) -> Result<Policy, CeilingError> {
    if let Some(key) = obj
        .keys()
        .find(|k| !matches!(k.as_str(), "kind" | "mode" | "entries"))
    {
        return Err(CeilingError::shape(format!("unknown network field: {key}")));
    }
    let mode = obj
        .get("mode")
        .and_then(Value::as_str)
        .and_then(Mode::parse)
        .ok_or_else(|| {
            CeilingError::shape("network.mode must be one of none, allowlist, egress")
        })?;
    let raw: &[Value] = match obj.get("entries") {
        None if mode == Mode::Allowlist => {
            return Err(CeilingError::shape("allowlist requires entries"));
        }
        None => &[],
        Some(Value::Array(items)) => items,
        Some(_) => return Err(CeilingError::shape(ENTRIES_SHAPE)),
    };
    if mode == Mode::None && !raw.is_empty() {
        return Err(CeilingError::shape("mode none takes no entries"));
    }
    let entries = parse_authored_entries(raw, ENTRIES_SHAPE)?;
    Ok(Policy { mode, entries })
}

// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

//! Agent `sandbox_config`: `{"network_endpoints": [<entries>]}`.

use serde_json::{Value, json};

use super::ceiling::{CeilingError, parse_authored_entries};

const SHAPE: &str = "sandbox_config must be null or {\"network_endpoints\": [...]}";
const ENDPOINTS_SHAPE: &str = "sandbox_config.network_endpoints must be an array of strings";

/// Validate an agent's `sandbox_config` and return its canonical form.
pub fn validate_sandbox_config(value: &Value) -> Result<Value, CeilingError> {
    let fields = value
        .as_object()
        .filter(|f| f.len() == 1)
        .ok_or_else(|| CeilingError::shape(SHAPE))?;
    let raw = match fields.get("network_endpoints") {
        Some(Value::Array(items)) => items,
        Some(_) => return Err(CeilingError::shape(ENDPOINTS_SHAPE)),
        None => return Err(CeilingError::shape(SHAPE)),
    };
    let entries = parse_authored_entries(raw, ENDPOINTS_SHAPE)?;
    let endpoints: Vec<String> = entries.iter().map(|e| e.to_string()).collect();
    Ok(json!({ "network_endpoints": endpoints }))
}

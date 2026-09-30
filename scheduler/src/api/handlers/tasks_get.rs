// SPDX-FileCopyrightText: Copyright 2025 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

use serde_json::Value as JsonValue;

use crate::api::jsonrpc::{JsonRpcError, JsonRpcResponse};
use crate::app::AppState;

/// Handle tasks/get JSON-RPC method
///
/// Stubbed: will be rewritten when new REST endpoints are built.
pub async fn handle_tasks_get(
    _state: &AppState,
    _params: JsonValue,
    id: Option<JsonValue>,
) -> JsonRpcResponse {
    JsonRpcResponse::error(id, JsonRpcError::internal_error("not implemented"))
}

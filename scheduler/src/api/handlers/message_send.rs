// SPDX-FileCopyrightText: Copyright 2025 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

use serde_json::Value as JsonValue;

use crate::api::jsonrpc::{JsonRpcError, JsonRpcResponse};
use crate::app::AppState;

/// Handle message/send JSON-RPC method (not implemented).
pub async fn handle_message_send(
    _state: &AppState,
    _params: JsonValue,
    id: Option<JsonValue>,
) -> JsonRpcResponse {
    JsonRpcResponse::error(id, JsonRpcError::internal_error("not implemented"))
}

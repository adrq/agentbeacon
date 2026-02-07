use serde_json::Value as JsonValue;

use crate::api::jsonrpc::{JsonRpcError, JsonRpcResponse};
use crate::app::AppState;

/// Handle message/send JSON-RPC method
///
/// Stubbed: A2A message/send is being redesigned for session-based coordination model.
/// Stubbed: will be reimplemented with MCP coordination server.
pub async fn handle_message_send(
    _state: &AppState,
    _params: JsonValue,
    id: Option<JsonValue>,
) -> JsonRpcResponse {
    JsonRpcResponse::error(
        id,
        JsonRpcError::internal_error(
            "not implemented — A2A message/send being redesigned for session model".to_string(),
        ),
    )
}

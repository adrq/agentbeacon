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
    JsonRpcResponse::error(
        id,
        JsonRpcError::internal_error(
            "not implemented — tasks/get being redesigned for session model".to_string(),
        ),
    )
}

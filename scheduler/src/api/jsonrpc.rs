use axum::{
    Json, Router,
    extract::State,
    http::StatusCode,
    response::{IntoResponse, Response},
    routing::post,
};
use serde::{Deserialize, Serialize};
use serde_json::{Value as JsonValue, json};

use crate::app::AppState;

/// JSON-RPC 2.0 Request structure
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JsonRpcRequest {
    pub jsonrpc: String,
    pub method: String,
    #[serde(default)]
    pub params: Option<JsonValue>,
    pub id: Option<JsonValue>,
}

/// JSON-RPC 2.0 Response structure
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JsonRpcResponse {
    pub jsonrpc: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<JsonValue>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<JsonRpcError>,
    pub id: Option<JsonValue>,
}

/// JSON-RPC 2.0 Error structure
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JsonRpcError {
    pub code: i32,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<JsonValue>,
}

impl JsonRpcError {
    /// Parse error (-32700)
    pub fn parse_error(data: Option<JsonValue>) -> Self {
        Self {
            code: -32700,
            message: "Parse error".to_string(),
            data,
        }
    }

    /// Invalid Request (-32600)
    pub fn invalid_request(data: Option<JsonValue>) -> Self {
        Self {
            code: -32600,
            message: "Invalid Request".to_string(),
            data,
        }
    }

    /// Method not found (-32601)
    pub fn method_not_found(method: &str) -> Self {
        Self {
            code: -32601,
            message: "Method not found".to_string(),
            data: Some(json!({"method": method})),
        }
    }

    /// Invalid params (-32602)
    pub fn invalid_params(data: Option<JsonValue>) -> Self {
        Self {
            code: -32602,
            message: "Invalid params".to_string(),
            data,
        }
    }

    /// Internal error (-32603)
    pub fn internal_error(message: String) -> Self {
        Self {
            code: -32603,
            message: "Internal error".to_string(),
            data: Some(json!({"detail": message})),
        }
    }
}

impl JsonRpcResponse {
    /// Create success response
    pub fn success(id: Option<JsonValue>, result: JsonValue) -> Self {
        Self {
            jsonrpc: "2.0".to_string(),
            result: Some(result),
            error: None,
            id,
        }
    }

    /// Create error response
    pub fn error(id: Option<JsonValue>, error: JsonRpcError) -> Self {
        Self {
            jsonrpc: "2.0".to_string(),
            result: None,
            error: Some(error),
            id,
        }
    }
}

impl IntoResponse for JsonRpcResponse {
    fn into_response(self) -> Response {
        (StatusCode::OK, Json(self)).into_response()
    }
}

/// JSON-RPC endpoint handler
async fn handle_jsonrpc(
    State(_state): State<AppState>,
    body: String,
) -> Result<JsonRpcResponse, JsonRpcResponse> {
    // Parse JSON
    let request: JsonRpcRequest = match serde_json::from_str(&body) {
        Ok(req) => req,
        Err(e) => {
            return Err(JsonRpcResponse::error(
                None,
                JsonRpcError::parse_error(Some(json!({"error": e.to_string()}))),
            ));
        }
    };

    // Validate JSON-RPC 2.0 format
    if request.jsonrpc != "2.0" {
        return Err(JsonRpcResponse::error(
            request.id,
            JsonRpcError::invalid_request(Some(json!({
                "error": "jsonrpc field must be '2.0'"
            }))),
        ));
    }

    // Route to method handlers
    let params = request.params.unwrap_or_else(|| json!({}));

    match request.method.as_str() {
        "message/send" => {
            let response =
                crate::api::handlers::handle_message_send(&_state, params, request.id.clone())
                    .await;
            Ok(response)
        }
        "tasks/get" => {
            let response =
                crate::api::handlers::handle_tasks_get(&_state, params, request.id.clone()).await;
            Ok(response)
        }
        _ => Err(JsonRpcResponse::error(
            request.id,
            JsonRpcError::method_not_found(&request.method),
        )),
    }
}

/// Create JSON-RPC router
pub fn routes() -> Router<AppState> {
    Router::new().route("/rpc", post(handle_jsonrpc))
}

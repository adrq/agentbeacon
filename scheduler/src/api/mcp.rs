use axum::{
    Router,
    extract::{Request, State},
    http::StatusCode,
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::post,
};
use serde_json::json;

use crate::api::auth::McpSession;
use crate::api::jsonrpc::{JsonRpcError, JsonRpcRequest, JsonRpcResponse};
use crate::app::AppState;

pub const MCP_PROTOCOL_VERSION: &str = "2025-11-25";

/// Validate transport-level headers per MCP 2025-11-25 spec.
/// Runs before auth extraction so DNS rebinding attacks are blocked early.
///
/// **Design note (intentional deviation):** The MCP spec uses MCP-Session-Id as
/// the primary session identity mechanism. We use Bearer token auth instead,
/// where the token IS the session UUID. This is simpler and sufficient for our
/// architecture where the scheduler controls session creation and token issuance.
/// As an interop safety measure, if a client sends MCP-Session-Id and it doesn't
/// match the Bearer token, we reject with 400 to catch misconfigured clients.
async fn validate_mcp_transport(request: Request, next: Next) -> Response {
    // Origin validation: MUST reject invalid origins with 403
    if let Some(origin) = request.headers().get("origin") {
        let origin_str = origin.to_str().unwrap_or("");
        if !is_valid_origin(origin_str) {
            return StatusCode::FORBIDDEN.into_response();
        }
    }

    // MCP-Protocol-Version: MUST reject invalid versions with 400
    if let Some(version) = request.headers().get("mcp-protocol-version") {
        let version_str = version.to_str().unwrap_or("");
        if version_str != MCP_PROTOCOL_VERSION {
            return StatusCode::BAD_REQUEST.into_response();
        }
    }

    // MCP-Session-Id mismatch: if present, must match Bearer token
    if let Some(mcp_sid) = request.headers().get("mcp-session-id")
        && let Some(auth_header) = request.headers().get("authorization")
    {
        let auth_str = auth_header.to_str().unwrap_or("");
        // RFC 7235: auth schemes are case-insensitive
        let bearer_token = if auth_str.len() > 7 && auth_str[..7].eq_ignore_ascii_case("bearer ") {
            &auth_str[7..]
        } else {
            ""
        };
        let mcp_sid_str = mcp_sid.to_str().unwrap_or("");
        if !bearer_token.is_empty() && !mcp_sid_str.is_empty() && bearer_token != mcp_sid_str {
            return StatusCode::BAD_REQUEST.into_response();
        }
    }

    next.run(request).await
}

fn is_valid_origin(origin: &str) -> bool {
    let lower = origin.to_lowercase();
    // Check scheme+host, then verify next char is a boundary (port, path, query, or end)
    // to prevent prefix-match bypasses like http://localhost.evil.com
    let prefixes = [
        "http://localhost",
        "https://localhost",
        "http://127.0.0.1",
        "https://127.0.0.1",
        "http://[::1]",
        "https://[::1]",
    ];
    prefixes.iter().any(|prefix| {
        if let Some(rest) = lower.strip_prefix(prefix) {
            rest.is_empty()
                || rest.starts_with(':')
                || rest.starts_with('/')
                || rest.starts_with('?')
        } else {
            false
        }
    })
}

/// MCP endpoint handler (POST /mcp)
///
/// Accepts JSON-RPC 2.0 requests with Bearer token auth (session_id).
/// Dispatches to MCP method handlers (initialize, tools/list, tools/call, ping).
async fn handle_mcp(auth: McpSession, State(state): State<AppState>, body: String) -> Response {
    let request: JsonRpcRequest = match serde_json::from_str(&body) {
        Ok(req) => req,
        Err(e) => {
            return JsonRpcResponse::error(
                None,
                JsonRpcError::parse_error(Some(json!({"detail": e.to_string()}))),
            )
            .into_response();
        }
    };

    if request.jsonrpc != "2.0" {
        return JsonRpcResponse::error(
            request.id,
            JsonRpcError::invalid_request("jsonrpc field must be '2.0'"),
        )
        .into_response();
    }

    // Notifications have no id — accept silently, return 202 per spec
    if request.id.is_none() {
        return StatusCode::ACCEPTED.into_response();
    }

    match request.method.as_str() {
        "initialize" => {
            let result = json!({
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": "agentbeacon",
                    "version": "0.1.0",
                    "title": "AgentBeacon Coordination Server"
                }
            });
            let mut response = JsonRpcResponse::success(request.id, result).into_response();
            // MCP-Session-Id header per spec
            if let Ok(val) = auth.session_id.parse() {
                response.headers_mut().insert("mcp-session-id", val);
            }
            response
        }
        "ping" => JsonRpcResponse::success(request.id, json!({})).into_response(),
        "tools/list" => crate::api::mcp_tools::handle_tools_list(&auth, request.id).into_response(),
        "tools/call" => {
            let params = request.params.unwrap_or_else(|| json!({}));
            match crate::api::mcp_tools::handle_tools_call(&auth, &state, params).await {
                Ok(result) => JsonRpcResponse::success(request.id, result).into_response(),
                Err(err) => JsonRpcResponse::error(request.id, err).into_response(),
            }
        }
        _ => JsonRpcResponse::error(request.id, JsonRpcError::method_not_found(&request.method))
            .into_response(),
    }
}

/// Explicit 405 for GET and DELETE on /mcp per Streamable HTTP spec
async fn handle_mcp_method_not_allowed() -> impl IntoResponse {
    StatusCode::METHOD_NOT_ALLOWED
}

pub fn routes() -> Router<AppState> {
    Router::new()
        .route(
            "/mcp",
            post(handle_mcp)
                .get(handle_mcp_method_not_allowed)
                .delete(handle_mcp_method_not_allowed),
        )
        .layer(middleware::from_fn(validate_mcp_transport))
}

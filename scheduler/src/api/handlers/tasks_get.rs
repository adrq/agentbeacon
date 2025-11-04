use serde::{Deserialize, Serialize};
use serde_json::{Value as JsonValue, json};
use uuid::Uuid;

use crate::api::jsonrpc::{JsonRpcError, JsonRpcResponse};
use crate::app::AppState;
use crate::db::executions;

/// TasksGetParams per A2A v0.3.0 protocol
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TasksGetParams {
    pub execution_id: String,
}

/// TasksGetResult response
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TasksGetResult {
    pub execution_id: String,
    pub status: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub workflow_ref: Option<String>,
    pub created_at: String,
    pub updated_at: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub completed_at: Option<String>,
    pub task_states: JsonValue,
}

/// Handle tasks/get JSON-RPC method (FR-004)
pub async fn handle_tasks_get(
    state: &AppState,
    params: JsonValue,
    id: Option<JsonValue>,
) -> JsonRpcResponse {
    // Parse params
    let params: TasksGetParams = match serde_json::from_value(params) {
        Ok(p) => p,
        Err(e) => {
            return JsonRpcResponse::error(
                id,
                JsonRpcError::invalid_params(Some(json!({
                    "error": format!("parse params failed: {e}")
                }))),
            );
        }
    };

    // Parse execution_id as UUID
    let execution_uuid = match Uuid::parse_str(&params.execution_id) {
        Ok(uuid) => uuid,
        Err(e) => {
            return JsonRpcResponse::error(
                id,
                JsonRpcError::invalid_params(Some(json!({
                    "error": format!("parse execution ID failed: {e}")
                }))),
            );
        }
    };

    // Query execution by ID
    let execution = match executions::get_by_id(&state.db_pool, &execution_uuid).await {
        Ok(exec) => exec,
        Err(crate::error::SchedulerError::NotFound(_)) => {
            // FR-036: Return JSON-RPC error for nonexistent execution
            return JsonRpcResponse::error(
                id,
                JsonRpcError::invalid_params(Some(json!({
                    "error": format!("execution not found: {}", params.execution_id)
                }))),
            );
        }
        Err(e) => {
            return JsonRpcResponse::error(
                id,
                JsonRpcError::internal_error(format!("Failed to query execution: {e}")),
            );
        }
    };

    // Build workflowRef if registry metadata is available
    let workflow_ref = if let (Some(namespace), Some(version)) =
        (&execution.workflow_namespace, &execution.workflow_version)
    {
        // Need workflow name to build full ref - fetch workflow
        match crate::db::workflows::get_by_id(&state.db_pool, &execution.workflow_id).await {
            Ok(workflow) => Some(format!("{}:{}@{}", namespace, workflow.name, version)),
            Err(_) => None,
        }
    } else {
        None
    };

    // Return execution details (FR-019: workflow-level AND node-level status)
    JsonRpcResponse::success(
        id,
        json!(TasksGetResult {
            execution_id: execution.id.to_string(),
            status: execution.status.clone(),
            workflow_ref,
            created_at: execution.created_at.to_rfc3339(),
            updated_at: execution.updated_at.to_rfc3339(),
            completed_at: execution.completed_at.map(|dt| dt.to_rfc3339()),
            task_states: execution.task_states,
        }),
    )
}

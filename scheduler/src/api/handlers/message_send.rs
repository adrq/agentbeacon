use serde::{Deserialize, Serialize};
use serde_json::{Value as JsonValue, json};
use uuid::Uuid;

use crate::api::jsonrpc::{JsonRpcError, JsonRpcResponse};
use crate::app::AppState;
use crate::db::{Workflow, executions, workflows};
use common::dag::WorkflowDAG;

/// MessageSendParams per A2A v0.3.0 protocol
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MessageSendParams {
    /// Inline workflow YAML (XOR with workflowRef)
    #[serde(skip_serializing_if = "Option::is_none")]
    pub workflow_yaml: Option<String>,
    /// Registry reference (e.g., "team/auth:v1.2.3") - requires T013
    #[serde(skip_serializing_if = "Option::is_none")]
    pub workflow_ref: Option<String>,
    /// Optional context grouping identifier
    #[serde(skip_serializing_if = "Option::is_none")]
    pub context_id: Option<String>,
}

/// MessageSendResult response
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MessageSendResult {
    pub execution_id: String,
    pub status: String,
    pub message: String,
}

/// Handle message/send JSON-RPC method
pub async fn handle_message_send(
    state: &AppState,
    params: JsonValue,
    id: Option<JsonValue>,
) -> JsonRpcResponse {
    // Parse params
    let params: MessageSendParams = match serde_json::from_value(params) {
        Ok(p) => p,
        Err(e) => {
            return JsonRpcResponse::error(
                id,
                JsonRpcError::invalid_params(Some(json!({
                    "error": format!("Failed to parse params: {e}")
                }))),
            );
        }
    };

    // Validate XOR: exactly one of workflowYaml or workflowRef must be provided
    match (&params.workflow_yaml, &params.workflow_ref) {
        (Some(_), Some(_)) => {
            return JsonRpcResponse::error(
                id,
                JsonRpcError::invalid_params(Some(json!({
                    "error": "Exactly one of workflowYaml or workflowRef must be provided"
                }))),
            );
        }
        (None, None) => {
            return JsonRpcResponse::error(
                id,
                JsonRpcError::invalid_params(Some(json!({
                    "error": "Either workflowYaml or workflowRef must be provided"
                }))),
            );
        }
        _ => {}
    }

    // Handle workflowRef path (requires T013 - Phase 6)
    if let Some(workflow_ref) = &params.workflow_ref {
        return JsonRpcResponse::error(
            id,
            JsonRpcError::internal_error(format!(
                "workflowRef resolution not yet implemented (T013 pending). Provided: {workflow_ref}"
            )),
        );
    }

    // Handle inline YAML path
    let workflow_yaml = params.workflow_yaml.unwrap(); // Safe due to XOR validation above

    // Validate workflow against schema (FR-006)
    let workflow_json = match state.validator.validate_workflow_yaml(&workflow_yaml) {
        Ok(json) => json,
        Err(e) => {
            return JsonRpcResponse::error(
                id,
                JsonRpcError::invalid_params(Some(json!({
                    "error": format!("Workflow schema validation failed: {e}")
                }))),
            );
        }
    };

    // Extract workflow name for database storage
    let workflow_name = workflow_json
        .get("name")
        .and_then(|v| v.as_str())
        .unwrap_or("unnamed-workflow")
        .to_string();

    // Build WorkflowDAG and detect cycles (FR-014)
    let _dag = match WorkflowDAG::from_workflow(&workflow_yaml) {
        Ok(dag) => dag,
        Err(e) => {
            return JsonRpcResponse::error(
                id,
                JsonRpcError::invalid_params(Some(json!({
                    "error": format!("Invalid workflow structure: {e}")
                }))),
            );
        }
    };
    // DAG is validated here; actual usage for task queueing happens in Phase 4 (T016-T017)

    // Create workflow record in database
    let workflow_id = Uuid::new_v4();
    let workflow = Workflow {
        id: workflow_id,
        name: workflow_name.clone(),
        description: workflow_json
            .get("description")
            .and_then(|v| v.as_str())
            .map(String::from),
        yaml_content: workflow_yaml.clone(),
        created_at: chrono::Utc::now(),
        updated_at: chrono::Utc::now(),
    };

    if let Err(e) = workflows::create(&state.db_pool, &workflow).await {
        return JsonRpcResponse::error(
            id,
            JsonRpcError::internal_error(format!("Failed to create workflow: {e}")),
        );
    }

    // Create execution record in database
    let task_states = json!({}); // Empty initially, will be populated as tasks execute
    let execution_id = match executions::create(&state.db_pool, &workflow_id, task_states).await {
        Ok(exec_id) => exec_id,
        Err(e) => {
            return JsonRpcResponse::error(
                id,
                JsonRpcError::internal_error(format!("Failed to create execution: {e}")),
            );
        }
    };

    // TODO (Phase 4 - T016-T017): Queue entry nodes to TaskQueue
    // For now, execution is created but tasks won't be queued until scheduler integration
    // Entry nodes: dag.entry_nodes()
    // Queue each: task_queue.push(build_task_assignment(...))

    // Return success response (non-blocking)
    JsonRpcResponse::success(
        id,
        json!(MessageSendResult {
            execution_id: execution_id.to_string(),
            status: "pending".to_string(),
            message: "Workflow submitted successfully".to_string(),
        }),
    )
}

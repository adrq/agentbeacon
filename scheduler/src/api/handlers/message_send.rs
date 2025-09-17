use serde::{Deserialize, Serialize};
use serde_json::{Value as JsonValue, json};
use uuid::Uuid;

use crate::api::jsonrpc::{JsonRpcError, JsonRpcResponse};
use crate::app::AppState;
use crate::db::{Workflow, executions, workflow_version, workflows};
use crate::error::SchedulerError;
use common::dag::WorkflowDAG;

/// A2A v0.3.0 Message object
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct Message {
    pub role: String, // "user" | "agent"
    pub parts: Vec<Part>,
    pub message_id: String,
    pub kind: String, // Always "message"
    #[serde(skip_serializing_if = "Option::is_none")]
    pub context_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub task_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub metadata: Option<JsonValue>,
}

/// A2A v0.3.0 Part (discriminated union)
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub enum Part {
    Text {
        text: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        metadata: Option<JsonValue>,
    },
    Data {
        data: JsonValue,
        #[serde(skip_serializing_if = "Option::is_none")]
        metadata: Option<JsonValue>,
    },
    File {
        #[serde(rename = "fileUri")]
        file_uri: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        metadata: Option<JsonValue>,
    },
}

/// A2A v0.3.0 MessageSendConfiguration (optional)
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MessageSendConfiguration {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub accepted_output_modes: Option<Vec<String>>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub history_length: Option<i32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub blocking: Option<bool>,
}

/// A2A v0.3.0 MessageSendParams
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MessageSendParams {
    pub message: Message, // REQUIRED per A2A spec
    #[serde(skip_serializing_if = "Option::is_none")]
    pub configuration: Option<MessageSendConfiguration>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub metadata: Option<JsonValue>,
}

/// MessageSendResult response
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct MessageSendResult {
    pub execution_id: String,
    pub status: String,
    pub message: String,
}

/// Extract workflowRef or workflowYaml from A2A Message parts
fn extract_workflow_params(message: &Message) -> Result<(Option<String>, Option<String>), String> {
    for part in &message.parts {
        if let Part::Data { data, .. } = part {
            // Try direct access: data.workflowRef or data.workflowYaml
            if let Some(workflow_ref) = data.get("workflowRef").and_then(|v| v.as_str()) {
                return Ok((Some(workflow_ref.to_string()), None));
            }
            if let Some(workflow_yaml) = data.get("workflowYaml").and_then(|v| v.as_str()) {
                return Ok((None, Some(workflow_yaml.to_string())));
            }

            // Also support nested format: data.data.workflowRef (for test compatibility)
            if let Some(nested_data) = data.get("data").and_then(|v| v.as_object()) {
                if let Some(workflow_ref) = nested_data.get("workflowRef").and_then(|v| v.as_str())
                {
                    return Ok((Some(workflow_ref.to_string()), None));
                }
                if let Some(workflow_yaml) =
                    nested_data.get("workflowYaml").and_then(|v| v.as_str())
                {
                    return Ok((None, Some(workflow_yaml.to_string())));
                }
            }
        }
    }
    Ok((None, None))
}

/// Parse workflowRef into (namespace, name, version)
///
/// Accepts formats:
/// - "namespace/name:version" - explicit version
/// - "namespace/name" - defaults to "latest"
fn parse_workflow_ref(ref_str: &str) -> Result<(String, String, String), SchedulerError> {
    // Parse "namespace/name:version" or "namespace/name"
    let parts: Vec<&str> = ref_str.split('/').collect();
    if parts.len() != 2 {
        return Err(SchedulerError::ValidationFailed(format!(
            "Invalid workflowRef format '{}', expected namespace/name[:version]",
            ref_str
        )));
    }

    let namespace = parts[0].to_string();
    let name_version: Vec<&str> = parts[1].split(':').collect();

    let (name, version) = match name_version.len() {
        1 => (name_version[0].to_string(), "latest".to_string()),
        2 => (name_version[0].to_string(), name_version[1].to_string()),
        _ => {
            return Err(SchedulerError::ValidationFailed(format!(
                "Invalid workflowRef format '{}', expected namespace/name[:version]",
                ref_str
            )));
        }
    };

    Ok((namespace, name, version))
}

/// Handle message/send JSON-RPC method
pub async fn handle_message_send(
    state: &AppState,
    params: JsonValue,
    id: Option<JsonValue>,
) -> JsonRpcResponse {
    // Parse A2A-compliant params
    let params: MessageSendParams = match serde_json::from_value(params) {
        Ok(p) => p,
        Err(e) => {
            return JsonRpcResponse::error(
                id,
                JsonRpcError::invalid_params(Some(json!({
                    "error": format!("Failed to parse MessageSendParams: {e}")
                }))),
            );
        }
    };

    // Extract workflowRef or workflowYaml from message.parts
    let (workflow_ref, workflow_yaml_inline) = match extract_workflow_params(&params.message) {
        Ok(params) => params,
        Err(e) => {
            return JsonRpcResponse::error(
                id,
                JsonRpcError::invalid_params(Some(json!({
                    "error": format!("Failed to extract workflow params: {e}")
                }))),
            );
        }
    };

    // XOR validation: exactly one must be present
    match (&workflow_ref, &workflow_yaml_inline) {
        (Some(_), Some(_)) => {
            return JsonRpcResponse::error(
                id,
                JsonRpcError::invalid_params(Some(json!({
                    "error": "Both workflowRef and workflowYaml provided, exactly one required"
                }))),
            );
        }
        (None, None) => {
            return JsonRpcResponse::error(
                id,
                JsonRpcError::invalid_params(Some(json!({
                    "error": "Either workflowRef or workflowYaml must be provided"
                }))),
            );
        }
        _ => {}
    }

    // Resolve workflow YAML (either from registry or inline)
    let (workflow_yaml, workflow_namespace, workflow_name, workflow_version_str) =
        if let Some(ref_str) = workflow_ref {
            // Parse and resolve workflowRef from registry
            let (namespace, name, version) = match parse_workflow_ref(&ref_str) {
                Ok(parts) => parts,
                Err(e) => {
                    return JsonRpcResponse::error(
                        id,
                        JsonRpcError::invalid_params(Some(json!({
                            "error": format!("Invalid workflowRef: {e}")
                        }))),
                    );
                }
            };

            // Query registry
            let wf_version =
                match workflow_version::get_by_ref(&state.db_pool, &namespace, &name, &version)
                    .await
                {
                    Ok(Some(wf)) => wf,
                    Ok(None) => {
                        return JsonRpcResponse::error(
                            id,
                            JsonRpcError::invalid_params(Some(json!({
                                "error": format!("Workflow not found in registry: {}", ref_str)
                            }))),
                        );
                    }
                    Err(e) => {
                        return JsonRpcResponse::error(
                            id,
                            JsonRpcError::internal_error(format!("Failed to query registry: {e}")),
                        );
                    }
                };

            (
                wf_version.yaml_snapshot,
                Some(namespace),
                Some(name),
                Some(version),
            )
        } else {
            (workflow_yaml_inline.unwrap(), None, None, None)
        };

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
    let workflow_name_from_yaml = workflow_json
        .get("name")
        .and_then(|v| v.as_str())
        .unwrap_or("unnamed-workflow")
        .to_string();

    // Use registry name if available, otherwise use YAML name
    let workflow_name_final = workflow_name.unwrap_or(workflow_name_from_yaml);

    // Build WorkflowDAG and detect cycles (FR-014)
    let dag = match WorkflowDAG::from_workflow(&workflow_yaml) {
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

    // Create workflow record in database
    let workflow_id = Uuid::new_v4();
    let workflow = Workflow {
        id: workflow_id,
        name: workflow_name_final.clone(),
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

    // Create execution record in database with registry metadata
    let task_states = json!({}); // Empty initially, will be populated as tasks execute
    let execution_id = match executions::create(
        &state.db_pool,
        &workflow_id,
        task_states,
        workflow_namespace,   // From registry: namespace
        workflow_version_str, // From registry: version
    )
    .await
    {
        Ok(exec_id) => exec_id,
        Err(e) => {
            return JsonRpcResponse::error(
                id,
                JsonRpcError::internal_error(format!("Failed to create execution: {e}")),
            );
        }
    };

    // Register execution with scheduler (enables event-driven DAG scheduling)
    if let Err(e) = state
        .scheduler
        .register_execution(execution_id.to_string(), dag)
        .await
    {
        return JsonRpcResponse::error(
            id,
            JsonRpcError::internal_error(format!("Failed to register execution: {e}")),
        );
    }

    // Queue entry nodes to TaskQueue (triggers worker task distribution)
    if let Err(e) = state
        .scheduler
        .queue_entry_nodes(&execution_id.to_string())
        .await
    {
        return JsonRpcResponse::error(
            id,
            JsonRpcError::internal_error(format!("Failed to queue entry nodes: {e}")),
        );
    }

    // Return A2A Task response (non-blocking)
    JsonRpcResponse::success(
        id,
        json!({
            "kind": "task",
            "id": execution_id.to_string(),
            "status": {
                "state": "pending",
                "timestamp": chrono::Utc::now().to_rfc3339()
            },
            "contextId": params.message.context_id,
            "history": [],
            "artifacts": []
        }),
    )
}

use axum::{Json, extract::State, http::StatusCode};
use serde::{Deserialize, Serialize};
use serde_json::Value as JsonValue;

use crate::app::AppState;
use crate::queue::TaskAssignment;

/// Worker sync request from worker to scheduler
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct WorkerSyncRequest {
    pub status: String, // "idle" or "working"
    #[serde(skip_serializing_if = "Option::is_none")]
    pub current_task: Option<CurrentTask>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub task_result: Option<TaskResult>,
}

/// Current task being worked on (heartbeat)
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CurrentTask {
    pub execution_id: String,
    pub node_id: String,
}

/// Task result reported by worker
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskResult {
    pub execution_id: String,
    pub node_id: String,
    pub task_status: TaskStatus,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub artifacts: Option<Vec<JsonValue>>,
}

/// A2A TaskStatus object
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskStatus {
    pub state: String, // "completed", "failed", etc.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<JsonValue>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub timestamp: Option<String>,
}

/// Worker sync response from scheduler to worker
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum WorkerSyncResponse {
    NoAction,
    TaskAssigned { task: TaskAssignment },
    Command { command: String },
}

/// Handle worker sync endpoint
///
/// Simplified: no Scheduler to dispatch to. If a worker reports a
/// task_result, log it and ignore. If idle, try to pop from queue. Otherwise NoAction.
pub async fn handle_worker_sync(
    State(state): State<AppState>,
    Json(request): Json<WorkerSyncRequest>,
) -> Result<Json<WorkerSyncResponse>, StatusCode> {
    // Handle task result if present — log and acknowledge
    if let Some(task_result) = request.task_result {
        tracing::info!(
            execution_id = %task_result.execution_id,
            node_id = %task_result.node_id,
            state = %task_result.task_status.state,
            "Received task result from worker (no scheduler to dispatch to)"
        );
        return Ok(Json(WorkerSyncResponse::NoAction));
    }

    // Assign task from queue if worker is idle
    if request.status == "idle" {
        match state.task_queue.pop().await {
            Ok(Some(task)) => {
                return Ok(Json(WorkerSyncResponse::TaskAssigned { task }));
            }
            Ok(None) => {
                return Ok(Json(WorkerSyncResponse::NoAction));
            }
            Err(e) => {
                tracing::error!("pop task from queue failed: {e}");
                return Err(StatusCode::INTERNAL_SERVER_ERROR);
            }
        }
    }

    // Worker is working — heartbeat acknowledgment
    Ok(Json(WorkerSyncResponse::NoAction))
}

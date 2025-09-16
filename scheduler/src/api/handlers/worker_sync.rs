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
pub async fn handle_worker_sync(
    State(state): State<AppState>,
    Json(request): Json<WorkerSyncRequest>,
) -> Result<Json<WorkerSyncResponse>, StatusCode> {
    // Handle task result if present
    if let Some(task_result) = request.task_result {
        handle_task_result(&state, task_result).await.map_err(|e| {
            eprintln!("Error handling task result: {e}");
            StatusCode::INTERNAL_SERVER_ERROR
        })?;
    }

    // Assign task from queue if worker is idle
    if request.status == "idle" {
        match state.task_queue.pop().await {
            Ok(Some(task)) => {
                // Update task state to "assigned" via scheduler (serializes with handle_task_result)
                // CRITICAL: This must succeed, otherwise we violate atomicity (task popped but not tracked)
                if let Err(e) = state
                    .scheduler
                    .mark_task_assigned(&task.execution_id, &task.node_id)
                    .await
                {
                    eprintln!("Error marking task as assigned: {e}");

                    // ATOMICITY FIX: Push task back to queue to prevent orphaning
                    if let Err(push_err) = state.task_queue.push(task.clone()).await {
                        eprintln!(
                            "CRITICAL: Failed to push task back to queue after mark_assigned failure: {push_err}"
                        );
                        eprintln!(
                            "Task {}/{} may be orphaned - manual recovery required",
                            task.execution_id, task.node_id
                        );
                    } else {
                        eprintln!(
                            "Task {}/{} pushed back to queue for retry",
                            task.execution_id, task.node_id
                        );
                    }

                    // Return error to worker (do not assign task)
                    return Err(StatusCode::INTERNAL_SERVER_ERROR);
                }

                return Ok(Json(WorkerSyncResponse::TaskAssigned { task }));
            }
            Ok(None) => {
                // Queue is empty - return no_action (FR-039)
                return Ok(Json(WorkerSyncResponse::NoAction));
            }
            Err(e) => {
                eprintln!("Error popping task from queue: {e}");
                return Err(StatusCode::INTERNAL_SERVER_ERROR);
            }
        }
    }

    // Worker is working - just heartbeat acknowledgment
    Ok(Json(WorkerSyncResponse::NoAction))
}

/// Handle task result from worker
async fn handle_task_result(
    state: &AppState,
    task_result: TaskResult,
) -> Result<(), Box<dyn std::error::Error>> {
    // Determine success based on task status state
    let success = task_result.task_status.state == "completed";

    // Update scheduler with task result (triggers event-driven scheduling)
    state
        .scheduler
        .handle_task_result(&task_result.execution_id, &task_result.node_id, success)
        .await?;

    Ok(())
}

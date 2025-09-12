use anyhow::{Context, Result};
use common::{A2AArtifact, A2ATaskStatus};
use serde::{Deserialize, Serialize};
use std::time::Duration;

#[derive(Debug, Clone)]
pub struct RetryConfig {
    pub startup_max_attempts: usize,
    pub reconnect_max_attempts: usize,
    pub retry_delay: Duration,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum WorkerStatus {
    Idle,
    Working,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SyncRequest {
    pub status: WorkerStatus,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub current_task: Option<CurrentTask>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub task_result: Option<TaskResult>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CurrentTask {
    pub execution_id: String,
    pub node_id: String,
}

#[derive(Debug, Serialize, Clone)]
#[serde(rename_all = "camelCase")]
pub struct TaskResult {
    pub execution_id: String,
    pub node_id: String,
    pub task_status: A2ATaskStatus,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub artifacts: Option<Vec<A2AArtifact>>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "snake_case", tag = "type")]
pub enum SyncResponse {
    NoAction,
    TaskAssigned { task: Box<TaskAssignment> },
    Command { command: WorkerCommand },
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct TaskAssignment {
    pub execution_id: String,
    pub node_id: String,
    pub agent: String,
    pub task: serde_json::Value,
    #[serde(default)]
    pub workflow_registry_id: Option<String>,
    #[serde(default)]
    #[allow(dead_code)]
    pub workflow_version: Option<String>,
    #[serde(default)]
    pub workflow_ref: Option<String>,
    #[serde(default)]
    #[allow(dead_code)]
    pub protocol_metadata: Option<serde_json::Value>,
}

#[derive(Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum WorkerCommand {
    Cancel,
    Shutdown,
}

impl SyncRequest {
    pub fn idle() -> Self {
        Self {
            status: WorkerStatus::Idle,
            current_task: None,
            task_result: None,
        }
    }

    pub fn working(execution_id: String, node_id: String) -> Self {
        Self {
            status: WorkerStatus::Working,
            current_task: Some(CurrentTask {
                execution_id,
                node_id,
            }),
            task_result: None,
        }
    }

    pub fn completed(result: TaskResult) -> Self {
        Self {
            status: WorkerStatus::Idle,
            current_task: None,
            task_result: Some(result),
        }
    }
}

pub async fn perform_sync(
    client: &reqwest::Client,
    scheduler_url: &str,
    request: &SyncRequest,
) -> Result<SyncResponse> {
    let url = format!("{scheduler_url}/api/worker/sync");

    let response = client
        .post(&url)
        .json(request)
        .send()
        .await
        .context("failed to send sync request")?;

    if !response.status().is_success() {
        return Err(anyhow::anyhow!(
            "sync request failed with status: {}",
            response.status()
        ));
    }

    let value: serde_json::Value = response
        .json()
        .await
        .context("failed to parse sync response JSON")?;

    if let Err(e) = common::validate_sync_response(&value) {
        tracing::error!(
            response = ?value,
            error = ?e,
            "Sync response failed schema validation"
        );
        return Err(anyhow::anyhow!("sync response validation failed: {e:?}"));
    }

    let sync_response: SyncResponse =
        serde_json::from_value(value).context("failed to deserialize sync response")?;

    Ok(sync_response)
}

/// Perform sync with retry logic based on worker state and connection history
/// - If working: retry indefinitely (task in progress, must maintain heartbeat)
/// - If never connected (startup): retry up to startup_max_attempts, then exit
/// - If previously connected (reconnecting): retry up to reconnect_max_attempts, then exit
pub async fn perform_sync_with_retry(
    client: &reqwest::Client,
    scheduler_url: &str,
    request: &SyncRequest,
    has_connected: bool,
    config: &RetryConfig,
) -> Result<SyncResponse> {
    let is_working = request.status == WorkerStatus::Working;

    // Determine retry strategy based on state
    let max_attempts = if is_working {
        usize::MAX // Unlimited retries when working
    } else if has_connected {
        config.reconnect_max_attempts // Use configured reconnect limit
    } else {
        config.startup_max_attempts // Use configured startup limit
    };

    let mut attempt = 1;
    loop {
        match perform_sync(client, scheduler_url, request).await {
            Ok(response) => return Ok(response),
            Err(e) => {
                if attempt >= max_attempts {
                    let context = if is_working {
                        "working"
                    } else if has_connected {
                        "after previous connection"
                    } else {
                        "during startup"
                    };

                    return Err(anyhow::anyhow!(
                        "Sync failed after {max_attempts} attempts ({context}), scheduler unreachable: {e}"
                    ));
                }

                // Log retry attempt with context
                if is_working {
                    tracing::warn!(
                        attempt = attempt,
                        error = %e,
                        "Sync failed while working on task, retrying indefinitely"
                    );
                } else {
                    tracing::warn!(
                        attempt = attempt,
                        max_attempts = max_attempts,
                        error = %e,
                        "Sync failed, retrying"
                    );
                }

                tokio::time::sleep(config.retry_delay).await;
                attempt += 1;
            }
        }
    }
}

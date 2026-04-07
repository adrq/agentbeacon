use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::time::Duration;

#[derive(Debug, Clone)]
pub struct RetryConfig {
    pub startup_max_attempts: usize,
    pub reconnect_max_attempts: usize,
    pub retry_delay: Duration,
}

#[derive(Debug, Serialize, Default)]
pub struct SyncRequest {
    pub worker_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub executor_report: Option<ExecutorReport>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub turn_result: Option<TurnResult>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub command_ack: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct ExecutorReport {
    pub session_id: String,
    pub executor_state: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub agent_session_id: Option<String>,
}

#[derive(Debug, Clone, Serialize)]
pub struct TurnMessage {
    pub msg_seq: i64,
    #[serde(flatten)]
    pub payload: serde_json::Value,
}

#[derive(Debug, Clone, Serialize)]
pub struct TurnResult {
    pub session_id: String,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub messages: Vec<TurnMessage>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error_kind: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub stderr: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
#[allow(clippy::large_enum_variant)]
pub enum SyncResponse {
    NoAction,
    Command {
        token: String,
        action: CommandAction,
    },
}

#[derive(Debug, Clone, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
#[allow(dead_code, clippy::large_enum_variant)]
pub enum CommandAction {
    Assign {
        session_id: String,
        #[serde(default)]
        execution_id: String,
        #[serde(default)]
        payload: Option<serde_json::Value>,
        #[serde(default)]
        resume: bool,
        #[serde(default)]
        cwd: Option<String>,
        #[serde(default)]
        driver: serde_json::Value,
        /// Agent configuration for this session.
        #[serde(default)]
        agent_config: serde_json::Value,
        #[serde(default)]
        agent_session_id: Option<String>,
        #[serde(default)]
        project_id: Option<String>,
        #[serde(default)]
        mcp_servers: Option<serde_json::Value>,
        #[serde(default)]
        next_msg_seq: i64,
    },
    FeedTurn {
        session_id: String,
        payload: serde_json::Value,
    },
    StopTurn {
        session_id: String,
    },
    Cancel {
        session_id: String,
    },
}

impl SyncRequest {
    pub fn empty(worker_id: &str) -> Self {
        Self {
            worker_id: worker_id.to_string(),
            ..Default::default()
        }
    }

    #[allow(dead_code)]
    pub fn with_report(worker_id: &str, report: ExecutorReport) -> Self {
        Self {
            worker_id: worker_id.to_string(),
            executor_report: Some(report),
            ..Default::default()
        }
    }

    #[allow(dead_code)]
    pub fn with_result(worker_id: &str, result: TurnResult) -> Self {
        Self {
            worker_id: worker_id.to_string(),
            turn_result: Some(result),
            ..Default::default()
        }
    }

    #[allow(dead_code)]
    pub fn with_ack(worker_id: &str, token: &str) -> Self {
        Self {
            worker_id: worker_id.to_string(),
            command_ack: Some(token.to_string()),
            ..Default::default()
        }
    }

    pub fn with_report_and_result(
        worker_id: &str,
        report: ExecutorReport,
        result: TurnResult,
    ) -> Self {
        Self {
            worker_id: worker_id.to_string(),
            executor_report: Some(report),
            turn_result: Some(result),
            ..Default::default()
        }
    }

    pub fn with_report_and_ack(worker_id: &str, report: ExecutorReport, ack_token: &str) -> Self {
        Self {
            worker_id: worker_id.to_string(),
            executor_report: Some(report),
            command_ack: Some(ack_token.to_string()),
            ..Default::default()
        }
    }
}

fn is_false(v: &bool) -> bool {
    !v
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct WorkerMessageEvent {
    pub worker_id: String,
    pub session_id: String,
    pub execution_id: String,
    pub msg_seq: i64,
    pub payload: serde_json::Value,
    #[serde(default, skip_serializing_if = "is_false")]
    pub ephemeral: bool,
}

pub async fn post_worker_message(
    client: &reqwest::Client,
    scheduler_url: &str,
    event: &WorkerMessageEvent,
) -> Result<()> {
    let url = format!("{scheduler_url}/api/worker/events");
    let max_attempts = 3;
    let retry_delay = Duration::from_millis(200);

    for attempt in 1..=max_attempts {
        match client.post(&url).json(event).send().await {
            Ok(response) if response.status().is_success() => return Ok(()),
            Ok(response) => {
                if attempt == max_attempts {
                    anyhow::bail!("worker event POST failed: status {}", response.status());
                }
                tracing::debug!(
                    attempt, status = %response.status(),
                    "worker event POST failed, retrying"
                );
            }
            Err(e) => {
                if attempt == max_attempts {
                    return Err(e).context("failed to post worker message event");
                }
                tracing::debug!(attempt, error = %e, "worker event POST failed, retrying");
            }
        }
        tokio::time::sleep(retry_delay).await;
    }
    unreachable!()
}

/// Parse response body as SyncResponse, logging raw body on failure.
async fn parse_sync_response(response: reqwest::Response) -> Result<SyncResponse> {
    let body = response
        .text()
        .await
        .context("failed to read sync response body")?;

    serde_json::from_str(&body).map_err(|e| {
        tracing::error!(
            body_len = body.len(),
            error = %e,
            "failed to parse sync response"
        );
        tracing::debug!(body = %body, "raw sync response body");
        anyhow::anyhow!("failed to parse sync response: {e}")
    })
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
            "send sync request failed: status {}",
            response.status()
        ));
    }

    parse_sync_response(response).await
}

/// Perform sync with extended timeout for long-poll
pub async fn perform_sync_long_poll(
    client: &reqwest::Client,
    scheduler_url: &str,
    request: &SyncRequest,
    long_poll_timeout: Duration,
) -> Result<SyncResponse> {
    let url = format!("{scheduler_url}/api/worker/sync");

    let response = client
        .post(&url)
        .json(request)
        .timeout(long_poll_timeout)
        .send()
        .await
        .context("failed to send long-poll sync request")?;

    if !response.status().is_success() {
        return Err(anyhow::anyhow!(
            "send long-poll sync request failed: status {}",
            response.status()
        ));
    }

    parse_sync_response(response).await
}

/// Perform sync with retry logic based on connection state
pub async fn perform_sync_with_retry(
    client: &reqwest::Client,
    scheduler_url: &str,
    request: &SyncRequest,
    has_connected: bool,
    config: &RetryConfig,
) -> Result<SyncResponse> {
    let is_in_session = request.executor_report.is_some()
        || request.turn_result.is_some()
        || request.command_ack.is_some();

    let max_attempts = if is_in_session {
        usize::MAX
    } else if has_connected {
        config.reconnect_max_attempts
    } else {
        config.startup_max_attempts
    };

    let mut attempt = 1;
    loop {
        match perform_sync(client, scheduler_url, request).await {
            Ok(response) => return Ok(response),
            Err(e) => {
                if attempt >= max_attempts {
                    let context = if is_in_session {
                        "in session"
                    } else if has_connected {
                        "after previous connection"
                    } else {
                        "during startup"
                    };
                    return Err(
                        e.context(format!("sync failed {context} after {attempt} attempts"))
                    );
                }

                tracing::warn!(
                    attempt,
                    max_attempts,
                    error = %e,
                    "sync failed, retrying"
                );
                tokio::time::sleep(config.retry_delay).await;
                attempt += 1;
            }
        }
    }
}

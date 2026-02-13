use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::time::Duration;

#[derive(Debug, Clone)]
pub struct RetryConfig {
    pub startup_max_attempts: usize,
    pub reconnect_max_attempts: usize,
    pub retry_delay: Duration,
}

// --- Request types (Serialize, camelCase) ---

#[derive(Debug, Serialize, Default)]
#[serde(rename_all = "camelCase")]
pub struct SyncRequest {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub session_state: Option<SessionState>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub session_result: Option<SessionResult>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionState {
    pub session_id: String,
    pub status: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub agent_session_id: Option<String>,
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SessionResult {
    pub session_id: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub agent_session_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub output: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error_kind: Option<String>,
}

// --- Response types (Deserialize, tagged union) ---

#[derive(Debug, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum SyncResponse {
    NoAction,
    SessionAssigned {
        #[serde(rename = "sessionId")]
        session_id: String,
        task: TaskAssignment,
    },
    PromptDelivery {
        #[serde(rename = "sessionId")]
        #[allow(dead_code)]
        session_id: String,
        task: TaskAssignment,
    },
    SessionComplete {
        #[serde(rename = "sessionId")]
        #[allow(dead_code)]
        session_id: String,
    },
    Command {
        command: String,
    },
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
#[allow(dead_code)]
pub struct TaskAssignment {
    pub execution_id: String,
    pub session_id: String,
    pub task_payload: serde_json::Value,
}

// --- Convenience constructors ---

impl SyncRequest {
    pub fn idle() -> Self {
        Self::default()
    }

    #[allow(dead_code)]
    pub fn running(session_id: &str) -> Self {
        Self {
            session_state: Some(SessionState {
                session_id: session_id.to_string(),
                status: "running".to_string(),
                agent_session_id: None,
            }),
            session_result: None,
        }
    }

    pub fn waiting_for_event(session_id: &str) -> Self {
        Self {
            session_state: Some(SessionState {
                session_id: session_id.to_string(),
                status: "waiting_for_event".to_string(),
                agent_session_id: None,
            }),
            session_result: None,
        }
    }

    pub fn with_result(
        session_id: &str,
        agent_session_id: Option<String>,
        output: Option<serde_json::Value>,
        error: Option<String>,
        error_kind: Option<String>,
    ) -> Self {
        // Include session_state "running" so scheduler knows we're still in this session
        // and doesn't try to assign us a new one
        Self {
            session_state: Some(SessionState {
                session_id: session_id.to_string(),
                status: "running".to_string(),
                agent_session_id: None,
            }),
            session_result: Some(SessionResult {
                session_id: session_id.to_string(),
                agent_session_id,
                output,
                error,
                error_kind,
            }),
        }
    }
}

// --- HTTP functions ---

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

/// Perform sync with extended timeout for long-poll (waiting_for_event)
pub async fn perform_sync_long_poll(
    client: &reqwest::Client,
    scheduler_url: &str,
    request: &SyncRequest,
    long_poll_timeout: Duration,
) -> Result<SyncResponse> {
    let url = format!("{scheduler_url}/api/worker/sync");

    // Build a one-off request with extended timeout
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
    let is_in_session = request.session_state.is_some() || request.session_result.is_some();

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

                    return Err(anyhow::anyhow!(
                        "Sync failed after {max_attempts} attempts ({context}), scheduler unreachable: {e}"
                    ));
                }

                tracing::warn!(
                    attempt = attempt,
                    max_attempts = max_attempts,
                    error = %e,
                    "Sync failed, retrying"
                );

                tokio::time::sleep(config.retry_delay).await;
                attempt += 1;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn test_idle_request_serializes_to_empty_object() {
        let request = SyncRequest::idle();
        let value = serde_json::to_value(&request).unwrap();
        assert_eq!(value, json!({}));
    }

    #[test]
    fn test_session_result_serializes_camelcase() {
        let request =
            SyncRequest::with_result("sess-1", Some("agent-sess-1".to_string()), None, None, None);
        let value = serde_json::to_value(&request).unwrap();
        assert_eq!(value["sessionResult"]["sessionId"], "sess-1");
        assert_eq!(value["sessionResult"]["agentSessionId"], "agent-sess-1");
    }

    #[test]
    fn test_session_result_with_output_serializes() {
        let output = json!({"role": "agent", "parts": [{"kind": "text", "text": "hello"}]});
        let request = SyncRequest::with_result(
            "sess-1",
            Some("agent-sess-1".to_string()),
            Some(output.clone()),
            None,
            None,
        );
        let value = serde_json::to_value(&request).unwrap();
        assert_eq!(value["sessionResult"]["output"], output);
    }

    #[test]
    fn test_session_result_without_output_omits_field() {
        let request = SyncRequest::with_result("sess-1", None, None, None, None);
        let value = serde_json::to_value(&request).unwrap();
        assert!(value["sessionResult"].get("output").is_none());
    }

    #[test]
    fn test_session_result_with_error_serializes() {
        let request = SyncRequest::with_result(
            "sess-1",
            None,
            None,
            Some("executor failed".to_string()),
            None,
        );
        let value = serde_json::to_value(&request).unwrap();
        assert_eq!(value["sessionResult"]["error"], "executor failed");
    }

    #[test]
    fn test_session_result_without_error_omits_field() {
        let request = SyncRequest::with_result("sess-1", None, None, None, None);
        let value = serde_json::to_value(&request).unwrap();
        assert!(value["sessionResult"].get("error").is_none());
    }

    #[test]
    fn test_session_result_with_error_kind_serializes() {
        let request = SyncRequest::with_result(
            "sess-1",
            None,
            None,
            Some("budget limit hit".to_string()),
            Some("budget_exceeded".to_string()),
        );
        let value = serde_json::to_value(&request).unwrap();
        assert_eq!(value["sessionResult"]["errorKind"], "budget_exceeded");
        assert_eq!(value["sessionResult"]["error"], "budget limit hit");
    }

    #[test]
    fn test_session_result_without_error_kind_omits_field() {
        let request = SyncRequest::with_result("sess-1", None, None, None, None);
        let value = serde_json::to_value(&request).unwrap();
        assert!(value["sessionResult"].get("errorKind").is_none());
    }

    #[test]
    fn test_waiting_for_event_serializes() {
        let request = SyncRequest::waiting_for_event("sess-1");
        let value = serde_json::to_value(&request).unwrap();
        assert_eq!(value["sessionState"]["sessionId"], "sess-1");
        assert_eq!(value["sessionState"]["status"], "waiting_for_event");
    }

    #[test]
    fn test_running_heartbeat_serializes() {
        let request = SyncRequest::running("sess-1");
        let value = serde_json::to_value(&request).unwrap();
        assert_eq!(value["sessionState"]["sessionId"], "sess-1");
        assert_eq!(value["sessionState"]["status"], "running");
    }

    #[test]
    fn test_no_action_deserializes() {
        let json = r#"{"type": "no_action"}"#;
        let response: SyncResponse = serde_json::from_str(json).unwrap();
        assert!(matches!(response, SyncResponse::NoAction));
    }

    #[test]
    fn test_session_assigned_deserializes() {
        let json = json!({
            "type": "session_assigned",
            "sessionId": "sess-1",
            "task": {
                "executionId": "exec-1",
                "sessionId": "sess-1",
                "taskPayload": {
                    "agent_id": "agent-1",
                    "agent_type": "acp",
                    "agent_config": {"command": "uv", "args": ["run", "agent"]},
                    "message": "hello"
                }
            }
        });
        let response: SyncResponse = serde_json::from_value(json).unwrap();
        match response {
            SyncResponse::SessionAssigned { session_id, task } => {
                assert_eq!(session_id, "sess-1");
                assert_eq!(task.execution_id, "exec-1");
                assert_eq!(task.session_id, "sess-1");
                assert_eq!(task.task_payload["agent_type"], "acp");
            }
            _ => panic!("expected SessionAssigned"),
        }
    }

    #[test]
    fn test_prompt_delivery_deserializes() {
        let json = json!({
            "type": "prompt_delivery",
            "sessionId": "sess-1",
            "task": {
                "executionId": "exec-1",
                "sessionId": "sess-1",
                "taskPayload": "[user]\n\nyes, use JWT"
            }
        });
        let response: SyncResponse = serde_json::from_value(json).unwrap();
        match response {
            SyncResponse::PromptDelivery { session_id, task } => {
                assert_eq!(session_id, "sess-1");
                assert!(task.task_payload.is_string());
            }
            _ => panic!("expected PromptDelivery"),
        }
    }

    #[test]
    fn test_session_complete_deserializes() {
        let json = json!({"type": "session_complete", "sessionId": "sess-1"});
        let response: SyncResponse = serde_json::from_value(json).unwrap();
        match response {
            SyncResponse::SessionComplete { session_id } => {
                assert_eq!(session_id, "sess-1");
            }
            _ => panic!("expected SessionComplete"),
        }
    }

    #[test]
    fn test_command_deserializes() {
        let json = json!({"type": "command", "command": "shutdown"});
        let response: SyncResponse = serde_json::from_value(json).unwrap();
        match &response {
            SyncResponse::Command { command } => assert_eq!(command, "shutdown"),
            _ => panic!("expected Command"),
        }

        let json = json!({"type": "command", "command": "cancel"});
        let response: SyncResponse = serde_json::from_value(json).unwrap();
        match &response {
            SyncResponse::Command { command } => assert_eq!(command, "cancel"),
            _ => panic!("expected Command"),
        }
    }
}

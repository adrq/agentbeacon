use crate::config::{AgentConfig, AgentsConfig};
use crate::sync::{TaskAssignment, TaskResult};
use anyhow::{Context, Result};
use common::{A2AArtifact, A2ATaskStatus, Part};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::Mutex;
use uuid::Uuid;

// Shared metadata that async task populates during execution
#[derive(Clone, Default)]
pub struct A2ATaskMetadata {
    pub task_id: Option<String>,
    pub rpc_url: Option<String>,
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AgentCard {
    pub url: String,
    #[serde(flatten)]
    #[allow(dead_code)]
    pub other: serde_json::Value,
}

#[derive(Debug, Serialize)]
pub struct A2aRequest {
    pub jsonrpc: String,
    pub method: String,
    pub params: serde_json::Value,
    pub id: String,
}

#[derive(Debug, Deserialize)]
pub struct A2aResponse {
    #[allow(dead_code)]
    pub jsonrpc: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<A2aError>,
    #[allow(dead_code)]
    pub id: String,
}

#[derive(Debug, Deserialize)]
pub struct A2aError {
    pub code: i64,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    #[allow(dead_code)]
    pub data: Option<serde_json::Value>,
}

pub async fn execute_a2a_task(
    client: &reqwest::Client,
    agents_config: &AgentsConfig,
    task: &TaskAssignment,
    metadata: Arc<Mutex<A2ATaskMetadata>>,
) -> TaskResult {
    let start = Instant::now();

    tracing::info!(
        execution_id = %task.execution_id,
        node_id = %task.node_id,
        agent = %task.agent,
        "Task started"
    );

    let result = match execute_a2a_task_inner(client, agents_config, task, metadata).await {
        Ok(r) => r,
        Err(e) => TaskResult {
            execution_id: task.execution_id.clone(),
            node_id: task.node_id.clone(),
            task_status: A2ATaskStatus::failed(format!("{e:#}")),
            artifacts: None,
        },
    };

    let duration = start.elapsed();
    let success = result.task_status.state == "completed";
    if success {
        tracing::info!(
            state = %result.task_status.state,
            duration_ms = duration.as_millis(),
            "Task completed successfully"
        );
    } else {
        let error_msg = result
            .task_status
            .message
            .as_ref()
            .and_then(|m| m.parts.first())
            .and_then(|p| match p {
                Part::Text { text } => Some(text.as_str()),
                _ => None,
            })
            .unwrap_or("unknown error");
        tracing::error!(
            state = %result.task_status.state,
            duration_ms = duration.as_millis(),
            error = error_msg,
            "Task failed"
        );
    }

    result
}

async fn execute_a2a_task_inner(
    client: &reqwest::Client,
    agents_config: &AgentsConfig,
    task: &TaskAssignment,
    metadata: Arc<Mutex<A2ATaskMetadata>>,
) -> Result<TaskResult> {
    if task.agent.is_empty() {
        return Err(anyhow::anyhow!("agent '' not found in configuration"));
    }

    let agent_config = agents_config.agents.get(&task.agent).ok_or_else(|| {
        let available_agents: Vec<_> = agents_config.agents.keys().collect();
        anyhow::anyhow!(
            "agent '{}' not found in configuration. Available agents: {:?}",
            task.agent,
            available_agents
        )
    })?;

    let agent_base_url = match agent_config {
        AgentConfig::A2a { config } => &config.url,
        _ => {
            return Err(anyhow::anyhow!(
                "agent '{}' is not an A2A agent (only A2A supported in Phase 1)",
                task.agent
            ));
        }
    };

    // TODO: Add agent card caching to reduce network calls per A2A best practices
    // Fetch agent card to get the RPC endpoint URL
    let agent_card_url = format!(
        "{}/.well-known/agent-card.json",
        agent_base_url.trim_end_matches('/')
    );
    let card_response = client
        .get(&agent_card_url)
        .send()
        .await
        .context(format!("failed to fetch agent card from {agent_card_url}"))?;

    if !card_response.status().is_success() {
        return Err(anyhow::anyhow!(
            "agent card request failed with status: {}",
            card_response.status()
        ));
    }

    let agent_card: AgentCard = card_response
        .json()
        .await
        .context("failed to deserialize agent card")?;

    let rpc_url = agent_card.url.clone();

    // Publish rpc_url as soon as known
    {
        let mut meta = metadata.lock().await;
        meta.rpc_url = Some(rpc_url.clone());
    }

    let request_id = format!("{}:{}", task.execution_id, task.node_id);

    // Extract history array from task payload
    // Task payload format: {"history": [Message, ...]}
    // Per A2A spec and scheduler contract: newest messages appended to END of array
    let history = task
        .task
        .get("history")
        .and_then(|h| h.as_array())
        .ok_or_else(|| anyhow::anyhow!("task payload missing 'history' array"))?;

    if history.is_empty() {
        return Err(anyhow::anyhow!("task payload has empty history array"));
    }

    // Extract the latest message (last in array) for sending
    let mut message = history
        .last()
        .ok_or_else(|| anyhow::anyhow!("history array is empty"))?
        .clone();

    // If this is a continuation (history > 1), extract taskId from earlier messages
    // Per A2A spec §6.4 & §9.2-§9.4: message.taskId indicates continuation
    // Use reverse iteration to get LATEST taskId (handles multi-task conversations)
    if history.len() > 1 {
        // Look for taskId in agent responses only (role="agent")
        if let Some(task_id) = history
            .iter()
            .rev() // Get latest taskId in case of multi-task conversations
            .filter(|m| m.get("role").and_then(|r| r.as_str()) == Some("agent"))
            .filter_map(|m| m.get("taskId").and_then(|v| v.as_str()))
            .next()
        // Now gets LAST match due to .rev()
        {
            tracing::debug!(
                task_id = task_id,
                history_length = history.len(),
                "Continuing conversation with taskId from latest agent response"
            );
            message["taskId"] = serde_json::Value::String(task_id.to_string());
        }
    }

    // Wrap in MessageSendParams structure per A2A spec
    let params = serde_json::json!({
        "message": message
    });

    let a2a_request = A2aRequest {
        jsonrpc: "2.0".to_string(),
        method: "message/send".to_string(),
        params,
        id: request_id.clone(),
    };

    // Validate outbound A2A request against schema before sending
    let request_value = serde_json::to_value(&a2a_request)
        .context("failed to serialize A2A request for validation")?;

    if let Err(e) = common::validate_a2a_request(&request_value) {
        tracing::error!(
            request = ?request_value,
            error = ?e,
            "Outbound A2A request failed schema validation"
        );
        return Err(anyhow::anyhow!("A2A request validation failed: {e:?}"));
    }

    let response = client
        .post(&rpc_url)
        .json(&a2a_request)
        .send()
        .await
        .context(format!("failed to send A2A request to {rpc_url}"))?;

    if !response.status().is_success() {
        return Err(anyhow::anyhow!(
            "A2A agent returned non-success status: {}",
            response.status()
        ));
    }

    let a2a_response: A2aResponse = response
        .json()
        .await
        .context("failed to deserialize A2A response")?;

    if let Some(error) = a2a_response.error {
        // Map A2A error codes to distinguish retryable vs permanent failures per A2A spec §8
        let is_retryable = match error.code {
            -32001 | -32002 | -32603 => true, // Task not found, agent unavailable, internal error (retryable)
            -32004 | -32602 | -32600 => false, // Terminal state, invalid params, invalid request (permanent)
            _ => false,                        // Unknown errors treated as permanent
        };

        let error_message = format!("A2A error ({}): {}", error.code, error.message);

        if is_retryable {
            tracing::warn!(
                code = error.code,
                message = %error.message,
                "Received retryable A2A error"
            );
        } else {
            tracing::error!(
                code = error.code,
                message = %error.message,
                "Received permanent A2A error"
            );
        }

        return Ok(TaskResult {
            execution_id: task.execution_id.clone(),
            node_id: task.node_id.clone(),
            task_status: A2ATaskStatus::failed(error_message),
            artifacts: None,
        });
    }

    let result_value = a2a_response
        .result
        .ok_or_else(|| anyhow::anyhow!("A2A response missing both result and error"))?;

    // Extract A2A Task fields from response
    // A2A Task object has: id, contextId, status: {state, message?, timestamp?}, artifacts?, history?, metadata?

    // Extract task_id for polling
    let task_id = result_value
        .get("id")
        .and_then(|v| v.as_str())
        .ok_or_else(|| anyhow::anyhow!("A2A Task missing 'id' field"))?
        .to_string();

    // Publish task_id as soon as message/send returns
    {
        let mut meta = metadata.lock().await;
        meta.task_id = Some(task_id.clone());
    }

    // Extract and deserialize TaskStatus directly from A2A response
    let mut task_status: A2ATaskStatus = serde_json::from_value(
        result_value
            .get("status")
            .ok_or_else(|| anyhow::anyhow!("A2A Task missing 'status' field"))?
            .clone(),
    )
    .context("failed to deserialize A2A TaskStatus")?;

    // Extract artifacts if present, deserializing directly to A2A format
    let mut artifacts = if let Some(artifacts_value) = result_value.get("artifacts") {
        let artifacts_list: Vec<A2AArtifact> = serde_json::from_value(artifacts_value.clone())
            .context("failed to deserialize A2A artifacts")?;
        if artifacts_list.is_empty() {
            None
        } else {
            Some(artifacts_list)
        }
    } else {
        None
    };

    // Check if response state is terminal or pseudo-terminal for headless execution
    let state_str = task_status.state.as_str();

    // Handle states that require user interaction (can't be satisfied in headless mode)
    if matches!(state_str, "input-required" | "auth-required") {
        tracing::warn!(
            state = state_str,
            task_id = %task_id,
            "Agent returned state requiring user interaction in headless execution - treating as failure"
        );
        return Ok(TaskResult {
            execution_id: task.execution_id.clone(),
            node_id: task.node_id.clone(),
            task_status: A2ATaskStatus::failed(format!(
                "Agent requires user interaction (state: {state_str}) which cannot be satisfied in automated workflow execution"
            )),
            artifacts,
        });
    }

    // Check if response state is terminal
    let is_terminal = matches!(state_str, "completed" | "failed" | "cancelled" | "rejected");

    if is_terminal {
        // Task finished, return immediately
        return Ok(TaskResult {
            execution_id: task.execution_id.clone(),
            node_id: task.node_id.clone(),
            task_status,
            artifacts,
        });
    }

    // Non-terminal state (submitted, working, unknown) - must poll until terminal
    tracing::debug!(
        state = %task_status.state,
        task_id = %task_id,
        "Received non-terminal state, starting polling loop"
    );

    // Polling configuration
    const POLL_INTERVAL: Duration = Duration::from_secs(1);

    loop {
        // Sleep before next poll (cancellation-friendly)
        tokio::time::sleep(POLL_INTERVAL).await;

        // Call tasks/get to fetch updated status
        let updated_task = poll_task_status(client, &rpc_url, &task_id).await?;

        // Extract updated status
        task_status = serde_json::from_value(
            updated_task
                .get("status")
                .ok_or_else(|| anyhow::anyhow!("A2A Task missing 'status' field"))?
                .clone(),
        )
        .context("failed to deserialize A2A TaskStatus")?;

        let state_str = task_status.state.as_str();

        // Handle states requiring user interaction (headless execution constraint)
        if matches!(state_str, "input-required" | "auth-required") {
            tracing::warn!(
                state = state_str,
                task_id = %task_id,
                "Agent transitioned to state requiring user interaction - treating as failure"
            );
            return Ok(TaskResult {
                execution_id: task.execution_id.clone(),
                node_id: task.node_id.clone(),
                task_status: A2ATaskStatus::failed(format!(
                    "Agent requires user interaction (state: {state_str}) in automated workflow"
                )),
                artifacts,
            });
        }

        // Check if terminal
        let is_terminal = matches!(state_str, "completed" | "failed" | "cancelled" | "rejected");

        if is_terminal {
            // Extract final artifacts
            artifacts = if let Some(artifacts_value) = updated_task.get("artifacts") {
                let artifacts_list: Vec<A2AArtifact> =
                    serde_json::from_value(artifacts_value.clone())
                        .context("failed to deserialize A2A artifacts")?;
                if artifacts_list.is_empty() {
                    None
                } else {
                    Some(artifacts_list)
                }
            } else {
                None
            };

            return Ok(TaskResult {
                execution_id: task.execution_id.clone(),
                node_id: task.node_id.clone(),
                task_status,
                artifacts,
            });
        }

        // Log progress for non-terminal states
        tracing::debug!(
            state = %task_status.state,
            task_id = %task_id,
            "Task still in non-terminal state, continuing poll"
        );
    }
}

async fn poll_task_status(
    client: &reqwest::Client,
    rpc_url: &str,
    task_id: &str,
) -> Result<serde_json::Value> {
    let request = A2aRequest {
        jsonrpc: "2.0".to_string(),
        method: "tasks/get".to_string(),
        params: serde_json::json!({ "id": task_id }),
        id: Uuid::new_v4().to_string(),
    };

    let response = client
        .post(rpc_url)
        .json(&request)
        .send()
        .await
        .context("failed to send tasks/get request")?;

    if !response.status().is_success() {
        return Err(anyhow::anyhow!(
            "tasks/get returned non-success status: {}",
            response.status()
        ));
    }

    let a2a_response: A2aResponse = response
        .json()
        .await
        .context("failed to deserialize tasks/get response")?;

    if let Some(error) = a2a_response.error {
        return Err(anyhow::anyhow!(
            "tasks/get error ({}): {}",
            error.code,
            error.message
        ));
    }

    a2a_response
        .result
        .ok_or_else(|| anyhow::anyhow!("tasks/get response missing result"))
}

pub async fn cancel_a2a_task(client: &reqwest::Client, rpc_url: &str, task_id: &str) -> Result<()> {
    let request = A2aRequest {
        jsonrpc: "2.0".to_string(),
        method: "tasks/cancel".to_string(),
        params: serde_json::json!({ "id": task_id }),
        id: Uuid::new_v4().to_string(),
    };

    // Best-effort cancel with short timeout, don't fail if agent rejects
    let _ = client
        .post(rpc_url)
        .json(&request)
        .timeout(Duration::from_secs(2))
        .send()
        .await;

    Ok(())
}

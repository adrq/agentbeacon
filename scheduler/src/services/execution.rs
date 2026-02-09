use serde_json::{Value as JsonValue, json};
use uuid::Uuid;

use crate::db;
use crate::db::DbPool;
use crate::error::SchedulerError;
use crate::queue::{TaskAssignment, TaskQueue};

pub struct CreateExecutionResult {
    pub execution_id: String,
    pub session_id: String,
    pub status: String,
}

pub async fn create_execution(
    db_pool: &DbPool,
    task_queue: &TaskQueue,
    agent_id: &str,
    prompt: &str,
    workspace_id: Option<&str>,
    title: Option<&str>,
) -> Result<CreateExecutionResult, SchedulerError> {
    // Look up agent — NotFound if missing, ValidationFailed if disabled
    let agent = db::agents::get_by_id(db_pool, agent_id).await?;
    if !agent.enabled {
        return Err(SchedulerError::ValidationFailed(format!(
            "agent is disabled: {}",
            agent_id
        )));
    }

    let execution_id = Uuid::new_v4().to_string();
    let session_id = Uuid::new_v4().to_string();
    let context_id = execution_id.clone();

    // Build A2A-style input message
    let input = json!({
        "role": "user",
        "parts": [{"kind": "text", "text": prompt}]
    });
    let input_json = serde_json::to_string(&input)
        .map_err(|e| SchedulerError::Database(format!("serialize input failed: {e}")))?;

    // Create execution
    db::executions::create(
        db_pool,
        &execution_id,
        &context_id,
        &input_json,
        workspace_id,
        None,
        title,
    )
    .await?;

    // Create master session (no parent)
    db::sessions::create(db_pool, &session_id, &execution_id, &agent.id, None).await?;

    // Record initial state_change event
    let state_event = json!({"from": null, "to": "submitted"});
    db::events::insert(
        db_pool,
        &execution_id,
        None,
        "state_change",
        &serde_json::to_string(&state_event).unwrap(),
    )
    .await?;

    // Enqueue initial task to session inbox
    let agent_config: JsonValue = serde_json::from_str(&agent.config).unwrap_or_else(|_| json!({}));
    let sandbox_config: JsonValue = agent
        .sandbox_config
        .as_ref()
        .and_then(|s| serde_json::from_str(s).ok())
        .unwrap_or(JsonValue::Null);
    let task_payload = json!({
        "agent_id": agent.id,
        "agent_type": agent.agent_type,
        "agent_config": agent_config,
        "sandbox_config": sandbox_config,
        "message": input
    });
    task_queue
        .push(TaskAssignment {
            execution_id: execution_id.clone(),
            session_id: session_id.clone(),
            task_payload,
        })
        .await?;

    Ok(CreateExecutionResult {
        execution_id,
        session_id,
        status: "submitted".to_string(),
    })
}

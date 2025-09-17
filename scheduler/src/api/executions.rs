use axum::{
    Json, Router,
    extract::{Path, Query, State},
    routing::get,
};
use serde::{Deserialize, Serialize};
use serde_json::{Value as JsonValue, json};
use uuid::Uuid;

use crate::app::AppState;
use crate::db;
use crate::error::SchedulerError;

/// Request body for creating executions
#[derive(Debug, Deserialize)]
pub struct CreateExecutionRequest {
    pub workflow_id: String,
}

/// Query parameters for listing executions
#[derive(Debug, Deserialize)]
pub struct ListExecutionsQuery {
    pub workflow_registry_id: Option<String>,
    pub status: Option<String>,
    pub limit: Option<i64>,
}

/// Execution response matching OpenAPI spec
#[derive(Debug, Serialize)]
pub struct ExecutionResponse {
    pub id: String,
    pub workflow_id: String,
    pub status: String,
    pub task_states: JsonValue,
    pub created_at: String,
    pub updated_at: String,
    pub completed_at: Option<String>,
}

impl From<db::Execution> for ExecutionResponse {
    fn from(execution: db::Execution) -> Self {
        Self {
            id: execution.id.to_string(),
            workflow_id: execution.workflow_id.to_string(),
            status: execution.status,
            task_states: execution.task_states,
            created_at: execution.created_at.to_rfc3339(),
            updated_at: execution.updated_at.to_rfc3339(),
            completed_at: execution.completed_at.map(|dt| dt.to_rfc3339()),
        }
    }
}

/// Execution detail response with events
#[derive(Debug, Serialize)]
pub struct ExecutionDetailResponse {
    pub id: String,
    pub workflow_id: String,
    pub status: String,
    pub task_states: JsonValue,
    pub created_at: String,
    pub updated_at: String,
    pub completed_at: Option<String>,
    pub events: Vec<ExecutionEventResponse>,
}

/// Execution event response matching OpenAPI spec
#[derive(Debug, Serialize)]
pub struct ExecutionEventResponse {
    pub id: i64,
    pub execution_id: String,
    pub event_type: String,
    pub task_id: Option<String>,
    pub message: String,
    pub metadata: JsonValue,
    pub timestamp: String,
}

impl From<db::ExecutionEvent> for ExecutionEventResponse {
    fn from(event: db::ExecutionEvent) -> Self {
        Self {
            id: event.id,
            execution_id: event.execution_id.to_string(),
            event_type: event.event_type,
            task_id: event.task_id,
            message: event.message,
            metadata: event.metadata,
            timestamp: event.timestamp.to_rfc3339(),
        }
    }
}

/// List all executions (GET /api/executions)
async fn list_executions(
    State(state): State<AppState>,
    Query(query): Query<ListExecutionsQuery>,
) -> Result<Json<Vec<ExecutionResponse>>, SchedulerError> {
    let workflow_id = if let Some(wf_id_str) = query.workflow_registry_id {
        let uuid = Uuid::parse_str(&wf_id_str).map_err(|_| {
            SchedulerError::ValidationFailed(format!("Invalid workflow_id UUID: {wf_id_str}"))
        })?;
        Some(uuid)
    } else {
        None
    };

    let executions = db::executions::list(
        &state.db_pool,
        workflow_id.as_ref(),
        query.status.as_deref(),
        query.limit,
    )
    .await?;

    let responses: Vec<ExecutionResponse> = executions.into_iter().map(Into::into).collect();
    Ok(Json(responses))
}

/// Get execution by ID with events (GET /api/executions/:id)
async fn get_execution(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<ExecutionDetailResponse>, SchedulerError> {
    let uuid = Uuid::parse_str(&id)
        .map_err(|_| SchedulerError::ValidationFailed(format!("Invalid UUID: {id}")))?;

    let execution = db::executions::get_by_id(&state.db_pool, &uuid).await?;
    let events = db::execution_events::list_by_execution(&state.db_pool, &uuid).await?;

    let response = ExecutionDetailResponse {
        id: execution.id.to_string(),
        workflow_id: execution.workflow_id.to_string(),
        status: execution.status,
        task_states: execution.task_states,
        created_at: execution.created_at.to_rfc3339(),
        updated_at: execution.updated_at.to_rfc3339(),
        completed_at: execution.completed_at.map(|dt| dt.to_rfc3339()),
        events: events.into_iter().map(Into::into).collect(),
    };

    Ok(Json(response))
}

/// Create a new execution (POST /api/executions)
async fn create_execution(
    State(state): State<AppState>,
    Json(payload): Json<CreateExecutionRequest>,
) -> Result<Json<ExecutionResponse>, SchedulerError> {
    let workflow_id = Uuid::parse_str(&payload.workflow_id).map_err(|_| {
        SchedulerError::ValidationFailed(format!(
            "Invalid workflow_id UUID: {}",
            payload.workflow_id
        ))
    })?;

    // Verify workflow exists
    let workflow = db::workflows::get_by_id(&state.db_pool, &workflow_id).await?;

    // Parse workflow YAML to extract task IDs
    let workflow_json: JsonValue = serde_yaml::from_str(&workflow.yaml_content)
        .map_err(|e| SchedulerError::ValidationFailed(format!("Invalid workflow YAML: {e}")))?;

    // Extract tasks array and initialize task_states
    let tasks = workflow_json["tasks"].as_array().ok_or_else(|| {
        SchedulerError::ValidationFailed("Workflow missing tasks array".to_string())
    })?;

    let mut task_states = json!({});
    for task in tasks {
        let task_id = task["id"]
            .as_str()
            .ok_or_else(|| SchedulerError::ValidationFailed("Task missing id field".to_string()))?;

        task_states[task_id] = json!({
            "status": "pending",
            "started_at": null,
            "completed_at": null,
            "error": null,
        });
    }

    // Create execution record
    let execution_id = db::executions::create(
        &state.db_pool,
        &workflow_id,
        task_states.clone(),
        None,
        None,
    )
    .await?;

    // Create initial execution event
    db::execution_events::create(
        &state.db_pool,
        &execution_id,
        "execution_start",
        None,
        "Execution created",
        json!({}),
    )
    .await?;

    // Fetch the created execution
    let execution = db::executions::get_by_id(&state.db_pool, &execution_id).await?;

    Ok(Json(execution.into()))
}

/// Execution routes
pub fn routes() -> Router<AppState> {
    Router::new()
        .route(
            "/api/executions",
            get(list_executions).post(create_execution),
        )
        .route("/api/executions/{id}", get(get_execution))
}

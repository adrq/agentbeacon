use axum::{
    Json, Router,
    extract::{Path, Query, State},
    http::StatusCode,
    response::IntoResponse,
    routing::get,
};
use serde::{Deserialize, Serialize};

use crate::app::AppState;
use crate::db;
use crate::error::SchedulerError;
use crate::services::execution;

/// Query parameters for listing executions
#[derive(Debug, Deserialize)]
pub struct ListExecutionsQuery {
    pub workspace_id: Option<String>,
    pub status: Option<String>,
    pub limit: Option<i64>,
}

/// Execution response matching new schema
#[derive(Debug, Serialize)]
pub struct ExecutionResponse {
    pub id: String,
    pub workspace_id: Option<String>,
    pub parent_execution_id: Option<String>,
    pub context_id: String,
    pub status: String,
    pub title: Option<String>,
    pub created_at: String,
    pub updated_at: String,
    pub completed_at: Option<String>,
}

impl From<db::Execution> for ExecutionResponse {
    fn from(e: db::Execution) -> Self {
        Self {
            id: e.id,
            workspace_id: e.workspace_id,
            parent_execution_id: e.parent_execution_id,
            context_id: e.context_id,
            status: e.status,
            title: e.title,
            created_at: e.created_at.to_rfc3339(),
            updated_at: e.updated_at.to_rfc3339(),
            completed_at: e.completed_at.map(|dt| dt.to_rfc3339()),
        }
    }
}

/// Session summary for execution detail
#[derive(Debug, Serialize)]
pub struct SessionResponse {
    pub id: String,
    pub execution_id: String,
    pub parent_session_id: Option<String>,
    pub agent_id: String,
    pub status: String,
    pub created_at: String,
    pub updated_at: String,
}

impl From<db::sessions::Session> for SessionResponse {
    fn from(s: db::sessions::Session) -> Self {
        Self {
            id: s.id,
            execution_id: s.execution_id,
            parent_session_id: s.parent_session_id,
            agent_id: s.agent_id,
            status: s.status,
            created_at: s.created_at.to_rfc3339(),
            updated_at: s.updated_at.to_rfc3339(),
        }
    }
}

/// Execution detail with sessions
#[derive(Debug, Serialize)]
pub struct ExecutionDetailResponse {
    #[serde(flatten)]
    pub execution: ExecutionResponse,
    pub sessions: Vec<SessionResponse>,
}

/// Request body for creating an execution
#[derive(Debug, Deserialize)]
pub struct CreateExecutionRequest {
    pub agent_id: String,
    pub prompt: String,
    pub workspace_id: Option<String>,
    pub title: Option<String>,
    pub cwd: Option<String>,
}

/// Response for create execution
#[derive(Debug, Serialize)]
pub struct CreateExecutionResponse {
    pub execution_id: String,
    pub session_id: String,
    pub status: String,
}

/// List all executions (GET /api/executions)
async fn list_executions(
    State(state): State<AppState>,
    Query(query): Query<ListExecutionsQuery>,
) -> Result<Json<Vec<ExecutionResponse>>, SchedulerError> {
    let executions = db::executions::list(
        &state.db_pool,
        query.workspace_id.as_deref(),
        query.status.as_deref(),
        query.limit,
    )
    .await?;

    let responses: Vec<ExecutionResponse> = executions.into_iter().map(Into::into).collect();
    Ok(Json(responses))
}

/// Get execution by ID with sessions (GET /api/executions/:id)
async fn get_execution(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<ExecutionDetailResponse>, SchedulerError> {
    let exec = db::executions::get_by_id(&state.db_pool, &id).await?;
    let sessions = db::sessions::list_by_execution(&state.db_pool, &id).await?;

    Ok(Json(ExecutionDetailResponse {
        execution: exec.into(),
        sessions: sessions.into_iter().map(Into::into).collect(),
    }))
}

/// Create a new execution (POST /api/executions)
async fn create_execution_handler(
    State(state): State<AppState>,
    Json(req): Json<CreateExecutionRequest>,
) -> Result<impl IntoResponse, SchedulerError> {
    let result = execution::create_execution(
        &state.db_pool,
        &state.task_queue,
        &req.agent_id,
        &req.prompt,
        req.workspace_id.as_deref(),
        req.title.as_deref(),
        req.cwd.as_deref(),
    )
    .await?;

    Ok((
        StatusCode::CREATED,
        Json(CreateExecutionResponse {
            execution_id: result.execution_id,
            session_id: result.session_id,
            status: result.status,
        }),
    ))
}

/// Execution routes
pub fn routes() -> Router<AppState> {
    Router::new()
        .route(
            "/api/executions",
            get(list_executions).post(create_execution_handler),
        )
        .route("/api/executions/{id}", get(get_execution))
}

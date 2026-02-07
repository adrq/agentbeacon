use axum::{
    Json, Router,
    extract::{Path, Query, State},
    http::StatusCode,
    routing::get,
};
use serde::{Deserialize, Serialize};

use crate::app::AppState;
use crate::db;
use crate::error::SchedulerError;

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

/// Get execution by ID (GET /api/executions/:id)
async fn get_execution(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<ExecutionResponse>, SchedulerError> {
    let execution = db::executions::get_by_id(&state.db_pool, &id).await?;
    Ok(Json(execution.into()))
}

/// Create a new execution (POST /api/executions)
///
/// Stubbed: returns 501 Not Implemented.
async fn create_execution() -> StatusCode {
    StatusCode::NOT_IMPLEMENTED
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

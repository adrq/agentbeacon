use axum::{
    Json, Router,
    extract::{Path, Query, State},
    routing::get,
};
use chrono::Utc;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::app::AppState;
use crate::db;
use crate::error::SchedulerError;

/// Maximum YAML content size (1MB) to prevent memory exhaustion
const MAX_YAML_SIZE: usize = 1024 * 1024;

/// Request body for creating/updating workflows
#[derive(Debug, Deserialize)]
pub struct CreateWorkflowRequest {
    pub yaml_content: String,
}

/// Query parameters for listing workflows
#[derive(Debug, Deserialize)]
pub struct ListWorkflowsQuery {
    pub name: Option<String>,
}

/// Workflow response matching OpenAPI spec
#[derive(Debug, Serialize)]
pub struct WorkflowResponse {
    pub id: String,
    pub name: String,
    pub description: Option<String>,
    pub yaml_content: String,
    pub created_at: String,
    pub updated_at: String,
}

impl From<db::Workflow> for WorkflowResponse {
    fn from(workflow: db::Workflow) -> Self {
        Self {
            id: workflow.id.to_string(),
            name: workflow.name,
            description: workflow.description,
            yaml_content: workflow.yaml_content,
            created_at: workflow.created_at.to_rfc3339(),
            updated_at: workflow.updated_at.to_rfc3339(),
        }
    }
}

/// List all workflows (GET /api/workflows)
async fn list_workflows(
    State(state): State<AppState>,
    Query(query): Query<ListWorkflowsQuery>,
) -> Result<Json<Vec<WorkflowResponse>>, SchedulerError> {
    let workflows = db::workflows::list(&state.db_pool, query.name.as_deref()).await?;
    let responses: Vec<WorkflowResponse> = workflows.into_iter().map(Into::into).collect();
    Ok(Json(responses))
}

/// Get workflow by ID (GET /api/workflows/:id)
async fn get_workflow(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<WorkflowResponse>, SchedulerError> {
    let uuid = Uuid::parse_str(&id)
        .map_err(|_| SchedulerError::ValidationFailed(format!("Invalid UUID: {id}")))?;

    let workflow = db::workflows::get_by_id(&state.db_pool, &uuid).await?;
    Ok(Json(workflow.into()))
}

/// Create or update workflow (POST /api/workflows)
async fn create_workflow(
    State(state): State<AppState>,
    Json(payload): Json<CreateWorkflowRequest>,
) -> Result<Json<WorkflowResponse>, SchedulerError> {
    // Validate payload size to prevent memory exhaustion
    if payload.yaml_content.len() > MAX_YAML_SIZE {
        return Err(SchedulerError::ValidationFailed(format!(
            "YAML content exceeds maximum size of {} bytes ({} bytes provided)",
            MAX_YAML_SIZE,
            payload.yaml_content.len()
        )));
    }

    // Validate YAML against workflow-schema.json
    let workflow_json = state
        .validator
        .validate_workflow_yaml(&payload.yaml_content)?;

    // Extract name from validated YAML (required field)
    let name = workflow_json["name"]
        .as_str()
        .ok_or_else(|| {
            SchedulerError::ValidationFailed("Missing required field 'name'".to_string())
        })?
        .to_string();

    // Extract optional description
    let description = workflow_json["description"].as_str().map(|s| s.to_string());

    // Create workflow entity
    let workflow = db::Workflow {
        id: Uuid::new_v4(),
        name,
        description,
        yaml_content: payload.yaml_content,
        created_at: Utc::now(),
        updated_at: Utc::now(),
    };

    // Upsert workflow (create or update by name)
    db::workflows::upsert(&state.db_pool, &workflow).await?;

    // Fetch actual workflow from database to return correct ID and timestamps
    let saved_workflow = db::workflows::get_by_name(&state.db_pool, &workflow.name).await?;
    Ok(Json(saved_workflow.into()))
}

/// Workflow routes
pub fn routes() -> Router<AppState> {
    Router::new()
        .route("/api/workflows", get(list_workflows).post(create_workflow))
        .route("/api/workflows/{id}", get(get_workflow))
}

use axum::{Json, Router, extract::State, routing::get};
use serde::Serialize;

use crate::app::AppState;
use crate::db;
use crate::error::SchedulerError;

#[derive(Debug, Serialize)]
pub struct AgentResponse {
    pub id: String,
    pub name: String,
    pub description: Option<String>,
    pub agent_type: String,
    pub enabled: bool,
    pub config: serde_json::Value,
    pub sandbox_config: Option<serde_json::Value>,
    pub created_at: String,
    pub updated_at: String,
}

impl From<db::agents::Agent> for AgentResponse {
    fn from(a: db::agents::Agent) -> Self {
        let config = serde_json::from_str(&a.config).unwrap_or_else(|e| {
            tracing::warn!(agent_id = %a.id, error = %e, "invalid JSON in agent config, falling back to {{}}");
            serde_json::json!({})
        });
        let sandbox_config = a.sandbox_config.as_ref().map(|s| {
            serde_json::from_str(s).unwrap_or_else(|e| {
                tracing::warn!(agent_id = %a.id, error = %e, "invalid JSON in agent sandbox_config");
                serde_json::Value::Null
            })
        });
        Self {
            id: a.id,
            name: a.name,
            description: a.description,
            agent_type: a.agent_type,
            enabled: a.enabled,
            config,
            sandbox_config,
            created_at: a.created_at.to_rfc3339(),
            updated_at: a.updated_at.to_rfc3339(),
        }
    }
}

async fn list_agents(
    State(state): State<AppState>,
) -> Result<Json<Vec<AgentResponse>>, SchedulerError> {
    let agents = db::agents::list(&state.db_pool).await?;
    Ok(Json(agents.into_iter().map(Into::into).collect()))
}

pub fn routes() -> Router<AppState> {
    Router::new().route("/api/agents", get(list_agents))
}

use serde::Serialize;
use serde_json::json;

use crate::db;

/// Shared execution response shape used by list, detail, and create endpoints.
#[derive(Debug, Serialize)]
pub struct ExecutionResponse {
    pub id: String,
    pub project_id: Option<String>,
    pub parent_execution_id: Option<String>,
    pub context_id: String,
    pub status: String,
    pub title: Option<String>,
    pub input: String,
    pub metadata: serde_json::Value,
    pub max_depth: i64,
    pub max_width: i64,
    pub created_at: String,
    pub updated_at: String,
    pub completed_at: Option<String>,
}

impl From<db::Execution> for ExecutionResponse {
    fn from(e: db::Execution) -> Self {
        let metadata = serde_json::from_str(&e.metadata).unwrap_or_else(|_| serde_json::json!({}));
        Self {
            id: e.id,
            project_id: e.project_id,
            parent_execution_id: e.parent_execution_id,
            context_id: e.context_id,
            status: e.status,
            title: e.title,
            input: e.input,
            metadata,
            max_depth: e.max_depth,
            max_width: e.max_width,
            created_at: e.created_at.to_rfc3339(),
            updated_at: e.updated_at.to_rfc3339(),
            completed_at: e.completed_at.map(|dt| dt.to_rfc3339()),
        }
    }
}

/// Shared session response shape.
#[derive(Debug, Serialize)]
pub struct SessionResponse {
    pub id: String,
    pub execution_id: String,
    pub parent_session_id: Option<String>,
    pub agent_id: String,
    pub agent_session_id: Option<String>,
    pub cwd: Option<String>,
    pub worktree_path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub base_commit_sha: Option<String>,
    pub status: String,
    pub recovery_attempts: i64,
    pub metadata: serde_json::Value,
    pub created_at: String,
    pub updated_at: String,
    pub completed_at: Option<String>,
}

impl From<db::sessions::Session> for SessionResponse {
    fn from(s: db::sessions::Session) -> Self {
        let metadata = serde_json::from_str(&s.metadata).unwrap_or_else(|_| serde_json::json!({}));
        Self {
            id: s.id,
            execution_id: s.execution_id,
            parent_session_id: s.parent_session_id,
            agent_id: s.agent_id,
            agent_session_id: s.agent_session_id,
            cwd: s.cwd,
            worktree_path: s.worktree_path,
            base_commit_sha: s.base_commit_sha,
            status: s.status,
            recovery_attempts: s.recovery_attempts,
            metadata,
            created_at: s.created_at.to_rfc3339(),
            updated_at: s.updated_at.to_rfc3339(),
            completed_at: s.completed_at.map(|dt| dt.to_rfc3339()),
        }
    }
}

/// Shared event response shape.
#[derive(Debug, Serialize)]
pub struct EventResponse {
    pub id: i64,
    pub execution_id: String,
    pub session_id: Option<String>,
    pub event_type: String,
    pub payload: serde_json::Value,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub msg_seq: Option<i64>,
    pub created_at: String,
}

impl From<db::events::Event> for EventResponse {
    fn from(e: db::events::Event) -> Self {
        let payload_value = serde_json::from_str(&e.payload).unwrap_or(json!(e.payload));
        Self {
            id: e.id,
            execution_id: e.execution_id,
            session_id: e.session_id,
            event_type: e.event_type,
            payload: payload_value,
            msg_seq: e.msg_seq,
            created_at: e.created_at.to_rfc3339(),
        }
    }
}

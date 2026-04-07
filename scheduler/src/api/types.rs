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
    pub desired: String,
    pub outcome: Option<String>,
    /// Display status derived on read.
    pub status: String,
    /// Whether the execution can be completed (vs canceled).
    pub completion_eligible: bool,
    pub title: Option<String>,
    pub metadata: serde_json::Value,
    pub max_depth: i64,
    pub max_width: i64,
    pub created_at: String,
    pub updated_at: String,
    pub completed_at: Option<String>,
}

/// Derived execution fields computed from session tree state.
pub struct ExecutionDerived {
    pub status: String,
    pub completion_eligible: bool,
}

impl ExecutionResponse {
    /// Derive from paired (session, pending_turns) snapshot — single-query path.
    pub fn derive_from_snapshot(
        exec: &db::Execution,
        snapshot: &[(db::sessions::Session, i64)],
    ) -> ExecutionDerived {
        if let Some(ref outcome) = exec.outcome {
            return ExecutionDerived {
                status: outcome.clone(),
                completion_eligible: false,
            };
        }
        if exec.desired == "terminate" {
            return ExecutionDerived {
                status: "canceled".to_string(),
                completion_eligible: false,
            };
        }

        let any_active = snapshot
            .iter()
            .any(|(s, pending)| crate::services::transition::is_active(s, *pending));

        if any_active {
            return ExecutionDerived {
                status: "working".to_string(),
                completion_eligible: false,
            };
        }

        let root = snapshot.iter().find(|(s, _)| s.parent_session_id.is_none());
        let root_alive = root.is_some_and(|(r, _)| r.outcome.is_none());

        let all_quiescent = snapshot.iter().all(|(s, pending)| {
            s.outcome.is_some() || crate::services::transition::is_quiescent(s, *pending)
        });

        let no_unnotified_failure = !snapshot.iter().any(|(s, _)| {
            s.outcome.as_deref() == Some("failed")
                && !s.parent_notified
                && s.parent_session_id.as_ref().is_some_and(|pid| {
                    snapshot
                        .iter()
                        .find(|(p, _)| p.id == *pid)
                        .is_some_and(|(p, _)| p.outcome.is_none())
                })
        });

        let eligible = root_alive && all_quiescent && no_unnotified_failure;

        ExecutionDerived {
            status: "awaiting_input".to_string(),
            completion_eligible: eligible,
        }
    }

    /// Derive from separate session and pending_counts arrays (list endpoint path).
    pub fn derive(
        exec: &db::Execution,
        sessions: &[db::sessions::Session],
        pending_counts: &[(String, i64)],
    ) -> ExecutionDerived {
        if let Some(ref outcome) = exec.outcome {
            return ExecutionDerived {
                status: outcome.clone(),
                completion_eligible: false,
            };
        }
        if exec.desired == "terminate" {
            return ExecutionDerived {
                status: "canceled".to_string(),
                completion_eligible: false,
            };
        }

        let pending_for = |sid: &str| -> i64 {
            pending_counts
                .iter()
                .find(|(s, _)| s == sid)
                .map(|(_, c)| *c)
                .unwrap_or(0)
        };

        let any_active = sessions
            .iter()
            .any(|s| crate::services::transition::is_active(s, pending_for(&s.id)));

        if any_active {
            return ExecutionDerived {
                status: "working".to_string(),
                completion_eligible: false,
            };
        }

        let root = sessions.iter().find(|s| s.parent_session_id.is_none());

        let root_alive = root.is_some_and(|r| r.outcome.is_none());

        let all_quiescent = sessions.iter().all(|s| {
            s.outcome.is_some() || crate::services::transition::is_quiescent(s, pending_for(&s.id))
        });

        let no_unnotified_failure = !sessions.iter().any(|s| {
            s.outcome.as_deref() == Some("failed")
                && !s.parent_notified
                && s.parent_session_id.as_ref().is_some_and(|pid| {
                    sessions
                        .iter()
                        .find(|p| p.id == *pid)
                        .is_some_and(|p| p.outcome.is_none())
                })
        });

        let eligible = root_alive && all_quiescent && no_unnotified_failure;

        ExecutionDerived {
            status: "awaiting_input".to_string(),
            completion_eligible: eligible,
        }
    }
}

impl From<db::Execution> for ExecutionResponse {
    fn from(e: db::Execution) -> Self {
        let metadata = serde_json::from_str(&e.metadata).unwrap_or_else(|_| serde_json::json!({}));
        let status = if let Some(ref outcome) = e.outcome {
            outcome.clone()
        } else if e.desired == "terminate" {
            "canceled".to_string()
        } else {
            "awaiting_input".to_string()
        };
        Self {
            id: e.id,
            project_id: e.project_id,
            parent_execution_id: e.parent_execution_id,
            context_id: e.context_id,
            desired: e.desired,
            outcome: e.outcome,
            status,
            completion_eligible: false,
            title: e.title,
            metadata,
            max_depth: e.max_depth,
            max_width: e.max_width,
            created_at: e.created_at.to_rfc3339(),
            updated_at: e.updated_at.to_rfc3339(),
            completed_at: e.completed_at.map(|dt| dt.to_rfc3339()),
        }
    }
}

/// Derive session display status from internal state.
pub fn derive_session_display_status(s: &db::sessions::Session, pending_turns: i64) -> String {
    if let Some(ref outcome) = s.outcome {
        return outcome.clone();
    }
    if crate::services::transition::is_active(s, pending_turns) {
        return "working".to_string();
    }
    if s.desired == "stop" {
        return "stopped".to_string();
    }
    match s.executor_state.as_str() {
        "idle" => "idle".to_string(),
        "unassigned" => "unassigned".to_string(),
        "crashed" => "crashed".to_string(),
        _ => "unassigned".to_string(),
    }
}

/// Fallback derivation without pending_turns data (used by From<Session>).
/// Covers most cases but lacks pending_turns data.
fn derive_session_display_status_fallback(s: &db::sessions::Session) -> String {
    if let Some(ref outcome) = s.outcome {
        return outcome.clone();
    }
    if s.executor_state == "running" || s.command_token.is_some() {
        return "working".to_string();
    }
    if s.desired == "stop" {
        return "stopped".to_string();
    }
    match s.executor_state.as_str() {
        "idle" => "idle".to_string(),
        "unassigned" => "unassigned".to_string(),
        "crashed" => "crashed".to_string(),
        _ => "unassigned".to_string(),
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
    pub desired: String,
    pub executor_state: String,
    pub outcome: Option<String>,
    /// Server-derived display status.
    pub status: String,
    pub desired_by: Option<String>,
    pub worker_id: Option<String>,
    pub command_type: Option<String>,
    pub parent_notified: bool,
    pub recovery_attempts: i64,
    pub metadata: serde_json::Value,
    pub created_at: String,
    pub updated_at: String,
    pub completed_at: Option<String>,
}

impl From<db::sessions::Session> for SessionResponse {
    fn from(s: db::sessions::Session) -> Self {
        let metadata = serde_json::from_str(&s.metadata).unwrap_or_else(|_| serde_json::json!({}));
        let status = derive_session_display_status_fallback(&s);
        Self {
            id: s.id,
            execution_id: s.execution_id,
            parent_session_id: s.parent_session_id,
            agent_id: s.agent_id,
            agent_session_id: s.agent_session_id,
            cwd: s.cwd,
            worktree_path: s.worktree_path,
            base_commit_sha: s.base_commit_sha,
            desired: s.desired,
            executor_state: s.executor_state,
            outcome: s.outcome,
            status,
            desired_by: s.desired_by,
            worker_id: s.worker_id,
            command_type: s.command_type,
            parent_notified: s.parent_notified,
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

        let event_type = if e.event_type == "platform" {
            let data_type = payload_value
                .get("parts")
                .and_then(|p| p.get(0))
                .and_then(|p| p.get("data"))
                .and_then(|d| d.get("type"))
                .and_then(|t| t.as_str());
            if data_type == Some("escalate") {
                "escalate".to_string()
            } else {
                e.event_type
            }
        } else {
            e.event_type
        };

        Self {
            id: e.id,
            execution_id: e.execution_id,
            session_id: e.session_id,
            event_type,
            payload: payload_value,
            msg_seq: e.msg_seq,
            created_at: e.created_at.to_rfc3339(),
        }
    }
}

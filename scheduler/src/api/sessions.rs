use std::time::Duration;

use axum::{
    Json, Router,
    extract::{Path, Query, State},
    http::StatusCode,
    response::IntoResponse,
    routing::get,
};
use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::api::types::{EventResponse, SessionResponse};
use crate::app::{AppState, EventNotification};
use crate::db;
use crate::error::SchedulerError;
use crate::queue::TaskAssignment;

/// Query parameters for listing sessions
#[derive(Debug, Deserialize)]
pub struct ListSessionsQuery {
    pub status: Option<String>,
    pub execution_id: Option<String>,
}

/// Request body for posting a user message
#[derive(Debug, Deserialize)]
pub struct PostMessageRequest {
    pub message: String,
}

/// Query parameters for diff endpoint
#[derive(Debug, Deserialize)]
pub struct DiffQuery {
    pub base: Option<String>,
    pub stat: Option<bool>,
}

#[derive(Debug, Serialize)]
struct DiffFileEntry {
    path: String,
    status: String,
    insertions: i64,
    deletions: i64,
}

#[derive(Debug, Serialize)]
struct DiffSummary {
    files_changed: i64,
    insertions: i64,
    deletions: i64,
}

#[derive(Debug, Serialize)]
struct DiffResponse {
    files: Vec<DiffFileEntry>,
    summary: DiffSummary,
    #[serde(skip_serializing_if = "Option::is_none")]
    patch: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    truncated: Option<bool>,
}

/// List sessions with optional filters (GET /api/sessions)
async fn list_sessions(
    State(state): State<AppState>,
    Query(query): Query<ListSessionsQuery>,
) -> Result<Json<Vec<SessionResponse>>, SchedulerError> {
    let sessions = db::sessions::list_filtered(
        &state.db_pool,
        query.status.as_deref(),
        query.execution_id.as_deref(),
    )
    .await?;

    Ok(Json(sessions.into_iter().map(Into::into).collect()))
}

/// Get events for a session (GET /api/sessions/{id}/events)
async fn session_events(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<Vec<EventResponse>>, SchedulerError> {
    // Verify session exists
    db::sessions::get_by_id(&state.db_pool, &id).await?;

    let events = db::events::list_by_session(&state.db_pool, &id).await?;
    Ok(Json(events.into_iter().map(Into::into).collect()))
}

/// Post a user message to a session (POST /api/sessions/{id}/message)
async fn post_message(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(req): Json<PostMessageRequest>,
) -> Result<impl IntoResponse, SchedulerError> {
    let session = db::sessions::get_by_id(&state.db_pool, &id).await?;

    // Status guard is inside deliver_message() — identical for user and agent (D17)
    let result = crate::services::messaging::deliver_message(
        &state.db_pool,
        &state.task_queue,
        &state.event_broadcast,
        &session,
        &req.message,
        None, // None = user message
    )
    .await?;

    Ok((
        StatusCode::OK,
        Json(json!({
            "event_id": result.event_id,
            "session_status": result.session_status,
            "execution_status": result.execution_status,
        })),
    ))
}

/// Cancel a session and its subtree (POST /api/sessions/{id}/cancel)
async fn cancel_session(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<serde_json::Value>, SchedulerError> {
    let session = db::sessions::get_by_id(&state.db_pool, &id).await?;

    if matches!(session.status.as_str(), "completed" | "failed" | "canceled") {
        return Err(SchedulerError::Conflict(format!(
            "session is already in terminal state: {}",
            session.status
        )));
    }

    use crate::services::cascade::{CascadeMode, terminate_subtree};

    let result = terminate_subtree(
        &state.db_pool,
        &id,
        true, // include root
        CascadeMode::Cancel,
        &state.event_broadcast,
        &state.task_queue,
    )
    .await?;

    // Notify parent that this session was canceled
    notify_parent_of_termination(&state, &session, "canceled").await?;

    // Root session: propagate to execution status
    if session.parent_session_id.is_none() {
        let execution = db::executions::get_by_id(&state.db_pool, &session.execution_id).await?;
        if !matches!(
            execution.status.as_str(),
            "completed" | "failed" | "canceled"
        ) {
            db::executions::update_status(&state.db_pool, &session.execution_id, "canceled")
                .await?;
            let exec_event = json!({"from": execution.status, "to": "canceled"});
            let event_id = db::events::insert(
                &state.db_pool,
                &session.execution_id,
                None,
                "state_change",
                &serde_json::to_string(&exec_event).unwrap(),
            )
            .await?;
            let _ = state.event_broadcast.send(EventNotification {
                execution_id: session.execution_id.clone(),
                event_id,
            });
        }
    }

    Ok(Json(json!({
        "canceled": true,
        "sessions_terminated": result.sessions_terminated
    })))
}

/// Complete a session and its subtree (POST /api/sessions/{id}/complete)
async fn complete_session(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<serde_json::Value>, SchedulerError> {
    let session = db::sessions::get_by_id(&state.db_pool, &id).await?;

    if session.status != "input-required" {
        return Err(SchedulerError::Conflict(format!(
            "session must be 'input-required' to complete (current: {})",
            session.status
        )));
    }

    use crate::services::cascade::{CascadeMode, terminate_subtree};

    let result = terminate_subtree(
        &state.db_pool,
        &id,
        true,
        CascadeMode::Release,
        &state.event_broadcast,
        &state.task_queue,
    )
    .await?;

    // Notify parent that this session was completed
    notify_parent_of_termination(&state, &session, "completed").await?;

    // Root session: propagate to execution status
    if session.parent_session_id.is_none() {
        let execution = db::executions::get_by_id(&state.db_pool, &session.execution_id).await?;
        if !matches!(
            execution.status.as_str(),
            "completed" | "failed" | "canceled"
        ) {
            db::executions::update_status(&state.db_pool, &session.execution_id, "completed")
                .await?;
            let exec_event = json!({"from": execution.status, "to": "completed"});
            let event_id = db::events::insert(
                &state.db_pool,
                &session.execution_id,
                None,
                "state_change",
                &serde_json::to_string(&exec_event).unwrap(),
            )
            .await?;
            let _ = state.event_broadcast.send(EventNotification {
                execution_id: session.execution_id.clone(),
                event_id,
            });
        }
    }

    Ok(Json(json!({
        "completed": true,
        "sessions_terminated": result.sessions_terminated
    })))
}

/// Get diff for a session's worktree (GET /api/sessions/{id}/worktree/diff)
async fn session_diff(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Query(query): Query<DiffQuery>,
) -> Result<axum::response::Response, SchedulerError> {
    let session = db::sessions::get_by_id(&state.db_pool, &id).await?;

    // Resolve diff directory: worktree_path first, then cwd fallback
    let diff_dir = session
        .worktree_path
        .as_deref()
        .or(session.cwd.as_deref())
        .ok_or_else(|| {
            SchedulerError::NotFound("session has no worktree or working directory".to_string())
        })?;

    if !std::path::Path::new(diff_dir).is_dir() {
        return Err(SchedulerError::NotFound(
            "worktree directory no longer exists".to_string(),
        ));
    }

    // Verify it's a git repo — let operational errors (timeout, spawn) pass through as 500
    let rev_parse = run_git_command(diff_dir, &["rev-parse", "--is-inside-work-tree"])
        .await
        .map_err(|e| match e {
            SchedulerError::Database(_) => e,
            _ => SchedulerError::ValidationFailed("not a git repository".to_string()),
        })?;
    if rev_parse.trim() != "true" {
        return Err(SchedulerError::ValidationFailed(
            "not a git repository".to_string(),
        ));
    }

    // Validate base ref
    let base = query.base.as_deref().unwrap_or("HEAD");
    if base.starts_with('-') {
        return Err(SchedulerError::ValidationFailed(
            "invalid base ref".to_string(),
        ));
    }

    // Get numstat (--no-renames avoids R/C parse ambiguity: renames show as delete + add)
    let numstat_output =
        run_git_command(diff_dir, &["diff", "--no-renames", "--numstat", base, "--"])
            .await
            .map_err(|e| remap_git_error(e, base))?;

    // Get name-status
    let name_status_output = run_git_command(
        diff_dir,
        &["diff", "--no-renames", "--name-status", base, "--"],
    )
    .await
    .map_err(|e| remap_git_error(e, base))?;

    let numstat_entries = parse_numstat(&numstat_output);
    let status_map = parse_name_status(&name_status_output);

    let mut files: Vec<DiffFileEntry> = Vec::new();
    let mut total_insertions: i64 = 0;
    let mut total_deletions: i64 = 0;

    for (path, ins, del) in &numstat_entries {
        let status = status_map
            .get(path.as_str())
            .cloned()
            .unwrap_or_else(|| "M".to_string());
        total_insertions += ins;
        total_deletions += del;
        files.push(DiffFileEntry {
            path: path.clone(),
            status,
            insertions: *ins,
            deletions: *del,
        });
    }

    let summary = DiffSummary {
        files_changed: files.len() as i64,
        insertions: total_insertions,
        deletions: total_deletions,
    };

    let (patch, truncated) = if query.stat.unwrap_or(false) {
        (None, None)
    } else {
        let patch_output = run_git_command(diff_dir, &["diff", "--no-renames", base, "--"])
            .await
            .map_err(|e| remap_git_error(e, base))?;

        const MAX_PATCH_SIZE: usize = 1_048_576; // 1MB
        if patch_output.len() > MAX_PATCH_SIZE {
            // Patch too large: return 413 with stat-only fallback
            let response = DiffResponse {
                files,
                summary,
                patch: None,
                truncated: Some(true),
            };
            return Ok((StatusCode::PAYLOAD_TOO_LARGE, Json(response)).into_response());
        }
        (Some(patch_output), None)
    };

    Ok(Json(DiffResponse {
        files,
        summary,
        patch,
        truncated,
    })
    .into_response())
}

/// Remap git diff errors: timeouts stay as 500, git failures become 400 (bad base ref)
fn remap_git_error(e: SchedulerError, base: &str) -> SchedulerError {
    match &e {
        // Timeouts and spawn failures should remain 500
        SchedulerError::Database(_) => e,
        _ => SchedulerError::ValidationFailed(format!("git diff failed for base ref '{base}'")),
    }
}

/// Run a git command in the given directory with a 10s timeout
async fn run_git_command(dir: &str, args: &[&str]) -> Result<String, SchedulerError> {
    let output = tokio::time::timeout(
        Duration::from_secs(10),
        tokio::process::Command::new("git")
            .arg("-C")
            .arg(dir)
            .args(args)
            .output(),
    )
    .await
    .map_err(|_| SchedulerError::Database("git command timed out after 10s".to_string()))?
    .map_err(|e| SchedulerError::Database(format!("failed to run git: {e}")))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(SchedulerError::ValidationFailed(stderr.trim().to_string()));
    }

    Ok(String::from_utf8_lossy(&output.stdout).to_string())
}

/// Parse `git diff --numstat` output into (path, insertions, deletions)
fn parse_numstat(output: &str) -> Vec<(String, i64, i64)> {
    output
        .lines()
        .filter(|line| !line.is_empty())
        .filter_map(|line| {
            let parts: Vec<&str> = line.splitn(3, '\t').collect();
            if parts.len() < 3 {
                return None;
            }
            // Binary files return `-` for counts
            let ins = parts[0].parse::<i64>().unwrap_or(0);
            let del = parts[1].parse::<i64>().unwrap_or(0);
            Some((parts[2].to_string(), ins, del))
        })
        .collect()
}

/// Parse `git diff --name-status` output into a path→status map
fn parse_name_status(output: &str) -> std::collections::HashMap<String, String> {
    output
        .lines()
        .filter(|line| !line.is_empty())
        .filter_map(|line| {
            let parts: Vec<&str> = line.splitn(2, '\t').collect();
            if parts.len() < 2 {
                return None;
            }
            // Status is the first char (M, A, D, R, etc.)
            let status = parts[0].chars().next().unwrap_or('M').to_string();
            Some((parts[1].to_string(), status))
        })
        .collect()
}

/// Push a notification to the parent session's inbox when a child is
/// externally terminated (by user cancel/complete, not agent release).
async fn notify_parent_of_termination(
    state: &AppState,
    session: &db::sessions::Session,
    terminal_status: &str,
) -> Result<(), SchedulerError> {
    if let Some(ref parent_id) = session.parent_session_id {
        let agent = db::agents::get_by_id(&state.db_pool, &session.agent_id).await;
        let agent_name = agent
            .map(|a| a.name)
            .unwrap_or_else(|_| session.agent_id.clone());
        let agent_name = agent_name.replace(['\r', '\n'], " ");
        let agent_name = agent_name.trim();

        let formatted_text = format!(
            "[session {} ({}) was {} by user]\n\nThe child session has been terminated.",
            session.id, agent_name, terminal_status
        );
        let notification = json!({
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": formatted_text}]
            },
        });
        state
            .task_queue
            .push(TaskAssignment {
                execution_id: session.execution_id.clone(),
                session_id: parent_id.clone(),
                task_payload: notification,
            })
            .await?;
        state.task_queue.wake_waiters();
    }
    Ok(())
}

/// Session routes
pub fn routes() -> Router<AppState> {
    Router::new()
        .route("/api/sessions", get(list_sessions))
        .route("/api/sessions/{id}/events", get(session_events))
        .route("/api/sessions/{id}/worktree/diff", get(session_diff))
        .route(
            "/api/sessions/{id}/message",
            axum::routing::post(post_message),
        )
        .route(
            "/api/sessions/{id}/cancel",
            axum::routing::post(cancel_session),
        )
        .route(
            "/api/sessions/{id}/complete",
            axum::routing::post(complete_session),
        )
}

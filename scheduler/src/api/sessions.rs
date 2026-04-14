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
use sqlx::Row;

/// Query parameters for listing sessions
#[derive(Debug, Deserialize)]
pub struct ListSessionsQuery {
    pub execution_id: Option<String>,
}

/// Request body for posting a user message
#[derive(Debug, Deserialize)]
pub struct PostMessageRequest {
    pub parts: Vec<serde_json::Value>,
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
struct DiffCommitEntry {
    sha: String,
    message: String,
    author: String,
    date: String,
}

#[derive(Debug, Serialize)]
struct DiffResponse {
    files: Vec<DiffFileEntry>,
    summary: DiffSummary,
    #[serde(skip_serializing_if = "Option::is_none")]
    patch: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    truncated: Option<bool>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    commits: Vec<DiffCommitEntry>,
}

/// Get a single session by ID (GET /api/sessions/{id})
async fn get_session(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<SessionResponse>, SchedulerError> {
    let session = db::sessions::get_by_id(&state.db_pool, &id).await?;
    let pending = db::sessions::count_pending_turns(&state.db_pool, &session.id).await?;
    let status = crate::api::types::derive_session_display_status(&session, pending);
    let mut resp: SessionResponse = session.into();
    resp.status = status;
    Ok(Json(resp))
}

/// List sessions with optional filters (GET /api/sessions)
async fn list_sessions(
    State(state): State<AppState>,
    Query(query): Query<ListSessionsQuery>,
) -> Result<Json<Vec<SessionResponse>>, SchedulerError> {
    let snapshot =
        db::sessions::list_with_pending(&state.db_pool, query.execution_id.as_deref()).await?;

    let responses: Vec<SessionResponse> = snapshot
        .into_iter()
        .map(|(session, pending)| {
            let status = crate::api::types::derive_session_display_status(&session, pending);
            let mut resp: SessionResponse = session.into();
            resp.status = status;
            resp
        })
        .collect();
    Ok(Json(responses))
}

/// Get events for a session (GET /api/sessions/{id}/events)
async fn session_events(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<Vec<EventResponse>>, SchedulerError> {
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
    if req.parts.is_empty() {
        return Err(SchedulerError::ValidationFailed(
            "parts must be non-empty".to_string(),
        ));
    }
    if !crate::services::messaging::has_deliverable_content(&req.parts) {
        return Err(SchedulerError::ValidationFailed(
            "parts must contain at least one non-empty text part or file part with bytes"
                .to_string(),
        ));
    }

    let session = db::sessions::get_by_id(&state.db_pool, &id).await?;

    let msg_payload = common::a2a::message_payload(common::a2a::role::USER, req.parts.clone());
    let delivery_payload = json!({"message": msg_payload});

    use crate::services::transition;
    let event_id = match transition::transition(
        &state.db_pool,
        &session.execution_id,
        &session.id,
        transition::Action::SendMessage(delivery_payload),
    )
    .await
    {
        Ok(Some(eid)) => eid,
        Ok(None) => 0,
        Err(transition::Rejected::WriteBarrier) => {
            return Err(SchedulerError::Conflict(
                "session or execution cannot accept messages".into(),
            ));
        }
        Err(e) => {
            return Err(SchedulerError::Database(format!(
                "transition failed: {e:?}"
            )));
        }
    };

    let _ = state.event_broadcast.send(EventNotification::persisted(
        session.execution_id.clone(),
        event_id,
    ));

    let platform_payload = json!({"type": "message_delivered"});
    let platform_event_id = db::events::insert(
        &state.db_pool,
        &session.execution_id,
        Some(&session.id),
        "platform",
        &serde_json::to_string(&platform_payload).unwrap(),
    )
    .await
    .unwrap_or(0);
    if platform_event_id > 0 {
        let _ = state.event_broadcast.send(EventNotification::persisted(
            session.execution_id.clone(),
            platform_event_id,
        ));
    }

    state.task_queue.wake_waiters();

    Ok((
        StatusCode::OK,
        Json(json!({
            "event_id": event_id,
            "session_status": "working",
            "execution_status": "working",
        })),
    ))
}

#[derive(Debug, Serialize)]
pub struct StopTurnResponse {
    pub stopped: bool,
    pub tasks_flushed: i64,
}

/// Terminate a session.
///
/// Sets desired=terminate and derives outcome. Cascades to children.
async fn terminate_session(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<serde_json::Value>, SchedulerError> {
    let session = db::sessions::get_by_id(&state.db_pool, &id).await?;

    use crate::services::{reconciler, transition};
    let already_terminated = match transition::transition(
        &state.db_pool,
        &session.execution_id,
        &session.id,
        transition::Action::SetDesired(transition::Desired::Terminate, "user".to_string()),
    )
    .await
    {
        Ok(_) => false,
        Err(transition::Rejected::Ratchet) => true,
        Err(e) => {
            return Err(SchedulerError::Database(format!(
                "transition failed: {e:?}"
            )));
        }
    };

    if already_terminated && session.parent_session_id.is_none() {
        let exec = db::executions::get_by_id(&state.db_pool, &session.execution_id).await?;
        if exec.outcome.is_none() {
            let mut fix_tx =
                db::executions::begin_execution_tx(&state.db_pool, &session.execution_id)
                    .await
                    .map_err(|e| SchedulerError::Database(format!("begin fix tx: {e}")))?;
            let tx_root = db::sessions::get_in_tx(&state.db_pool, &mut fix_tx, &session.id)
                .await
                .map_err(|e| SchedulerError::Database(format!("recheck root: {e}")))?;
            if tx_root.outcome.is_none() || tx_root.desired != "terminate" {
                let _ = fix_tx.rollback().await;
            } else {
                let derived = transition::derive_execution_outcome(
                    &state.db_pool,
                    &mut fix_tx,
                    &session.execution_id,
                )
                .await?;
                let fix_sql = state.db_pool.prepare_query(
                    "UPDATE executions SET desired = 'terminate', outcome = ?, \
                     updated_at = CURRENT_TIMESTAMP, completed_at = CURRENT_TIMESTAMP \
                     WHERE id = ? AND outcome IS NULL",
                );
                let _ = sqlx::query(&fix_sql)
                    .bind(&derived)
                    .bind(&session.execution_id)
                    .execute(&mut *fix_tx)
                    .await;
                reconciler::emit_execution_repair_event(
                    &state.db_pool,
                    &mut fix_tx,
                    &session.execution_id,
                    &derived,
                )
                .await;
                let _ = fix_tx.commit().await;
            }
        }
    }

    let fresh = db::sessions::get_by_id(&state.db_pool, &id).await?;
    let _ = reconciler::cascade_children(&state.db_pool, &fresh).await;

    state.task_queue.wake_waiters();

    let _ = state.event_broadcast.send(EventNotification::persisted(
        session.execution_id.clone(),
        0,
    ));

    Ok(Json(json!({"terminated": true})))
}

/// Continue from a terminal session (POST /api/sessions/{id}/continue)
/// Creates a new sibling session under the same parent.
#[derive(Debug, Deserialize)]
struct ContinueFromRequest {
    #[serde(default)]
    parts: Vec<serde_json::Value>,
}

async fn continue_from_handler(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(req): Json<ContinueFromRequest>,
) -> Result<(StatusCode, Json<serde_json::Value>), SchedulerError> {
    let pool = &state.db_pool;
    let old_session = db::sessions::get_by_id(pool, &id).await?;

    let has_content = crate::services::messaging::has_deliverable_content(&req.parts);
    if !has_content {
        return Err(SchedulerError::ValidationFailed(
            "parts must contain at least one non-empty text part".to_string(),
        ));
    }

    if old_session.outcome.is_none() {
        return Err(SchedulerError::Conflict(
            "session is not terminal (outcome IS NULL)".into(),
        ));
    }

    let parent_id = match old_session.parent_session_id.as_deref() {
        Some(pid) => pid.to_string(),
        None => {
            return Err(SchedulerError::Conflict(
                "cannot continue a root session — use re-run instead".into(),
            ));
        }
    };

    if old_session.command_token.is_some() {
        return Err(SchedulerError::Conflict(
            "session has a pending command (command_token IS NOT NULL)".into(),
        ));
    }

    if old_session.worker_id.is_some() {
        return Err(SchedulerError::Conflict(
            "session has a worker attached (worker_id IS NOT NULL)".into(),
        ));
    }

    let execution = db::executions::get_by_id(pool, &old_session.execution_id).await?;
    if execution.outcome.is_some() || execution.desired == "terminate" {
        return Err(SchedulerError::Conflict(
            "execution is not alive (desired=terminate or outcome set)".into(),
        ));
    }

    let parent = db::sessions::get_by_id(pool, &parent_id).await?;
    if parent.outcome.is_some() || parent.desired == "terminate" {
        return Err(SchedulerError::Conflict(
            "parent is not alive or is terminating".into(),
        ));
    }

    let agent = db::agents::get_by_id(pool, &old_session.agent_id)
        .await
        .map_err(|e| match e {
            SchedulerError::NotFound(_) => {
                SchedulerError::Conflict("agent config no longer exists (deleted)".into())
            }
            other => other,
        })?;
    if !agent.enabled {
        return Err(SchedulerError::Conflict(
            "agent is disabled — cannot continue".into(),
        ));
    }

    let new_session_id = uuid::Uuid::new_v4().to_string();

    let is_resumable = matches!(
        agent.agent_type.as_str(),
        "claude_sdk" | "copilot_sdk" | "codex_sdk"
    );
    let msg_payload = common::a2a::message_payload(common::a2a::role::USER, req.parts.clone());
    let prompt_payload = json!({"message": msg_payload});
    let prompt_str = serde_json::to_string(&prompt_payload)
        .map_err(|e| SchedulerError::Database(format!("serialize prompt failed: {e}")))?;
    let notif_text = format!("Child session continued from {} as {}.", id, new_session_id);
    let notification = serde_json::json!({
        "message": {
            "role": "ROLE_USER",
            "parts": [
                {"text": notif_text},
                {"data": {
                    "type": "child_continued",
                    "old_session_id": &id,
                    "new_session_id": &new_session_id,
                }}
            ]
        }
    });
    let notification_str = serde_json::to_string(&notification)
        .map_err(|e| SchedulerError::Database(format!("serialize notification failed: {e}")))?;
    let event_payload = serde_json::json!({
        "type": "child_continued",
        "old_session_id": &id,
        "new_session_id": &new_session_id,
    });
    let event_str = serde_json::to_string(&event_payload).unwrap_or_default();
    let source = format!("child_result:{new_session_id}");

    let mut tx = db::executions::begin_execution_tx(pool, &old_session.execution_id)
        .await
        .map_err(|e| SchedulerError::Database(format!("begin continue_from tx: {e}")))?;

    let recheck_sql = pool.prepare_query(
        "SELECT outcome, command_token, worker_id, agent_session_id FROM sessions WHERE id = ?",
    );
    let recheck = sqlx::query(&recheck_sql)
        .bind(&id)
        .fetch_one(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("recheck old session: {e}")))?;

    let recheck_outcome: Option<String> = recheck.get("outcome");
    let recheck_cmd: Option<String> = recheck.get("command_token");
    let recheck_wid: Option<String> = recheck.get("worker_id");
    if recheck_outcome.is_none() || recheck_cmd.is_some() || recheck_wid.is_some() {
        let _ = tx.rollback().await;
        return Err(SchedulerError::Conflict(
            "precondition race: old session state changed".into(),
        ));
    }

    let exec_recheck_sql =
        pool.prepare_query("SELECT desired, outcome FROM executions WHERE id = ?");
    let exec_row = sqlx::query(&exec_recheck_sql)
        .bind(&old_session.execution_id)
        .fetch_one(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("recheck execution: {e}")))?;
    let exec_desired: String = exec_row.get("desired");
    let exec_outcome: Option<String> = exec_row.get("outcome");
    if exec_outcome.is_some() || exec_desired != "run" {
        let _ = tx.rollback().await;
        return Err(SchedulerError::Conflict(
            "precondition race: execution is no longer alive/running".into(),
        ));
    }

    let parent_recheck_sql =
        pool.prepare_query("SELECT desired, outcome FROM sessions WHERE id = ?");
    let parent_row = sqlx::query(&parent_recheck_sql)
        .bind(&parent_id)
        .fetch_one(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("recheck parent: {e}")))?;
    let parent_desired: String = parent_row.get("desired");
    let parent_outcome: Option<String> = parent_row.get("outcome");
    if parent_outcome.is_some() || parent_desired == "terminate" {
        let _ = tx.rollback().await;
        return Err(SchedulerError::Conflict(
            "precondition race: parent is no longer alive or is terminating".into(),
        ));
    }

    let agent_sql =
        pool.prepare_query("SELECT enabled FROM agents WHERE id = ? AND deleted_at IS NULL");
    let agent_row = sqlx::query(&agent_sql)
        .bind(&old_session.agent_id)
        .fetch_optional(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("recheck agent: {e}")))?;
    match agent_row {
        None => {
            let _ = tx.rollback().await;
            return Err(SchedulerError::Conflict(
                "agent deleted during continue_from".into(),
            ));
        }
        Some(row) => {
            let enabled: bool = row
                .try_get("enabled")
                .unwrap_or_else(|_| row.get::<i32, _>("enabled") != 0);
            if !enabled {
                let _ = tx.rollback().await;
                return Err(SchedulerError::Conflict(
                    "agent disabled during continue_from".into(),
                ));
            }
        }
    }

    let slug_sql =
        pool.prepare_query("SELECT slug FROM sessions WHERE parent_session_id = ? AND slug != ''");
    let slug_rows = sqlx::query(&slug_sql)
        .bind(&parent_id)
        .fetch_all(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("sibling slugs: {e}")))?;
    let existing_slugs: Vec<String> = slug_rows.iter().map(|r| r.get("slug")).collect();
    let slug = crate::slugs::generate_slug(&existing_slugs);

    let create_sql = pool.prepare_query(
        "INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, cwd, \
         worktree_path, base_commit_sha, slug, continued_from_session_id) \
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
    );
    sqlx::query(&create_sql)
        .bind(&new_session_id)
        .bind(&old_session.execution_id)
        .bind(Some(&parent_id))
        .bind(&old_session.agent_id)
        .bind(old_session.cwd.as_deref())
        .bind(old_session.worktree_path.as_deref())
        .bind(old_session.base_commit_sha.as_deref())
        .bind(&slug)
        .bind(&id)
        .execute(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("create continued session: {e}")))?;

    if old_session.worktree_path.is_some() {
        let clear_wt_sql = pool.prepare_query(
            "UPDATE sessions SET worktree_path = NULL, base_commit_sha = NULL, \
             updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        );
        sqlx::query(&clear_wt_sql)
            .bind(&id)
            .execute(&mut *tx)
            .await
            .map_err(|e| SchedulerError::Database(format!("clear old worktree: {e}")))?;
    }

    if is_resumable {
        let recheck_asid: Option<String> = recheck.get("agent_session_id");
        if let Some(ref asid) = recheck_asid {
            let set_sql = pool.prepare_query(
                "UPDATE sessions SET agent_session_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            );
            sqlx::query(&set_sql)
                .bind(asid)
                .bind(&new_session_id)
                .execute(&mut *tx)
                .await
                .map_err(|e| SchedulerError::Database(format!("transfer agent_session_id: {e}")))?;

            let clear_sql = pool.prepare_query(
                "UPDATE sessions SET agent_session_id = NULL, updated_at = CURRENT_TIMESTAMP \
                 WHERE id = ? AND agent_session_id = ?",
            );
            let clear_result = sqlx::query(&clear_sql)
                .bind(&id)
                .bind(asid)
                .execute(&mut *tx)
                .await
                .map_err(|e| {
                    SchedulerError::Database(format!("clear old agent_session_id: {e}"))
                })?;

            if clear_result.rows_affected() == 0 {
                let _ = tx.rollback().await;
                return Err(SchedulerError::Conflict(
                    "concurrent continue_from: SDK session already transferred".into(),
                ));
            }
        }
    }

    let prompt_event_sql = pool.prepare_query(
        "INSERT INTO events (execution_id, session_id, event_type, payload) \
         VALUES (?, ?, 'message', ?)",
    );
    sqlx::query(&prompt_event_sql)
        .bind(&old_session.execution_id)
        .bind(&new_session_id)
        .bind(&prompt_str)
        .execute(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("insert prompt event: {e}")))?;

    let enqueue_sql = pool.prepare_query(
        "INSERT INTO task_queue (execution_id, session_id, task_payload, source) VALUES (?, ?, ?, ?)",
    );
    sqlx::query(&enqueue_sql)
        .bind(&old_session.execution_id)
        .bind(&new_session_id)
        .bind(&prompt_str)
        .bind("user")
        .execute(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("enqueue prompt: {e}")))?;

    if parent_desired == "stop" {
        let resume_sql = pool.prepare_query(
            "UPDATE sessions SET desired = 'run', desired_by = 'system:continue_from_notify', \
             desired_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP \
             WHERE id = ? AND desired = 'stop'",
        );
        let resume_result = sqlx::query(&resume_sql)
            .bind(&parent_id)
            .execute(&mut *tx)
            .await;

        if resume_result.as_ref().is_ok_and(|r| r.rows_affected() > 0) {
            let resume_event =
                serde_json::json!({"desired": "run", "desired_by": "system:continue_from_notify"});
            let resume_event_str = serde_json::to_string(&resume_event).unwrap_or_default();
            let sc_sql = pool.prepare_query(
                "INSERT INTO events (execution_id, session_id, event_type, payload) \
                 VALUES (?, ?, 'state_change', ?)",
            );
            sqlx::query(&sc_sql)
                .bind(&old_session.execution_id)
                .bind(&parent_id)
                .bind(&resume_event_str)
                .execute(&mut *tx)
                .await
                .map_err(|e| {
                    SchedulerError::Database(format!("auto-resume state_change event: {e}"))
                })?;
        }
    }
    sqlx::query(&enqueue_sql)
        .bind(&old_session.execution_id)
        .bind(&parent_id)
        .bind(&notification_str)
        .bind(&source)
        .execute(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("enqueue parent notification: {e}")))?;

    let event_sql = pool.prepare_query(
        "INSERT INTO events (execution_id, session_id, event_type, payload) VALUES (?, ?, 'platform', ?)",
    );
    sqlx::query(&event_sql)
        .bind(&old_session.execution_id)
        .bind(&parent_id)
        .bind(&event_str)
        .execute(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("insert platform event: {e}")))?;

    tx.commit()
        .await
        .map_err(|e| SchedulerError::Database(format!("commit continue_from tx: {e}")))?;

    state.task_queue.wake_waiters();
    let _ = state
        .event_broadcast
        .send(crate::app::EventNotification::persisted(
            old_session.execution_id.clone(),
            0,
        ));

    Ok((
        StatusCode::CREATED,
        Json(json!({"session_id": new_session_id})),
    ))
}

/// Sets desired=stop on the session.
async fn stop_turn_handler(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<StopTurnResponse>, SchedulerError> {
    let session = db::sessions::get_by_id(&state.db_pool, &id).await?;

    use crate::services::transition;
    match transition::transition(
        &state.db_pool,
        &session.execution_id,
        &session.id,
        transition::Action::SetDesired(transition::Desired::Stop, "user".to_string()),
    )
    .await
    {
        Ok(_) => {}
        Err(transition::Rejected::Ratchet) => {
            return Err(SchedulerError::Conflict(
                "session already terminated".into(),
            ));
        }
        Err(e) => {
            return Err(SchedulerError::Database(format!(
                "transition failed: {e:?}"
            )));
        }
    }

    state.task_queue.wake_waiters();

    let _ = state.event_broadcast.send(EventNotification::persisted(
        session.execution_id.clone(),
        0,
    ));

    Ok(Json(StopTurnResponse {
        stopped: true,
        tasks_flushed: 0,
    }))
}

#[derive(Debug, Serialize)]
struct WorktreeInfoResponse {
    path: String,
    branch: Option<String>,
    head_sha: Option<String>,
    exists: bool,
}

/// Query parameters for DELETE /api/sessions/{id}/worktree
#[derive(Debug, Deserialize)]
struct DeleteWorktreeQuery {
    dry_run: Option<bool>,
    delete_branch: Option<bool>,
}

/// Delete a session's worktree (DELETE /api/sessions/{id}/worktree)
async fn delete_session_worktree(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Query(params): Query<DeleteWorktreeQuery>,
) -> Result<Json<serde_json::Value>, SchedulerError> {
    let session = db::sessions::get_by_id(&state.db_pool, &id).await?;

    if session.outcome.is_none() || session.worker_id.is_some() || session.command_token.is_some() {
        return Err(SchedulerError::Conflict(
            "worktree cleanup only allowed on terminal finalized sessions \
             (outcome set, no worker, no pending command)"
                .to_string(),
        ));
    }

    let wt_path = session
        .worktree_path
        .as_deref()
        .ok_or_else(|| SchedulerError::NotFound("session has no worktree".to_string()))?;

    let dry_run = params.dry_run.unwrap_or(false);
    let delete_branch = params.delete_branch.unwrap_or(false);

    if dry_run {
        let dir_exists = matches!(std::fs::metadata(wt_path), Ok(m) if m.is_dir());

        if !dir_exists {
            return Ok(Json(json!({
                "deleted": false,
                "dry_run": true,
                "dirty": null,
                "dirty_summary": null,
                "branch": null,
                "directory_missing": true,
            })));
        }

        let dirty_info = run_git_command(wt_path, &["status", "--porcelain"])
            .await
            .ok();
        let dirty = dirty_info.as_ref().map(|output| !output.trim().is_empty());
        let dirty_summary = dirty_info.as_ref().and_then(|output| {
            let trimmed = output.trim();
            if trimmed.is_empty() {
                return None;
            }
            let mut modified = 0i32;
            let mut untracked = 0i32;
            for line in trimmed.lines() {
                if line.starts_with("??") {
                    untracked += 1;
                } else {
                    modified += 1;
                }
            }
            let mut parts = Vec::new();
            if modified > 0 {
                parts.push(format!("{modified} modified"));
            }
            if untracked > 0 {
                parts.push(format!("{untracked} untracked"));
            }
            Some(parts.join(", "))
        });

        let branch = run_git_command(wt_path, &["rev-parse", "--abbrev-ref", "HEAD"])
            .await
            .ok()
            .map(|o| o.trim().to_string())
            .and_then(|b| if b == "HEAD" { None } else { Some(b) });

        return Ok(Json(json!({
            "deleted": false,
            "dry_run": true,
            "dirty": dirty,
            "dirty_summary": dirty_summary,
            "branch": branch,
            "directory_missing": false,
        })));
    }

    let branch_name = run_git_command(wt_path, &["rev-parse", "--abbrev-ref", "HEAD"])
        .await
        .ok()
        .map(|o| o.trim().to_string())
        .and_then(|b| if b == "HEAD" { None } else { Some(b) });

    let execution = db::executions::get_by_id(&state.db_pool, &session.execution_id).await?;
    let project_path = if let Some(ref pid) = execution.project_id {
        match db::projects::get_by_id(&state.db_pool, pid).await {
            Ok(p) => Some(p.path),
            Err(SchedulerError::NotFound(_)) => None,
            Err(e) => return Err(e),
        }
    } else {
        None
    };

    if let Some(ref proj) = project_path {
        let _ = run_git_command(proj, &["worktree", "remove", "--force", wt_path]).await;
        let _ = run_git_command(proj, &["worktree", "prune"]).await;
    }

    let _ = tokio::fs::remove_dir_all(wt_path).await;

    match std::fs::metadata(wt_path) {
        Ok(_) => {
            return Err(SchedulerError::Database(format!(
                "failed to remove worktree directory: {wt_path}"
            )));
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => {
            return Err(SchedulerError::Database(format!(
                "cannot verify worktree removal ({e}): {wt_path}"
            )));
        }
    }

    let pre_clear = db::sessions::get_by_id(&state.db_pool, &id).await?;
    if pre_clear.outcome.is_none()
        || pre_clear.worker_id.is_some()
        || pre_clear.command_token.is_some()
    {
        return Err(SchedulerError::Conflict(
            "session was recovered during worktree cleanup — aborting".to_string(),
        ));
    }

    let mut branch_deleted = false;
    if delete_branch
        && let Some(ref branch) = branch_name
        && let Some(ref proj) = project_path
        && run_git_command(proj, &["branch", "-D", branch])
            .await
            .is_ok()
    {
        branch_deleted = true;
    }

    let rows_affected = db::sessions::clear_worktree_path(&state.db_pool, &id).await?;

    if rows_affected == 0 {
        let refreshed = db::sessions::get_by_id(&state.db_pool, &id).await?;
        if refreshed.worktree_path.is_none() {
        } else {
            return Err(SchedulerError::Conflict(
                "worktree cleanup only allowed on terminal finalized sessions \
                 (outcome set, no worker, no pending command)"
                    .to_string(),
            ));
        }
    }

    Ok(Json(json!({
        "deleted": true,
        "path": wt_path,
        "branch_deleted": branch_deleted,
        "branch": branch_name,
    })))
}

/// Get worktree info for a session (GET /api/sessions/{id}/worktree)
async fn session_worktree_info(
    State(state): State<AppState>,
    Path(id): Path<String>,
) -> Result<Json<WorktreeInfoResponse>, SchedulerError> {
    let session = db::sessions::get_by_id(&state.db_pool, &id).await?;

    let wt_path = session
        .worktree_path
        .as_deref()
        .ok_or_else(|| SchedulerError::NotFound("session has no worktree".to_string()))?;

    let exists = match std::fs::metadata(wt_path) {
        Ok(m) => m.is_dir(),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => false,
        Err(e) => {
            return Err(SchedulerError::Database(format!(
                "cannot stat worktree directory ({e}): {wt_path}"
            )));
        }
    };

    if !exists {
        return Ok(Json(WorktreeInfoResponse {
            path: wt_path.to_string(),
            branch: None,
            head_sha: None,
            exists: false,
        }));
    }

    let branch = run_git_command(wt_path, &["rev-parse", "--abbrev-ref", "HEAD"])
        .await
        .ok()
        .map(|o| o.trim().to_string())
        .and_then(|b| if b == "HEAD" { None } else { Some(b) });

    let head_sha = run_git_command(wt_path, &["rev-parse", "HEAD"])
        .await
        .ok()
        .map(|o| o.trim().to_string());

    Ok(Json(WorktreeInfoResponse {
        path: wt_path.to_string(),
        branch,
        head_sha,
        exists: true,
    }))
}

/// Get diff for a session's worktree (GET /api/sessions/{id}/worktree/diff)
async fn session_diff(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Query(query): Query<DiffQuery>,
) -> Result<axum::response::Response, SchedulerError> {
    let session = db::sessions::get_by_id(&state.db_pool, &id).await?;

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

    let base = query
        .base
        .as_deref()
        .or(session.base_commit_sha.as_deref())
        .unwrap_or("HEAD");
    if base.starts_with('-') {
        return Err(SchedulerError::ValidationFailed(
            "invalid base ref".to_string(),
        ));
    }

    let numstat_output =
        run_git_command(diff_dir, &["diff", "--no-renames", "--numstat", base, "--"])
            .await
            .map_err(|e| remap_git_error(e, base))?;

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

    let commit_base = session.base_commit_sha.as_deref().unwrap_or("HEAD");
    let commits = run_git_command(
        diff_dir,
        &[
            "log",
            "--format=%H%x00%s%x00%an%x00%aI",
            &format!("{commit_base}..HEAD"),
        ],
    )
    .await
    .map(|output| parse_commit_log(&output))
    .unwrap_or_default();

    let (patch, truncated) = if query.stat.unwrap_or(false) {
        (None, None)
    } else {
        let patch_output = run_git_command(diff_dir, &["diff", "--no-renames", base, "--"])
            .await
            .map_err(|e| remap_git_error(e, base))?;

        const MAX_PATCH_SIZE: usize = 1_048_576;
        if patch_output.len() > MAX_PATCH_SIZE {
            let response = DiffResponse {
                files,
                summary,
                patch: None,
                truncated: Some(true),
                commits,
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
        commits,
    })
    .into_response())
}

/// Remap git diff errors: timeouts stay as 500, git failures become 400 (bad base ref)
fn remap_git_error(e: SchedulerError, base: &str) -> SchedulerError {
    match &e {
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
            let status = parts[0].chars().next().unwrap_or('M').to_string();
            Some((parts[1].to_string(), status))
        })
        .collect()
}

/// Parse `git log --format=%H%x00%s%x00%an%x00%aI` output into commit entries.
/// Fields are NUL-separated within each line to avoid ambiguity with pipes
/// or other characters that may appear in commit messages.
fn parse_commit_log(output: &str) -> Vec<DiffCommitEntry> {
    output
        .lines()
        .filter(|line| !line.is_empty())
        .filter_map(|line| {
            let parts: Vec<&str> = line.splitn(4, '\0').collect();
            if parts.len() < 4 {
                return None;
            }
            Some(DiffCommitEntry {
                sha: parts[0].to_string(),
                message: parts[1].to_string(),
                author: parts[2].to_string(),
                date: parts[3].to_string(),
            })
        })
        .collect()
}

/// Request body for manual recovery
#[derive(Debug, Deserialize)]
pub struct RecoverSessionRequest {
    pub message: Option<String>,
}

#[derive(Debug, Serialize)]
pub struct RecoverSessionResponse {
    pub session: SessionResponse,
    pub execution_recovered: bool,
}

/// Attempt to recover a failed session (POST /api/sessions/{id}/recover)
async fn recover_session_handler(
    State(state): State<AppState>,
    Path(id): Path<String>,
    Json(req): Json<RecoverSessionRequest>,
) -> Result<impl IntoResponse, SchedulerError> {
    if let Some(ref msg) = req.message
        && msg.chars().count() > 10_000
    {
        return Err(SchedulerError::ValidationFailed(
            "message must be 10,000 characters or fewer".to_string(),
        ));
    }

    let session = db::sessions::get_by_id(&state.db_pool, &id).await?;

    if session.outcome.as_deref() != Some("failed") {
        return Err(SchedulerError::ValidationFailed(
            "session is not in failed state".to_string(),
        ));
    }

    let pool = &state.db_pool;

    let agent = db::agents::get_by_id(pool, &session.agent_id)
        .await
        .map_err(|e| match e {
            SchedulerError::NotFound(_) => SchedulerError::ValidationFailed(
                "agent no longer exists — cannot recover".to_string(),
            ),
            other => other,
        })?;
    if !agent.enabled {
        return Err(SchedulerError::ValidationFailed(
            "agent is disabled — cannot recover".to_string(),
        ));
    }

    let is_resumable = matches!(
        agent.agent_type.as_str(),
        "claude_sdk" | "copilot_sdk" | "codex_sdk"
    );
    let clear_agent_session_id = !is_resumable && session.agent_session_id.is_some();
    if !is_resumable && req.message.is_none() {
        return Err(SchedulerError::ValidationFailed(
            "non-resumable agent requires a message for recovery".to_string(),
        ));
    }

    let exec = db::executions::get_by_id(pool, &session.execution_id).await?;
    let is_root = session.parent_session_id.is_none();
    if !is_root && (exec.desired == "terminate" || exec.outcome.is_some()) {
        return Err(SchedulerError::ValidationFailed(
            "cannot recover child session under terminal or terminating execution".to_string(),
        ));
    }

    let msg_payload_json = if let Some(ref msg) = req.message {
        let msg_payload = common::a2a::message_payload(
            common::a2a::role::USER,
            vec![common::a2a::text_part(msg)],
        );
        Some(
            serde_json::to_string(&serde_json::json!({"message": msg_payload}))
                .map_err(|e| SchedulerError::Database(format!("serialize failed: {e}")))?,
        )
    } else {
        None
    };

    let mut tx = db::executions::begin_execution_tx(pool, &session.execution_id)
        .await
        .map_err(|e| SchedulerError::Database(format!("begin recover tx: {e}")))?;

    if !is_root {
        let tx_exec = db::executions::get_in_tx(pool, &mut tx, &session.execution_id)
            .await
            .map_err(|e| SchedulerError::Database(format!("execution recheck: {e}")))?;
        if tx_exec.desired == "terminate" || tx_exec.outcome.is_some() {
            let _ = tx.rollback().await;
            return Err(SchedulerError::ValidationFailed(
                "execution became terminal during recovery".to_string(),
            ));
        }
    }

    let agent_sql =
        pool.prepare_query("SELECT enabled FROM agents WHERE id = ? AND deleted_at IS NULL");
    let agent_row = sqlx::query(&agent_sql)
        .bind(&session.agent_id)
        .fetch_optional(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("recheck agent: {e}")))?;
    match agent_row {
        None => {
            let _ = tx.rollback().await;
            return Err(SchedulerError::ValidationFailed(
                "agent deleted during recovery".into(),
            ));
        }
        Some(row) => {
            let enabled: bool = row
                .try_get("enabled")
                .unwrap_or_else(|_| row.get::<i32, _>("enabled") != 0);
            if !enabled {
                let _ = tx.rollback().await;
                return Err(SchedulerError::ValidationFailed(
                    "agent disabled during recovery".into(),
                ));
            }
        }
    }

    let sql = if clear_agent_session_id {
        pool.prepare_query(
            "UPDATE sessions SET desired = 'run', executor_state = 'unassigned', \
             outcome = NULL, completed_at = NULL, recovery_attempts = 0, \
             parent_notified = FALSE, desired_by = 'user', \
             desired_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP, \
             worker_id = NULL, command_token = NULL, command_type = NULL, \
             command_at = NULL, command_has_payload = FALSE, agent_session_id = NULL \
             WHERE id = ? AND outcome = 'failed'",
        )
    } else {
        pool.prepare_query(
            "UPDATE sessions SET desired = 'run', executor_state = 'unassigned', \
             outcome = NULL, completed_at = NULL, recovery_attempts = 0, \
             parent_notified = FALSE, desired_by = 'user', \
             desired_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP, \
             worker_id = NULL, command_token = NULL, command_type = NULL, \
             command_at = NULL, command_has_payload = FALSE \
             WHERE id = ? AND outcome = 'failed'",
        )
    };
    let result = sqlx::query(&sql)
        .bind(&id)
        .execute(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("manual recovery failed: {e}")))?;

    if result.rows_affected() == 0 {
        let _ = tx.rollback().await;
        return Err(SchedulerError::Conflict(
            "session state changed concurrently".to_string(),
        ));
    }

    if let Some(ref payload_str) = msg_payload_json {
        let enqueue_sql = pool.prepare_query(
            "INSERT INTO task_queue (execution_id, session_id, task_payload, source) VALUES (?, ?, ?, ?)",
        );
        sqlx::query(&enqueue_sql)
            .bind(&session.execution_id)
            .bind(&id)
            .bind(payload_str)
            .bind(None::<&str>)
            .execute(&mut *tx)
            .await
            .map_err(|e| SchedulerError::Database(format!("enqueue message failed: {e}")))?;
    }

    let execution_recovered = if is_root && (exec.desired == "terminate" || exec.outcome.is_some())
    {
        let tx_exec = db::executions::get_in_tx(pool, &mut tx, &session.execution_id)
            .await
            .map_err(|e| SchedulerError::Database(format!("execution recheck: {e}")))?;
        if tx_exec.desired == "terminate" || tx_exec.outcome.is_some() {
            let exec_sql = pool.prepare_query(
                "UPDATE executions SET desired = 'run', outcome = NULL, \
                 completed_at = NULL, updated_at = CURRENT_TIMESTAMP \
                 WHERE id = ? AND (desired = 'terminate' OR outcome IS NOT NULL)",
            );
            sqlx::query(&exec_sql)
                .bind(&session.execution_id)
                .execute(&mut *tx)
                .await
                .map_err(|e| SchedulerError::Database(format!("execution recovery failed: {e}")))?;
            true
        } else {
            false
        }
    } else {
        false
    };

    let recovery_event = serde_json::json!({"desired": "run", "recovery": true});
    let recovery_event_str = serde_json::to_string(&recovery_event).unwrap_or_default();
    let evt_sql = pool.prepare_query(
        "INSERT INTO events (execution_id, session_id, event_type, payload) VALUES (?, ?, 'state_change', ?)",
    );
    let _ = sqlx::query(&evt_sql)
        .bind(&session.execution_id)
        .bind(&session.id)
        .bind(&recovery_event_str)
        .execute(&mut *tx)
        .await;

    if execution_recovered {
        let exec_evt_sql = pool.prepare_query(
            "INSERT INTO events (execution_id, session_id, event_type, payload) VALUES (?, NULL, 'state_change', ?)",
        );
        let _ = sqlx::query(&exec_evt_sql)
            .bind(&session.execution_id)
            .bind(&recovery_event_str)
            .execute(&mut *tx)
            .await;
    }

    tx.commit()
        .await
        .map_err(|e| SchedulerError::Database(format!("commit recover tx: {e}")))?;

    state.task_queue.wake_waiters();
    let _ = state
        .event_broadcast
        .send(crate::app::EventNotification::persisted(
            session.execution_id.clone(),
            0,
        ));

    let updated = db::sessions::get_by_id(pool, &id).await?;
    let pending = db::sessions::count_pending_turns(pool, &updated.id).await?;
    let status = crate::api::types::derive_session_display_status(&updated, pending);
    let mut resp: SessionResponse = updated.into();
    resp.status = status;
    Ok((
        StatusCode::OK,
        Json(RecoverSessionResponse {
            session: resp,
            execution_recovered,
        }),
    ))
}

/// Session routes
pub fn routes() -> Router<AppState> {
    Router::new()
        .route("/api/sessions", get(list_sessions))
        .route("/api/sessions/{id}", get(get_session))
        .route("/api/sessions/{id}/events", get(session_events))
        .route(
            "/api/sessions/{id}/worktree",
            get(session_worktree_info).delete(delete_session_worktree),
        )
        .route("/api/sessions/{id}/worktree/diff", get(session_diff))
        .route(
            "/api/sessions/{id}/message",
            axum::routing::post(post_message),
        )
        .route(
            "/api/sessions/{id}/terminate",
            axum::routing::post(terminate_session),
        )
        .route(
            "/api/sessions/{id}/stop",
            axum::routing::post(stop_turn_handler),
        )
        .route(
            "/api/sessions/{id}/continue",
            axum::routing::post(continue_from_handler),
        )
        .route(
            "/api/sessions/{id}/recover",
            axum::routing::post(recover_session_handler),
        )
}

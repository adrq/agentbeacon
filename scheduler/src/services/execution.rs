use std::path::Path;

use serde_json::{Value as JsonValue, json};
use uuid::Uuid;

use crate::db;
use crate::db::DbPool;
use crate::error::SchedulerError;
use crate::queue::{TaskAssignment, TaskQueue};

pub struct CreateExecutionResult {
    pub execution: db::Execution,
    pub session_id: String,
    pub warning: Option<String>,
}

#[allow(clippy::too_many_arguments)]
pub async fn create_execution(
    db_pool: &DbPool,
    task_queue: &TaskQueue,
    agent_id: &str,
    prompt: &str,
    project_id: Option<&str>,
    title: Option<&str>,
    cwd: Option<&str>,
    branch: Option<&str>,
    context_id: Option<&str>,
) -> Result<CreateExecutionResult, SchedulerError> {
    // Validate prompt non-empty
    if prompt.trim().is_empty() {
        return Err(SchedulerError::ValidationFailed(
            "prompt is required".to_string(),
        ));
    }

    // Look up agent — re-map NotFound to ValidationFailed (400, not 404)
    let agent = db::agents::get_by_id(db_pool, agent_id)
        .await
        .map_err(|e| match e {
            SchedulerError::NotFound(_) => {
                SchedulerError::ValidationFailed(format!("agent not found: {agent_id}"))
            }
            other => other,
        })?;
    if !agent.enabled {
        return Err(SchedulerError::ValidationFailed(format!(
            "agent is disabled: {}",
            agent_id
        )));
    }

    // Mutual exclusivity: branch and cwd
    if branch.is_some() && cwd.is_some() {
        return Err(SchedulerError::ValidationFailed(
            "branch and cwd are mutually exclusive".to_string(),
        ));
    }

    // At least project_id or cwd required
    if project_id.is_none() && cwd.is_none() {
        return Err(SchedulerError::ValidationFailed(
            "at least project_id or cwd is required".to_string(),
        ));
    }

    // branch requires project_id
    if branch.is_some() && project_id.is_none() {
        return Err(SchedulerError::ValidationFailed(
            "branch requires project_id".to_string(),
        ));
    }

    // Validate branch name
    if let Some(b) = branch {
        validate_branch_name(b)?;
    }

    // Validate cwd if provided
    let resolved_cwd_from_param = if let Some(dir) = cwd {
        let dir = dir.trim();
        if dir.is_empty() {
            return Err(SchedulerError::ValidationFailed(
                "cwd cannot be empty".to_string(),
            ));
        }
        if !Path::new(dir).is_absolute() {
            return Err(SchedulerError::ValidationFailed(format!(
                "cwd must be an absolute path: {dir}"
            )));
        }
        let canonical = std::fs::canonicalize(dir)
            .map_err(|_| SchedulerError::ValidationFailed(format!("path does not exist: {dir}")))?;
        if !canonical.is_dir() {
            return Err(SchedulerError::ValidationFailed(format!(
                "path is not a directory: {dir}"
            )));
        }
        Some(canonical.to_string_lossy().to_string())
    } else {
        None
    };

    // Look up project if provided
    let project = if let Some(pid) = project_id {
        Some(
            db::projects::get_by_id(db_pool, pid)
                .await
                .map_err(|e| match e {
                    SchedulerError::NotFound(_) => {
                        SchedulerError::ValidationFailed(format!("project not found: {pid}"))
                    }
                    other => other,
                })?,
        )
    } else {
        None
    };

    // branch requires git-backed project
    if branch.is_some()
        && let Some(ref proj) = project
        && !Path::new(&proj.path).join(".git").is_dir()
    {
        return Err(SchedulerError::ValidationFailed(
            "branch requires a git-backed project".to_string(),
        ));
    }

    let execution_id = Uuid::new_v4().to_string();
    let session_id = Uuid::new_v4().to_string();
    let effective_context_id = context_id
        .map(|s| s.to_string())
        .unwrap_or_else(|| execution_id.clone());

    // Working directory resolution + worktree creation
    let (session_cwd, worktree_path) = if let Some(cwd_val) = resolved_cwd_from_param {
        // A) Explicit cwd takes priority — no worktree
        (cwd_val, None)
    } else if let Some(b) = branch {
        // B) Explicit branch — create worktree with named branch
        let proj = project.as_ref().unwrap();
        let wt_path = common::execution_dir(&proj.id, &execution_id);
        let wt_path_str = wt_path.to_string_lossy().to_string();
        create_worktree(&proj.path, &wt_path, Some(b))?;
        (wt_path_str.clone(), Some(wt_path_str))
    } else if let Some(ref proj) = project {
        // C) Project exists — auto-worktree for git repos with commits
        let is_git = Path::new(&proj.path).join(".git").is_dir();
        let has_commits = if is_git {
            let output = std::process::Command::new("git")
                .args(["-C", &proj.path, "rev-parse", "HEAD"])
                .output()
                .map_err(|e| SchedulerError::Database(format!("failed to run git: {e}")))?;
            output.status.success()
        } else {
            false
        };

        if has_commits {
            let wt_path = common::execution_dir(&proj.id, &execution_id);
            let wt_path_str = wt_path.to_string_lossy().to_string();
            create_worktree(&proj.path, &wt_path, None)?;
            (wt_path_str.clone(), Some(wt_path_str))
        } else {
            // Non-git or empty git repo (no commits): use project path directly
            (proj.path.clone(), None)
        }
    } else {
        // D) Should not reach here due to validation above
        return Err(SchedulerError::ValidationFailed(
            "at least project_id or cwd is required".to_string(),
        ));
    };

    // Persist to DB and enqueue. If a worktree was created, clean it up on failure
    // so retries don't fail with "branch already exists".
    let result = persist_and_enqueue(
        db_pool,
        task_queue,
        &execution_id,
        &effective_context_id,
        prompt,
        project_id,
        title,
        worktree_path.as_deref(),
        &session_id,
        &agent,
        &session_cwd,
    )
    .await;

    if let Err(ref e) = result
        && let Some(ref wt) = worktree_path
    {
        tracing::warn!(worktree = %wt, error = %e, "cleaning up worktree after failure");
        cleanup_worktree(project.as_ref().map(|p| p.path.as_str()), wt, branch);
    }

    result
}

#[allow(clippy::too_many_arguments)]
async fn persist_and_enqueue(
    db_pool: &DbPool,
    task_queue: &TaskQueue,
    execution_id: &str,
    context_id: &str,
    prompt: &str,
    project_id: Option<&str>,
    title: Option<&str>,
    worktree_path: Option<&str>,
    session_id: &str,
    agent: &db::agents::Agent,
    session_cwd: &str,
) -> Result<CreateExecutionResult, SchedulerError> {
    // Store input as plain prompt string
    db::executions::create(
        db_pool,
        execution_id,
        context_id,
        prompt,
        project_id,
        None, // parent_execution_id
        title,
        worktree_path,
    )
    .await?;

    // Create lead session with cwd
    db::sessions::create(
        db_pool,
        session_id,
        execution_id,
        &agent.id,
        None,
        Some(session_cwd),
    )
    .await?;

    // Record initial state_change event
    let state_event = json!({"from": null, "to": "submitted"});
    db::events::insert(
        db_pool,
        execution_id,
        None,
        "state_change",
        &serde_json::to_string(&state_event).unwrap(),
    )
    .await?;

    // Concurrent execution warning
    let warning = if let Some(pid) = project_id {
        let count = db::executions::count_non_terminal_by_project(db_pool, pid).await?;
        // count includes the one we just created, so warn if > 1
        if count > 1 {
            Some("Another execution is active for this project".to_string())
        } else {
            None
        }
    } else {
        None
    };

    // Build A2A task payload for worker dispatch
    let agent_config: JsonValue = serde_json::from_str(&agent.config).unwrap_or_else(|_| json!({}));
    let sandbox_config: JsonValue = agent
        .sandbox_config
        .as_ref()
        .and_then(|s| serde_json::from_str(s).ok())
        .unwrap_or(JsonValue::Null);

    // Wrap prompt in A2A message format for task_payload only
    let a2a_message = json!({
        "role": "user",
        "parts": [{"kind": "text", "text": prompt}]
    });

    let task_payload = json!({
        "agent_id": agent.id,
        "agent_type": agent.agent_type,
        "agent_config": agent_config,
        "sandbox_config": sandbox_config,
        "message": a2a_message,
        "cwd": session_cwd,
    });

    // Fetch the created execution before enqueue — if this read fails,
    // cleanup is safe because nothing has been queued yet.
    let execution = db::executions::get_by_id(db_pool, execution_id).await?;

    // Enqueue last: the irreversible side effect happens only after all
    // DB operations succeed, so cleanup never races a queued task.
    task_queue
        .push(TaskAssignment {
            execution_id: execution_id.to_string(),
            session_id: session_id.to_string(),
            task_payload,
        })
        .await?;

    Ok(CreateExecutionResult {
        execution,
        session_id: session_id.to_string(),
        warning,
    })
}

/// Create a git worktree at `worktree_path` from the repo at `project_path`.
/// When `branch` is `None`, creates a detached HEAD worktree.
/// When `branch` is `Some(name)`, creates a new branch `beacon/{name}`.
fn create_worktree(
    project_path: &str,
    worktree_path: &Path,
    branch: Option<&str>,
) -> Result<(), SchedulerError> {
    if let Some(parent) = worktree_path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| {
            SchedulerError::Database(format!("create worktree parent directory failed: {e}"))
        })?;
    }

    // Clean up stale worktree entry if path already exists (from a failed previous attempt)
    if worktree_path.exists() {
        let _ = std::fs::remove_dir_all(worktree_path);
        let _ = std::process::Command::new("git")
            .args(["-C", project_path, "worktree", "prune"])
            .output();
    }

    let wt_str = worktree_path.to_string_lossy();
    let branch_name;
    let mut args = vec!["-C", project_path, "worktree", "add"];

    if let Some(b) = branch {
        branch_name = format!("beacon/{b}");
        args.extend(["-b", branch_name.as_str(), &*wt_str]);
    } else {
        args.extend(["--detach", &*wt_str]);
    }

    let output = std::process::Command::new("git")
        .args(&args)
        .output()
        .map_err(|e| SchedulerError::Database(format!("failed to run git: {e}")))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let stdout = String::from_utf8_lossy(&output.stdout);
        return Err(SchedulerError::Database(format!(
            "git worktree add failed (exit {}): stderr={stderr} stdout={stdout}",
            output.status
        )));
    }

    Ok(())
}

/// Best-effort cleanup of a worktree and its directory after a failure.
fn cleanup_worktree(project_path: Option<&str>, worktree_path: &str, branch: Option<&str>) {
    if let Some(proj) = project_path {
        let _ = std::process::Command::new("git")
            .args(["-C", proj, "worktree", "remove", "--force", worktree_path])
            .output();
        // Prune stale entries if remove failed
        let _ = std::process::Command::new("git")
            .args(["-C", proj, "worktree", "prune"])
            .output();
        // Delete the named branch so retries don't fail with "branch already exists"
        if let Some(b) = branch {
            let branch_name = format!("beacon/{b}");
            let _ = std::process::Command::new("git")
                .args(["-C", proj, "branch", "-D", &branch_name])
                .output();
        }
    }
    // Fallback: remove directory if git worktree remove didn't clean it up
    let _ = std::fs::remove_dir_all(worktree_path);
}

fn validate_branch_name(branch: &str) -> Result<(), SchedulerError> {
    if branch.is_empty() || branch.len() > 100 {
        return Err(SchedulerError::ValidationFailed(
            "branch name must be 1-100 characters".to_string(),
        ));
    }
    if branch.starts_with('-') {
        return Err(SchedulerError::ValidationFailed(
            "branch name must not start with '-'".to_string(),
        ));
    }
    if branch.contains("..") {
        return Err(SchedulerError::ValidationFailed(
            "branch name must not contain '..'".to_string(),
        ));
    }
    let valid = branch
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '_' || c == '/' || c == '-');
    if !valid {
        return Err(SchedulerError::ValidationFailed(
            "branch name contains invalid characters".to_string(),
        ));
    }
    Ok(())
}

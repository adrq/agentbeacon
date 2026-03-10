use std::sync::LazyLock;

use jsonschema::Validator;
use serde_json::{Value as JsonValue, json};
use sqlx::Row;
use uuid::Uuid;

use crate::api::auth::{McpRole, McpSession};
use crate::api::jsonrpc::{JsonRpcError, JsonRpcResponse};
use crate::app::{AppState, EventNotification};
use crate::db;
use crate::error::SchedulerError;

static DELEGATE_VALIDATOR: LazyLock<Validator> = LazyLock::new(|| {
    Validator::new(&delegate_schema()["inputSchema"]).expect("delegate schema must compile")
});

static ESCALATE_VALIDATOR: LazyLock<Validator> = LazyLock::new(|| {
    Validator::new(&escalate_schema()["inputSchema"]).expect("escalate schema must compile")
});

static RELEASE_VALIDATOR: LazyLock<Validator> = LazyLock::new(|| {
    Validator::new(&release_schema()["inputSchema"]).expect("release schema must compile")
});

fn validate_tool_args(validator: &Validator, args: &JsonValue) -> Result<(), JsonRpcError> {
    validator.validate(args).map_err(|err| {
        let path = err.instance_path.to_string();
        if path.is_empty() {
            JsonRpcError::invalid_params(&err.to_string())
        } else {
            JsonRpcError::invalid_params(&format!("{path}: {err}"))
        }
    })
}

/// Handle tools/list — returns role-filtered tool schemas per D18 table
pub fn handle_tools_list(auth: &McpSession, id: Option<JsonValue>) -> JsonRpcResponse {
    let at_max_depth = auth.depth >= auth.max_depth;
    let tools = match (&auth.role, at_max_depth) {
        (McpRole::RootLead, false) => vec![delegate_schema(), release_schema(), escalate_schema()],
        (McpRole::RootLead, true) => vec![escalate_schema()],
        (McpRole::SubLead, _) => vec![delegate_schema(), release_schema()],
        (McpRole::Leaf, _) => vec![],
    };

    JsonRpcResponse::success(id, json!({"tools": tools}))
}

/// Handle tools/call — dispatches to tool handlers with role enforcement
pub async fn handle_tools_call(
    auth: &McpSession,
    state: &AppState,
    params: JsonValue,
) -> Result<JsonValue, JsonRpcError> {
    let tool_name = params
        .get("name")
        .and_then(|v| v.as_str())
        .ok_or_else(|| JsonRpcError::invalid_params("missing 'name' parameter"))?;

    let arguments = params
        .get("arguments")
        .cloned()
        .unwrap_or_else(|| json!({}));

    // Leaf: no tools available
    if auth.role == McpRole::Leaf {
        return Err(JsonRpcError::invalid_request(
            "no tools available for this session role",
        ));
    }

    // delegate/release: requires not at max depth
    if matches!(tool_name, "delegate" | "release") && auth.depth >= auth.max_depth {
        return Err(JsonRpcError::invalid_request(&format!(
            "Cannot {}: maximum hierarchy depth ({}) reached. Handle this work directly.",
            tool_name, auth.max_depth
        )));
    }

    // escalate: root-lead-only
    if tool_name == "escalate" && auth.role != McpRole::RootLead {
        return Err(JsonRpcError::invalid_request(
            "escalate is only available to the root lead agent",
        ));
    }

    match tool_name {
        "delegate" => handle_delegate(auth, state, arguments).await,
        "release" => handle_release(auth, state, arguments).await,
        "escalate" => handle_escalate(auth, state, arguments).await,
        _ => Err(JsonRpcError::invalid_params(&format!(
            "unknown tool: {tool_name}"
        ))),
    }
}

async fn handle_delegate(
    auth: &McpSession,
    state: &AppState,
    args: JsonValue,
) -> Result<JsonValue, JsonRpcError> {
    validate_tool_args(&DELEGATE_VALIDATOR, &args)?;
    let agent_name = args["agent"].as_str().unwrap();
    let prompt = args["prompt"].as_str().unwrap();
    let explicit_cwd = args.get("cwd").and_then(|v| v.as_str());

    // Look up agent by name
    let agent = db::agents::get_by_name(&state.db_pool, agent_name)
        .await
        .map_err(|e| match e {
            crate::error::SchedulerError::NotFound(_) => {
                JsonRpcError::invalid_params(&format!("agent not found: {agent_name}"))
            }
            _ => JsonRpcError::internal_error(&e.to_string()),
        })?;

    if !agent.enabled {
        return Err(JsonRpcError::invalid_params(&format!(
            "agent is disabled: {agent_name}"
        )));
    }

    // Validate agent is in this execution's pool
    let pool_agent_ids =
        db::execution_agents::list_by_execution(&state.db_pool, &auth.execution_id)
            .await
            .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;
    if !pool_agent_ids.contains(&agent.id) {
        return Err(JsonRpcError::invalid_params(&format!(
            "agent '{}' is not available in this execution. \
             Available agents can be listed via the execution's agent pool.",
            agent_name
        )));
    }

    // Resolve child cwd: explicit > parent session's cwd
    let parent_session = db::sessions::get_by_id(&state.db_pool, &auth.session_id)
        .await
        .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;

    // Validate explicit cwd is an absolute path before canonicalize
    if let Some(cwd) = explicit_cwd
        && !std::path::Path::new(cwd).is_absolute()
    {
        return Err(JsonRpcError::invalid_params(&format!(
            "cwd must be an absolute path: {cwd}"
        )));
    }

    let child_cwd = if let Some(cwd) = explicit_cwd {
        Some(cwd.to_string())
    } else {
        parent_session.cwd.clone()
    };

    // Security: validate child cwd is subdirectory of lead session's cwd
    // Fail closed: if explicit cwd is provided but lead has no cwd, reject
    if let Some(ref child_dir) = child_cwd
        && explicit_cwd.is_some()
    {
        let lead_cwd = find_lead_cwd(&state.db_pool, &auth.session_id)
            .await
            .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;

        let lead_dir = lead_cwd.ok_or_else(|| {
            JsonRpcError::invalid_params("cannot verify cwd containment: lead session has no cwd")
        })?;

        let lead_canonical = std::fs::canonicalize(&lead_dir).map_err(|e| {
            JsonRpcError::internal_error(&format!("canonicalize lead cwd failed: {e}"))
        })?;
        let child_canonical = std::fs::canonicalize(child_dir).map_err(|_| {
            JsonRpcError::invalid_params(&format!("cwd path does not exist: {child_dir}"))
        })?;

        if !child_canonical.starts_with(&lead_canonical) {
            return Err(JsonRpcError::invalid_params(
                "child cwd must be within lead session's directory tree",
            ));
        }
    }

    // Atomic width-guarded creation with slug collision retry.
    let new_id = Uuid::new_v4().to_string();
    let mut existing_slugs = db::sessions::sibling_slugs(&state.db_pool, &auth.session_id)
        .await
        .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;
    let mut slug = crate::slugs::generate_slug(&existing_slugs);
    let mut created = false;
    for _ in 0..3 {
        match db::sessions::create_with_width_guard(
            &state.db_pool,
            &new_id,
            &auth.execution_id,
            &agent.id,
            &auth.session_id,
            child_cwd.as_deref(),
            None, // worktree_path (None for child MVP)
            auth.max_width,
            &slug,
        )
        .await
        {
            Ok(true) => {
                created = true;
                break;
            }
            Ok(false) => {
                // Width limit reached — no retry will help
                return Err(JsonRpcError::invalid_params(&format!(
                    "Cannot delegate: maximum active children ({}) reached. \
                     Release idle children or wait for completions.",
                    auth.max_width
                )));
            }
            Err(SchedulerError::Database(msg))
                if msg.to_lowercase().contains("unique")
                    || msg.to_lowercase().contains("duplicate key") =>
            {
                // Slug collision — regenerate and retry
                existing_slugs = db::sessions::sibling_slugs(&state.db_pool, &auth.session_id)
                    .await
                    .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;
                slug = crate::slugs::generate_slug(&existing_slugs);
            }
            Err(e) => return Err(JsonRpcError::internal_error(&e.to_string())),
        }
    }
    if !created {
        return Err(JsonRpcError::internal_error(
            "create child session failed: slug collision after 3 retries",
        ));
    }
    let (child_session_id, child_slug) = (new_id, slug);

    // Determine child's role based on depth
    let child_depth = auth.depth + 1;
    let child_role = if child_depth >= auth.max_depth {
        crate::services::briefing::BriefingRole::Leaf
    } else {
        crate::services::briefing::BriefingRole::SubLead
    };

    // Compute parent's hierarchical name — O(depth), not O(N sessions)
    let parent_hier_name =
        crate::services::messaging::hierarchical_name_for_session(&state.db_pool, &auth.session_id)
            .await
            .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;

    let child_hier_name = format!("{parent_hier_name}/{child_slug}");

    // Only fetch agent pool for roles that display the delegation section
    // (a delegated child is never RootLead — only SubLead or Leaf)
    let available_agents = if matches!(child_role, crate::services::briefing::BriefingRole::SubLead)
    {
        db::execution_agents::list_agent_configs_for_execution(&state.db_pool, &auth.execution_id)
            .await
            .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?
    } else {
        vec![]
    };

    let briefing_ctx = crate::services::briefing::BriefingContext {
        role: child_role,
        slug: child_slug.clone(),
        hierarchical_name: child_hier_name,
        agent_config_name: agent.name.clone(),
        parent_info: parent_hier_name.clone(),
        available_agents,
    };
    let briefing = crate::services::briefing::build_environment_briefing(&briefing_ctx);

    // Parse agent config for task payload
    let mut agent_config: JsonValue = serde_json::from_str::<JsonValue>(&agent.config)
        .ok()
        .filter(|v| v.is_object())
        .unwrap_or_else(|| json!({}));
    let sandbox_config: JsonValue = agent
        .sandbox_config
        .as_ref()
        .and_then(|s| serde_json::from_str(s).ok())
        .unwrap_or(JsonValue::Null);

    // Prepend briefing to existing system_prompt
    let existing_prompt = agent_config
        .get("system_prompt")
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let combined = crate::services::briefing::prepend_briefing(&briefing, existing_prompt);
    agent_config["system_prompt"] = JsonValue::String(combined);

    // Record delegation event + child prompt atomically so observers never
    // see a partial delegation (event without prompt or vice versa).
    let delegate_event_str = serde_json::to_string(&json!({
        "role": "agent",
        "parts": [{"kind": "data", "data": {
            "type": "delegate",
            "agent": agent_name,
            "child_session_id": child_session_id,
            "prompt": prompt
        }}]
    }))
    .unwrap();
    let child_prompt_str = serde_json::to_string(&json!({
        "role": "user",
        "parts": [
            {"kind": "data", "data": {
                "type": "sender",
                "name": &parent_hier_name,
                "session_id": &auth.session_id,
            }},
            {"kind": "text", "text": &prompt},
        ]
    }))
    .unwrap();

    // Build task payload before the transaction so all values are ready.
    let mut task_payload = json!({
        "agent_id": agent.id,
        "driver": {
            "platform": agent.agent_type,
            "config": sandbox_config,
        },
        "agent_config": agent_config,
        "message": {
            "role": "user",
            "parts": [{"kind": "text", "text": prompt}]
        },
    });
    if let Some(ref dir) = child_cwd {
        task_payload["cwd"] = JsonValue::String(dir.clone());
    }
    if let Some(ref pid) = auth.project_id {
        task_payload["project_id"] = JsonValue::String(pid.clone());
    }
    let task_payload_json = serde_json::to_string(&task_payload).map_err(|e| {
        JsonRpcError::internal_error(&format!("serialize task_payload failed: {e}"))
    })?;

    // Single transaction: delegation event + child prompt + task queue entry.
    // If any insert fails the whole thing rolls back — no orphaned state.
    let insert_event_sql = state.db_pool.prepare_query(
        "INSERT INTO events (execution_id, session_id, event_type, payload) VALUES (?, ?, ?, ?) RETURNING id",
    );
    let insert_task_sql = state.db_pool.prepare_query(
        "INSERT INTO task_queue (execution_id, session_id, task_payload) VALUES (?, ?, ?)",
    );
    let mut tx = state
        .db_pool
        .begin()
        .await
        .map_err(|e| JsonRpcError::internal_error(&format!("begin transaction failed: {e}")))?;

    let event_id: i64 = sqlx::query(&insert_event_sql)
        .bind(&auth.execution_id)
        .bind(Some(&auth.session_id))
        .bind("platform")
        .bind(&delegate_event_str)
        .fetch_one(&mut *tx)
        .await
        .map_err(|e| JsonRpcError::internal_error(&format!("insert delegate event failed: {e}")))?
        .try_get("id")
        .map_err(|e| JsonRpcError::internal_error(&format!("get event id failed: {e}")))?;

    let prompt_event_id: i64 = sqlx::query(&insert_event_sql)
        .bind(&auth.execution_id)
        .bind(Some(&*child_session_id))
        .bind("message")
        .bind(&child_prompt_str)
        .fetch_one(&mut *tx)
        .await
        .map_err(|e| JsonRpcError::internal_error(&format!("insert child prompt failed: {e}")))?
        .try_get("id")
        .map_err(|e| JsonRpcError::internal_error(&format!("get prompt id failed: {e}")))?;

    sqlx::query(&insert_task_sql)
        .bind(&auth.execution_id)
        .bind(&*child_session_id)
        .bind(&task_payload_json)
        .execute(&mut *tx)
        .await
        .map_err(|e| JsonRpcError::internal_error(&format!("insert task_queue failed: {e}")))?;

    tx.commit()
        .await
        .map_err(|e| JsonRpcError::internal_error(&format!("commit transaction failed: {e}")))?;

    // Broadcast SSE + wake workers after commit
    let _ = state.event_broadcast.send(EventNotification::persisted(
        auth.execution_id.clone(),
        event_id,
    ));
    let _ = state.event_broadcast.send(EventNotification::persisted(
        auth.execution_id.clone(),
        prompt_event_id,
    ));
    state.task_queue.wake_waiters();

    let result_text = serde_json::to_string(&json!({"session_id": child_session_id})).unwrap();
    Ok(json!({
        "content": [{"type": "text", "text": result_text}],
        "isError": false
    }))
}

async fn handle_release(
    auth: &McpSession,
    state: &AppState,
    args: JsonValue,
) -> Result<JsonValue, JsonRpcError> {
    validate_tool_args(&RELEASE_VALIDATOR, &args)?;
    let target_session_id = args["session_id"].as_str().unwrap();

    // Look up target session
    let target = db::sessions::get_by_id(&state.db_pool, target_session_id)
        .await
        .map_err(|e| match e {
            crate::error::SchedulerError::NotFound(_) => {
                JsonRpcError::invalid_params(&format!("session not found: {target_session_id}"))
            }
            _ => JsonRpcError::internal_error(&e.to_string()),
        })?;

    // Defense-in-depth: verify same execution
    if target.execution_id != auth.execution_id {
        return Err(JsonRpcError::invalid_params(
            "session does not belong to this execution",
        ));
    }

    // Authority: caller must be parent of target
    if target.parent_session_id.as_deref() != Some(&auth.session_id) {
        return Err(JsonRpcError::invalid_params(
            "session is not a child of this session",
        ));
    }

    // State: target must be input-required
    if target.status != "input-required" {
        return Err(JsonRpcError::invalid_params(&format!(
            "cannot release session in '{}' state (must be 'input-required')",
            target.status
        )));
    }

    // Cascade terminate the target and its subtree
    use crate::services::cascade::{CascadeMode, terminate_subtree};

    let result = terminate_subtree(
        &state.db_pool,
        target_session_id,
        true, // include root — target itself transitions to completed
        CascadeMode::Release,
        &state.event_broadcast,
        &state.task_queue,
    )
    .await
    .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;

    // Log release event on the parent (caller) session
    let release_event = json!({
        "role": "agent",
        "parts": [{"kind": "data", "data": {
            "type": "release",
            "target_session_id": target_session_id,
            "sessions_terminated": result.sessions_terminated
        }}]
    });
    let event_id = db::events::insert(
        &state.db_pool,
        &auth.execution_id,
        Some(&auth.session_id),
        "platform",
        &serde_json::to_string(&release_event).unwrap(),
    )
    .await
    .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;
    let _ = state.event_broadcast.send(EventNotification::persisted(
        auth.execution_id.clone(),
        event_id,
    ));

    let result_text = serde_json::to_string(&json!({
        "released": true,
        "sessions_terminated": result.sessions_terminated
    }))
    .unwrap();

    Ok(json!({
        "content": [{"type": "text", "text": result_text}],
        "isError": false
    }))
}

fn release_schema() -> JsonValue {
    json!({
        "name": "release",
        "title": "Release",
        "description": "Terminate a child session and free its resources. The child must be in 'input-required' state. Also terminates any descendants.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "The session ID of the child to release (returned by delegate)"
                }
            },
            "required": ["session_id"]
        }
    })
}

/// Traverse parent_session_id chain to find lead session's cwd.
/// Bounded to 100 iterations to guard against data corruption cycles.
async fn find_lead_cwd(
    pool: &crate::db::DbPool,
    session_id: &str,
) -> Result<Option<String>, crate::error::SchedulerError> {
    let mut current = db::sessions::get_by_id(pool, session_id).await?;
    for _ in 0..100 {
        match current.parent_session_id {
            Some(ref parent_id) => {
                current = db::sessions::get_by_id(pool, parent_id).await?;
            }
            None => break,
        }
    }
    Ok(current.cwd)
}

async fn handle_escalate(
    auth: &McpSession,
    state: &AppState,
    args: JsonValue,
) -> Result<JsonValue, JsonRpcError> {
    validate_tool_args(&ESCALATE_VALIDATOR, &args)?;
    let questions = args["questions"].as_array().unwrap();
    let importance = args
        .get("importance")
        .and_then(|v| v.as_str())
        .unwrap_or("blocking");

    let batch_id = Uuid::new_v4().to_string();
    let batch_size = questions.len();
    let mut question_ids = Vec::with_capacity(batch_size);

    // Create one event per question
    for (batch_index, q) in questions.iter().enumerate() {
        let question = q["question"].as_str().unwrap();
        let options = q.get("options").cloned();
        let context = q.get("context").and_then(|v| v.as_str());

        let mut data = json!({
            "type": "escalate",
            "question": question,
            "importance": importance,
            "batch_id": batch_id,
            "batch_size": batch_size,
            "batch_index": batch_index,
        });
        if let Some(opts) = options {
            data["options"] = opts;
        }
        if let Some(ctx) = context {
            data["context"] = json!(ctx);
        }
        let event_payload = json!({
            "role": "agent",
            "parts": [{"kind": "data", "data": data}]
        });

        let event_id = db::events::insert(
            &state.db_pool,
            &auth.execution_id,
            Some(&auth.session_id),
            "platform",
            &serde_json::to_string(&event_payload).unwrap(),
        )
        .await
        .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;
        let _ = state.event_broadcast.send(EventNotification::persisted(
            auth.execution_id.clone(),
            event_id,
        ));

        question_ids.push(event_id);
    }

    // State transitions once after all events (only if blocking)
    if importance == "blocking" {
        db::sessions::update_status(&state.db_pool, &auth.session_id, "input-required")
            .await
            .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;

        let session_state_event = json!({"from": auth.status, "to": "input-required"});
        let event_id = db::events::insert(
            &state.db_pool,
            &auth.execution_id,
            Some(&auth.session_id),
            "state_change",
            &serde_json::to_string(&session_state_event).unwrap(),
        )
        .await
        .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;
        let _ = state.event_broadcast.send(EventNotification::persisted(
            auth.execution_id.clone(),
            event_id,
        ));

        if auth.role == McpRole::RootLead {
            use db::executions::CasResult;
            match db::executions::update_status_cas(
                &state.db_pool,
                &auth.execution_id,
                "input-required",
                &["working"],
            )
            .await
            .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?
            {
                CasResult::Applied => {
                    let exec_state_event = json!({"from": "working", "to": "input-required"});
                    let event_id = db::events::insert(
                        &state.db_pool,
                        &auth.execution_id,
                        None,
                        "state_change",
                        &serde_json::to_string(&exec_state_event).unwrap(),
                    )
                    .await
                    .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;
                    let _ = state.event_broadcast.send(EventNotification::persisted(
                        auth.execution_id.clone(),
                        event_id,
                    ));
                }
                CasResult::Conflict => {
                    tracing::debug!(
                        execution_id = %auth.execution_id,
                        "execution no longer working — skipping input-required transition"
                    );
                }
                CasResult::NotFound => {
                    return Err(JsonRpcError::internal_error(
                        "execution row missing — data integrity issue",
                    ));
                }
            }
        }
    }

    let result_text =
        serde_json::to_string(&json!({"question_ids": question_ids, "batch_id": batch_id}))
            .unwrap();
    Ok(json!({
        "content": [{"type": "text", "text": result_text}],
        "isError": false
    }))
}

fn delegate_schema() -> JsonValue {
    json!({
        "name": "delegate",
        "title": "Delegate",
        "description": "Assign work to a child agent. Returns immediately with a session_id.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "string",
                    "description": "Name of the agent to delegate to"
                },
                "prompt": {
                    "type": "string",
                    "description": "Task description for the child agent"
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory for child (defaults to parent's cwd)"
                }
            },
            "required": ["agent", "prompt"]
        }
    })
}

fn escalate_schema() -> JsonValue {
    json!({
        "name": "escalate",
        "title": "Escalate",
        "description": "Escalate one or more questions or notifications to the user.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "questions": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 4,
                    "items": {
                        "type": "object",
                        "properties": {
                            "question": {
                                "type": "string",
                                "description": "The question or message to present to the user"
                            },
                            "context": {
                                "type": "string",
                                "description": "Additional context to help the user answer"
                            },
                            "options": {
                                "type": "array",
                                "minItems": 2,
                                "maxItems": 5,
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "label": {"type": "string"},
                                        "description": {"type": "string"}
                                    },
                                    "required": ["label", "description"]
                                },
                                "description": "Optional list of choices for the user"
                            }
                        },
                        "required": ["question"]
                    },
                    "description": "Array of 1-4 questions to present to the user"
                },
                "importance": {
                    "type": "string",
                    "enum": ["blocking", "fyi"],
                    "description": "Whether this blocks execution or is informational"
                }
            },
            "required": ["questions"]
        }
    })
}

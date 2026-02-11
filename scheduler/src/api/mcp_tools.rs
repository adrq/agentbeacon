use std::sync::LazyLock;
use std::time::Duration;

use jsonschema::Validator;
use serde_json::{Value as JsonValue, json};
use uuid::Uuid;

use crate::api::auth::{McpRole, McpSession};
use crate::api::jsonrpc::{JsonRpcError, JsonRpcResponse};
use crate::app::AppState;
use crate::db;
use crate::queue::TaskAssignment;

const DEFAULT_POLL_TIMEOUT_MS: u64 = 50_000;
const MAX_POLL_TIMEOUT_MS: u64 = 300_000;

// Compiled JSON Schema validators for MCP tool arguments.
// Schemas are extracted from the tool definitions served via tools/list.
static HANDOFF_VALIDATOR: LazyLock<Validator> = LazyLock::new(|| {
    Validator::new(&handoff_schema()["inputSchema"]).expect("handoff schema must compile")
});

static DELEGATE_VALIDATOR: LazyLock<Validator> = LazyLock::new(|| {
    Validator::new(&delegate_schema()["inputSchema"]).expect("delegate schema must compile")
});

static ASK_USER_VALIDATOR: LazyLock<Validator> = LazyLock::new(|| {
    Validator::new(&ask_user_schema()["inputSchema"]).expect("ask_user schema must compile")
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

/// Handle tools/list — returns role-filtered tool schemas
pub fn handle_tools_list(auth: &McpSession, id: Option<JsonValue>) -> JsonRpcResponse {
    let tools = match auth.role {
        McpRole::Master => vec![
            delegate_schema(),
            ask_user_schema(),
            next_instruction_schema(),
        ],
        McpRole::Child => vec![handoff_schema(), next_instruction_schema()],
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

    // Role enforcement
    match (tool_name, &auth.role) {
        ("delegate", McpRole::Child) | ("ask_user", McpRole::Child) => {
            return Err(JsonRpcError::invalid_request(
                "tool not available for this session role",
            ));
        }
        ("handoff", McpRole::Master) => {
            return Err(JsonRpcError::invalid_request(
                "tool not available for this session role",
            ));
        }
        _ => {}
    }

    match tool_name {
        "handoff" => handle_handoff(auth, state, arguments).await,
        "delegate" => handle_delegate(auth, state, arguments).await,
        "ask_user" => handle_ask_user(auth, state, arguments).await,
        "next_instruction" => handle_next_instruction(auth, state).await,
        _ => Err(JsonRpcError::invalid_params(&format!(
            "unknown tool: {tool_name}"
        ))),
    }
}

async fn handle_handoff(
    auth: &McpSession,
    state: &AppState,
    args: JsonValue,
) -> Result<JsonValue, JsonRpcError> {
    validate_tool_args(&HANDOFF_VALIDATOR, &args)?;
    let message = args["message"].as_str().unwrap();

    // Record message event
    let msg_payload = json!({
        "role": "agent",
        "parts": [{"kind": "data", "data": {"tool": "handoff", "message": message}}]
    });
    db::events::insert(
        &state.db_pool,
        &auth.execution_id,
        Some(&auth.session_id),
        "message",
        &serde_json::to_string(&msg_payload).unwrap(),
    )
    .await
    .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;

    // Update session to completed
    db::sessions::update_status(&state.db_pool, &auth.session_id, "completed")
        .await
        .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;

    // Record state_change event
    let state_event = json!({"from": auth.status, "to": "completed"});
    db::events::insert(
        &state.db_pool,
        &auth.execution_id,
        Some(&auth.session_id),
        "state_change",
        &serde_json::to_string(&state_event).unwrap(),
    )
    .await
    .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;

    // Deliver handoff result to parent session's inbox
    let child_session = db::sessions::get_by_id(&state.db_pool, &auth.session_id)
        .await
        .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;

    if let Some(parent_id) = &child_session.parent_session_id {
        // Record a message event on the parent session for audit/UI
        let parent_msg = json!({
            "role": "agent",
            "parts": [{"kind": "data", "data": {
                "tool": "handoff_result",
                "child_session_id": auth.session_id,
                "message": message
            }}]
        });
        db::events::insert(
            &state.db_pool,
            &auth.execution_id,
            Some(parent_id),
            "message",
            &serde_json::to_string(&parent_msg).unwrap(),
        )
        .await
        .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;

        // Look up agent name for context prefix — fallback to agent_id if lookup fails
        // so the handoff result still reaches the master even if enrichment fails
        let agent_name = match db::agents::get_by_id(&state.db_pool, &child_session.agent_id).await
        {
            Ok(agent) => agent.name,
            Err(e) => {
                tracing::warn!(
                    agent_id = %child_session.agent_id,
                    error = %e,
                    "failed to look up agent name for handoff prefix, using agent_id"
                );
                child_session.agent_id.clone()
            }
        };

        // Sanitize agent name to prevent control chars breaking the prefix format
        let agent_name = agent_name.replace(['\r', '\n'], " ");
        let agent_name = agent_name.trim();

        // Push to parent session's inbox so next_instruction can deliver it
        let handoff_payload = serde_json::Value::String(format!(
            "[delegated result from {} \u{00b7} session {}]\n\n{}",
            agent_name, auth.session_id, message
        ));
        state
            .task_queue
            .push(TaskAssignment {
                execution_id: auth.execution_id.clone(),
                session_id: parent_id.clone(),
                task_payload: handoff_payload,
            })
            .await
            .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;
    }

    Ok(json!({
        "content": [{"type": "text", "text": "{\"status\": \"completed\"}"}],
        "isError": false
    }))
}

async fn handle_delegate(
    auth: &McpSession,
    state: &AppState,
    args: JsonValue,
) -> Result<JsonValue, JsonRpcError> {
    validate_tool_args(&DELEGATE_VALIDATOR, &args)?;
    let agent_name = args["agent"].as_str().unwrap();
    let prompt = args["prompt"].as_str().unwrap();
    let resume_session_id = args.get("session_id").and_then(|v| v.as_str());

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

    let child_session_id = if let Some(existing_id) = resume_session_id {
        // Resume existing session — verify it belongs to this execution and this master
        let existing = db::sessions::get_by_id(&state.db_pool, existing_id)
            .await
            .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;

        if existing.execution_id != auth.execution_id {
            return Err(JsonRpcError::invalid_params(
                "session does not belong to this execution",
            ));
        }
        if existing.parent_session_id.as_deref() != Some(&auth.session_id) {
            return Err(JsonRpcError::invalid_params(
                "session is not a child of this session",
            ));
        }
        if existing.agent_id != agent.id {
            return Err(JsonRpcError::invalid_params(
                "session belongs to a different agent",
            ));
        }

        // Reset status to submitted
        db::sessions::update_status(&state.db_pool, existing_id, "submitted")
            .await
            .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;

        existing_id.to_string()
    } else {
        // Create new child session
        let new_id = Uuid::new_v4().to_string();
        db::sessions::create(
            &state.db_pool,
            &new_id,
            &auth.execution_id,
            &agent.id,
            Some(&auth.session_id),
        )
        .await
        .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;
        new_id
    };

    // Parse agent config for task payload
    let agent_config: JsonValue = serde_json::from_str(&agent.config).unwrap_or_else(|_| json!({}));
    let sandbox_config: JsonValue = agent
        .sandbox_config
        .as_ref()
        .and_then(|s| serde_json::from_str(s).ok())
        .unwrap_or(JsonValue::Null);

    // Build task payload and enqueue
    let task_payload = json!({
        "agent_id": agent.id,
        "agent_type": agent.agent_type,
        "agent_config": agent_config,
        "sandbox_config": sandbox_config,
        "message": {
            "role": "user",
            "parts": [{"kind": "text", "text": prompt}]
        }
    });

    state
        .task_queue
        .push(TaskAssignment {
            execution_id: auth.execution_id.clone(),
            session_id: child_session_id.clone(),
            task_payload,
        })
        .await
        .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;

    // Record delegation event on master session
    let event_payload = json!({
        "role": "agent",
        "parts": [{"kind": "data", "data": {
            "tool": "delegate",
            "agent": agent_name,
            "child_session_id": child_session_id,
            "prompt": prompt
        }}]
    });
    db::events::insert(
        &state.db_pool,
        &auth.execution_id,
        Some(&auth.session_id),
        "message",
        &serde_json::to_string(&event_payload).unwrap(),
    )
    .await
    .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;

    let result_text = serde_json::to_string(&json!({"session_id": child_session_id})).unwrap();
    Ok(json!({
        "content": [{"type": "text", "text": result_text}],
        "isError": false
    }))
}

async fn handle_ask_user(
    auth: &McpSession,
    state: &AppState,
    args: JsonValue,
) -> Result<JsonValue, JsonRpcError> {
    validate_tool_args(&ASK_USER_VALIDATOR, &args)?;
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
            "tool": "ask_user",
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
            "message",
            &serde_json::to_string(&event_payload).unwrap(),
        )
        .await
        .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;

        question_ids.push(event_id);
    }

    // State transitions once after all events (only if blocking)
    if importance == "blocking" {
        db::sessions::update_status(&state.db_pool, &auth.session_id, "input-required")
            .await
            .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;

        let session_state_event = json!({"from": auth.status, "to": "input-required"});
        db::events::insert(
            &state.db_pool,
            &auth.execution_id,
            Some(&auth.session_id),
            "state_change",
            &serde_json::to_string(&session_state_event).unwrap(),
        )
        .await
        .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;

        if auth.role == McpRole::Master {
            let execution = db::executions::get_by_id(&state.db_pool, &auth.execution_id)
                .await
                .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;

            db::executions::update_status(&state.db_pool, &auth.execution_id, "input-required")
                .await
                .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;

            let exec_state_event = json!({"from": execution.status, "to": "input-required"});
            db::events::insert(
                &state.db_pool,
                &auth.execution_id,
                None,
                "state_change",
                &serde_json::to_string(&exec_state_event).unwrap(),
            )
            .await
            .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;
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

fn handoff_schema() -> JsonValue {
    json!({
        "name": "handoff",
        "title": "Handoff",
        "description": "Signal that your work is complete and hand off results to the master agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Summary of work done, approach, decisions, and open questions"
                }
            },
            "required": ["message"]
        }
    })
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
                "session_id": {
                    "type": "string",
                    "description": "Resume existing session (optional)"
                }
            },
            "required": ["agent", "prompt"]
        }
    })
}

async fn handle_next_instruction(
    auth: &McpSession,
    state: &AppState,
) -> Result<JsonValue, JsonRpcError> {
    // Auto-detect coordination mode on first call
    let session = db::sessions::get_by_id(&state.db_pool, &auth.session_id)
        .await
        .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;

    if session.coordination_mode != "mcp_poll" {
        db::sessions::update_coordination_mode(&state.db_pool, &auth.session_id, "mcp_poll")
            .await
            .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;
    }

    // Read poll_timeout_ms from agent config
    let agent = db::agents::get_by_id(&state.db_pool, &auth.agent_id)
        .await
        .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?;

    let agent_config: JsonValue = serde_json::from_str(&agent.config).unwrap_or_else(|_| json!({}));
    let poll_timeout_ms = agent_config
        .get("poll_timeout_ms")
        .and_then(|v| v.as_u64())
        .unwrap_or(DEFAULT_POLL_TIMEOUT_MS)
        .min(MAX_POLL_TIMEOUT_MS);

    let deadline = tokio::time::Instant::now() + Duration::from_millis(poll_timeout_ms);

    // Long-poll loop: register notified BEFORE checking inbox to avoid
    // missing a push that fires between pop and select!
    loop {
        let notified = state.task_queue.notified();

        if let Some(task) = state
            .task_queue
            .pop_by_session(&auth.session_id)
            .await
            .map_err(|e| JsonRpcError::internal_error(&e.to_string()))?
        {
            // Unwrap bootstrap objects: MCP-poll agents only need the prompt text,
            // not the worker-specific fields (agent_id, agent_type, agent_config).
            let payload = if task.task_payload.is_object() {
                if let Some(message) = task.task_payload.get("message") {
                    let role = message
                        .get("role")
                        .and_then(|r| r.as_str())
                        .unwrap_or("user");
                    // Aggregate all text parts (not just the first)
                    let text: String = message
                        .get("parts")
                        .and_then(|p| p.as_array())
                        .map(|parts| {
                            parts
                                .iter()
                                .filter(|p| p.get("kind").and_then(|k| k.as_str()) == Some("text"))
                                .filter_map(|p| p.get("text").and_then(|t| t.as_str()))
                                .collect::<Vec<_>>()
                                .join("\n")
                        })
                        .unwrap_or_default();
                    if text.is_empty() {
                        // Can't extract text — serialize the object so the agent
                        // always gets a string (consistent contract)
                        let json_str =
                            serde_json::to_string(&task.task_payload).unwrap_or_default();
                        serde_json::Value::String(format!("[{role}]\n\n{json_str}"))
                    } else {
                        serde_json::Value::String(format!("[{role}]\n\n{text}"))
                    }
                } else {
                    task.task_payload
                }
            } else {
                task.task_payload
            };
            let result_text = serde_json::to_string(&json!({"task": payload})).unwrap();
            return Ok(json!({
                "content": [{"type": "text", "text": result_text}],
                "isError": false
            }));
        }

        let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
        if remaining.is_zero() {
            let result_text = serde_json::to_string(&json!({"timed_out": true})).unwrap();
            return Ok(json!({
                "content": [{"type": "text", "text": result_text}],
                "isError": false
            }));
        }

        tokio::select! {
            _ = notified => {
                // A push happened — loop back and check our session's inbox
            }
            _ = tokio::time::sleep(remaining) => {
                // Deadline reached — will return timed_out on next iteration
            }
        }
    }
}

fn ask_user_schema() -> JsonValue {
    json!({
        "name": "ask_user",
        "title": "Ask User",
        "description": "Surface one or more questions or notifications to the user as a batch.",
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

fn next_instruction_schema() -> JsonValue {
    json!({
        "name": "next_instruction",
        "title": "Next Instruction",
        "description": "Wait for the next instruction or event. Blocks until a message arrives or timeout.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    })
}

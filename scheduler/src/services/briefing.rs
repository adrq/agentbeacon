use tracing::warn;

use crate::db;
use crate::db::DbPool;

pub struct BriefingContext {
    pub role: BriefingRole,
    pub slug: String,
    pub hierarchical_name: String,
    pub agent_config_name: String,
    pub parent_info: String,
    pub project_id: Option<String>,
}

pub enum BriefingRole {
    RootLead,
    SubLead,
    Leaf,
}

// Default briefing text — the primary source of truth.
// When briefing.use_db_overrides is false (default), these are used directly.
// When true, DB and project-level overrides can replace these defaults.

const DEFAULT_DELEGATION: &str = "Use the AgentBeacon `delegate` MCP tool to assign work to child agents.\n\
Discover available agent configs via `GET $AGENTBEACON_API_BASE/api/executions/$AGENTBEACON_EXECUTION_ID/agents` before delegating.\n\
An **agent** is a configured specialist type (e.g., `backend-dev`). A **session** is a running instance — delegating to the same agent twice creates two independent sessions.\n\
\n\
**Sessions are long-lived.** After a child completes a task, do NOT release it by default. The child retains its full context (codebase understanding, conversation history, mental model). Re-use it for follow-up work.\n\
\n\
**Follow-up work uses messaging, not delegate.** To give more work to an existing child, send a message via `POST /api/messages` (see Messaging section). This automatically wakes the child if it ended its turn. Calling `delegate` again creates a *new*, independent session — only do this when you intentionally want a fresh worker with no prior context.\n\
\n\
**Release is explicit termination.** Use `release` only when you have a clear reason:\n\
- The work stream is fully complete with no foreseeable follow-ups\n\
- You want a fresh perspective (e.g., code review without bias from having written the code)\n\
- The child is permanently stuck and recovery has failed\n\
- The execution is wrapping up and you are cleaning house";

const DEFAULT_ESCALATE: &str = "**Always use this API to surface questions, decisions, and updates to the user.** Ending your turn with a question in your output does not notify the user — they would have to manually find your idle session. The escalate API triggers a notification.\n\n\
`POST $AGENTBEACON_API_BASE/api/escalate`\n\
```json\n\
{\"questions\": [{\"question\": \"Your question here\", \"options\": [{\"label\": \"A\", \"description\": \"...\"}]}], \"importance\": \"blocking\"}\n\
```\n\
- `importance`: `\"blocking\"` (default) — end your turn and wait for the answer. `\"fyi\"` — fire-and-forget notification for updates the user should know about.\n\
- `options`: optional array of 2-5 `{label, description}` choices per question.\n\
- `context`: optional string with additional context per question.\n\
- Max 4 questions per batch.\n\
- The user's answer is delivered as a normal message to this session.";

const DEFAULT_COORDINATION: &str = "When waiting for a reply from another agent or for a child to complete,\n\
**end your turn**. The system will resume you automatically when:\n\
- A message arrives for you\n\
- A child session completes or crashes\n\
- The user responds to an escalation\n\
\n\
Do NOT poll in a loop or sleep-wait. Just finish your turn.\n\
\n\
**Authority is separate from communication.**\n\
- Authority flows through the tree: you delegate to children, your parent delegates to you.\n\
- Communication flows freely: you can message any agent in the execution by hierarchical name.\n\
- Messaging a peer is requesting cooperation, not issuing commands. You have no authority over peers.\n\
\n\
You can message any non-terminal session. Stopped, idle, and running children all accept messages — the system handles delivery and auto-resume. Messages are rejected when the recipient session or its execution is terminal or shutting down.\n\
\n\
**Escalate decisions, do not decide alone.** When you encounter decisions outside your delegated scope — product direction, architectural choices, ambiguous requirements — do not just proceed. Ending your turn with a question in your output does not notify anyone. Message your parent, who has broader context and can escalate further.";

const DEFAULT_MESSAGING: &str = "Send a message:\n\
  curl -X POST \"$AGENTBEACON_API_BASE/api/messages\" \\\n\
    -H \"Authorization: Bearer $AGENTBEACON_SESSION_ID\" \\\n\
    -H \"Content-Type: application/json\" \\\n\
    -d \"{\\\"to\\\": \\\"<hierarchical-name>\\\", \\\"parts\\\": [{\\\"text\\\": \\\"your message\\\"}]}\"\n\
\n\
Read your messages:\n\
  curl \"$AGENTBEACON_API_BASE/api/messages?session_id=$AGENTBEACON_SESSION_ID\"\n\
\n\
Read messages since a known event ID:\n\
  curl \"$AGENTBEACON_API_BASE/api/messages?session_id=$AGENTBEACON_SESSION_ID&since_id=<id>\"\n\
\n\
Discover sessions in your execution:\n\
  curl \"$AGENTBEACON_API_BASE/api/executions/$AGENTBEACON_EXECUTION_ID/sessions\" \\\n\
    -H \"Authorization: Bearer $AGENTBEACON_SESSION_ID\"\n\
\n\
Search the wiki (prefer this over listing pages):\n\
  curl \"$AGENTBEACON_API_BASE/api/wiki/search?q=<terms>\" \\\n\
    -H \"Authorization: Bearer $AGENTBEACON_SESSION_ID\"\n\
- Results may include pages owned by other projects; each carries its owning project. \
Fetch a page to see your access level.\n\
\n\
Read a wiki page:\n\
  curl \"$AGENTBEACON_API_BASE/api/projects/$AGENTBEACON_PROJECT_ID/wiki/pages/<slug>\" \\\n\
    -H \"Authorization: Bearer $AGENTBEACON_SESSION_ID\"\n\
- For a page owned by another project, replace $AGENTBEACON_PROJECT_ID with that project's slug \
(shown in search results). You may edit it if your access level says read_write.\n\
\n\
Create a wiki page:\n\
  curl -X PUT \"$AGENTBEACON_API_BASE/api/projects/$AGENTBEACON_PROJECT_ID/wiki/pages/<slug>\" \\\n\
    -H \"Authorization: Bearer $AGENTBEACON_SESSION_ID\" \\\n\
    -H \"Content-Type: application/json\" \\\n\
    -d \"{\\\"title\\\": \\\"Page Title\\\", \\\"body\\\": \\\"Content here\\\"}\"\n\
\n\
Edit a wiki page (targeted find/replace -- the only way to update an existing page):\n\
  curl -X PATCH \"$AGENTBEACON_API_BASE/api/projects/$AGENTBEACON_PROJECT_ID/wiki/pages/<slug>\" \\\n\
    -H \"Authorization: Bearer $AGENTBEACON_SESSION_ID\" \\\n\
    -H \"Content-Type: application/json\" \\\n\
    -d \"{\\\"edits\\\": [{\\\"old_string\\\": \\\"find me\\\", \\\"new_string\\\": \\\"replace me\\\"}], \\\"revision_number\\\": 5}\"\n\
- old_string must match exactly once (or set replace_all: true).\n\
- edits are applied in order, all-or-nothing.\n\
- revision_number must match the current page revision (GET it first).";
const DEFAULT_RECOVERY: &str = "When a child session crashes, the system automatically attempts recovery (up to 3 retries).\n\
- Do NOT immediately re-delegate the same work to a new child.\n\
- Do NOT assume the work is lost — the child retains its context through recovery.\n\
- Continue with other work. You will be notified when the child recovers or permanently fails.\n\
- Only re-delegate if the status reaches a terminal failure state (at which point the old session cannot receive messages).";

const DEFAULT_REST_API: &str = "Environment variables for API access:\n\
- `$AGENTBEACON_SESSION_ID` — your auth token (use as Bearer header)\n\
- `$AGENTBEACON_API_BASE` — scheduler base URL\n\
- `$AGENTBEACON_EXECUTION_ID` — current execution\n\
- `$AGENTBEACON_PROJECT_ID` — current project (if set)\n\
`GET $AGENTBEACON_API_BASE/api/docs` for the full API reference.\n\
Discover running sessions via `GET $AGENTBEACON_API_BASE/api/executions/$AGENTBEACON_EXECUTION_ID/sessions`.\n\
Write scripts to interact with the API (e.g. discover agents, filter results, send messages in a loop) \
rather than making one curl call at a time — process data in code, not in your context window.";

/// Read a briefing section with three-tier resolution:
/// 1. Project settings override (from pre-loaded briefing overrides map)
/// 2. Config table row (`briefing.<section>`)
/// 3. Compiled-in fallback constant
async fn read_briefing_section(
    pool: &DbPool,
    key: &str,
    fallback: &str,
    overrides: Option<&serde_json::Value>,
) -> String {
    // Check pre-loaded project overrides first
    if let Some(override_value) = resolve_override_from_map(overrides, key) {
        return override_value;
    }

    match db::config::get(pool, key).await {
        Ok(c) => c.value,
        Err(e) => {
            warn!(
                key,
                error = %e,
                "briefing config key missing from DB, using compiled-in fallback"
            );
            fallback.to_string()
        }
    }
}

/// Load project briefing overrides map once (the `settings.briefing` object).
async fn load_project_briefing_overrides(
    pool: &DbPool,
    project_id: Option<&str>,
) -> Option<serde_json::Value> {
    let pid = project_id?;
    let project = match db::projects::get_by_id(pool, pid).await {
        Ok(p) => p,
        Err(e) => {
            warn!(
                project_id = pid,
                error = %e,
                "failed to load project for briefing overrides"
            );
            return None;
        }
    };
    let settings: serde_json::Value = match serde_json::from_str(&project.settings) {
        Ok(v) => v,
        Err(e) => {
            warn!(
                project_id = pid,
                error = %e,
                "malformed project settings JSON, skipping briefing overrides"
            );
            return None;
        }
    };
    settings.get("briefing").cloned()
}

/// Extract a single override from the pre-loaded briefing map.
/// Key format: "briefing.delegation" → looks for map["delegation"]
fn resolve_override_from_map(
    briefing_map: Option<&serde_json::Value>,
    key: &str,
) -> Option<String> {
    let map = briefing_map?;
    let section = key.strip_prefix("briefing.")?;
    map.get(section)
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
}

async fn read_briefing_section_in_tx(
    pool: &DbPool,
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    key: &str,
    fallback: &str,
    overrides: Option<&serde_json::Value>,
) -> String {
    // Check pre-loaded project overrides first
    if let Some(override_value) = resolve_override_from_map(overrides, key) {
        return override_value;
    }

    let sql = pool.prepare_query("SELECT value FROM config WHERE name = ?");
    match sqlx::query(&sql).bind(key).fetch_one(&mut **tx).await {
        Ok(row) => {
            use sqlx::Row;
            row.get::<String, _>("value")
        }
        Err(_) => fallback.to_string(),
    }
}

/// Load project briefing overrides map through an existing transaction.
async fn load_project_briefing_overrides_in_tx(
    pool: &DbPool,
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    project_id: Option<&str>,
) -> Option<serde_json::Value> {
    let pid = project_id?;
    let sql =
        pool.prepare_query("SELECT settings FROM projects WHERE id = ? AND deleted_at IS NULL");
    let settings_str: String = match sqlx::query(&sql).bind(pid).fetch_one(&mut **tx).await {
        Ok(row) => {
            use sqlx::Row;
            row.get::<String, _>("settings")
        }
        Err(e) => {
            warn!(
                project_id = pid,
                error = %e,
                "failed to load project for briefing overrides (in tx)"
            );
            return None;
        }
    };
    let settings: serde_json::Value = match serde_json::from_str(&settings_str) {
        Ok(v) => v,
        Err(e) => {
            warn!(
                project_id = pid,
                error = %e,
                "malformed project settings JSON, skipping briefing overrides"
            );
            return None;
        }
    };
    settings.get("briefing").cloned()
}

pub async fn build_environment_briefing(pool: &DbPool, ctx: &BriefingContext) -> String {
    let use_db_overrides = match db::config::get(pool, "briefing.use_db_overrides").await {
        Ok(c) => c.value == "true",
        Err(_) => false,
    };

    if !use_db_overrides {
        return build_environment_briefing_with_sections(
            ctx,
            DEFAULT_DELEGATION,
            DEFAULT_ESCALATE,
            DEFAULT_COORDINATION,
            DEFAULT_MESSAGING,
            DEFAULT_RECOVERY,
            DEFAULT_REST_API,
        );
    }

    let overrides = load_project_briefing_overrides(pool, ctx.project_id.as_deref()).await;
    let ov = overrides.as_ref();
    let delegation_text =
        read_briefing_section(pool, "briefing.delegation", DEFAULT_DELEGATION, ov).await;
    let escalate_text =
        read_briefing_section(pool, "briefing.escalate", DEFAULT_ESCALATE, ov).await;
    let coordination_text =
        read_briefing_section(pool, "briefing.coordination", DEFAULT_COORDINATION, ov).await;
    let messaging_text =
        read_briefing_section(pool, "briefing.messaging", DEFAULT_MESSAGING, ov).await;
    let recovery_text =
        read_briefing_section(pool, "briefing.recovery", DEFAULT_RECOVERY, ov).await;
    let rest_api_text =
        read_briefing_section(pool, "briefing.rest_api", DEFAULT_REST_API, ov).await;

    build_environment_briefing_with_sections(
        ctx,
        &delegation_text,
        &escalate_text,
        &coordination_text,
        &messaging_text,
        &recovery_text,
        &rest_api_text,
    )
}

/// Like `build_environment_briefing` but reads config through an existing transaction.
pub async fn build_environment_briefing_in_tx(
    pool: &DbPool,
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    ctx: &BriefingContext,
) -> String {
    // Check use_db_overrides flag via the transaction
    let use_db_overrides = {
        let sql = pool.prepare_query("SELECT value FROM config WHERE name = ?");
        match sqlx::query(&sql)
            .bind("briefing.use_db_overrides")
            .fetch_one(&mut **tx)
            .await
        {
            Ok(row) => {
                use sqlx::Row;
                row.get::<String, _>("value") == "true"
            }
            Err(_) => false,
        }
    };

    if !use_db_overrides {
        return build_environment_briefing_with_sections(
            ctx,
            DEFAULT_DELEGATION,
            DEFAULT_ESCALATE,
            DEFAULT_COORDINATION,
            DEFAULT_MESSAGING,
            DEFAULT_RECOVERY,
            DEFAULT_REST_API,
        );
    }

    let overrides =
        load_project_briefing_overrides_in_tx(pool, tx, ctx.project_id.as_deref()).await;
    let ov = overrides.as_ref();
    let delegation_text =
        read_briefing_section_in_tx(pool, tx, "briefing.delegation", DEFAULT_DELEGATION, ov).await;
    let escalate_text =
        read_briefing_section_in_tx(pool, tx, "briefing.escalate", DEFAULT_ESCALATE, ov).await;
    let coordination_text =
        read_briefing_section_in_tx(pool, tx, "briefing.coordination", DEFAULT_COORDINATION, ov)
            .await;
    let messaging_text =
        read_briefing_section_in_tx(pool, tx, "briefing.messaging", DEFAULT_MESSAGING, ov).await;
    let recovery_text =
        read_briefing_section_in_tx(pool, tx, "briefing.recovery", DEFAULT_RECOVERY, ov).await;
    let rest_api_text =
        read_briefing_section_in_tx(pool, tx, "briefing.rest_api", DEFAULT_REST_API, ov).await;

    build_environment_briefing_with_sections(
        ctx,
        &delegation_text,
        &escalate_text,
        &coordination_text,
        &messaging_text,
        &recovery_text,
        &rest_api_text,
    )
}

/// Pure function for testability — no DB dependency.
pub fn build_environment_briefing_with_sections(
    ctx: &BriefingContext,
    delegation_text: &str,
    escalate_text: &str,
    coordination_text: &str,
    messaging_text: &str,
    recovery_text: &str,
    rest_api_text: &str,
) -> String {
    let mut sections = Vec::new();

    // Header
    sections.push(format!(
        "# AgentBeacon Environment\n\
         You are **{}** (config: `{}`), role: {}.\n\
         Your slug: `{}`  \n\
         Hierarchical name: `{}`  \n\
         Parent: {}",
        ctx.hierarchical_name,
        ctx.agent_config_name,
        role_label(&ctx.role),
        ctx.slug,
        ctx.hierarchical_name,
        ctx.parent_info,
    ));

    // Delegation section (root lead and sub-lead only)
    if matches!(ctx.role, BriefingRole::RootLead | BriefingRole::SubLead) {
        sections.push(format!("## Delegation\n{delegation_text}"));
    }

    // Escalate section (root lead only)
    if matches!(ctx.role, BriefingRole::RootLead) {
        sections.push(format!("## Escalate\n{escalate_text}"));
    }

    // Coordination section (all roles)
    sections.push(format!("## Coordination\n{coordination_text}"));

    // Messaging section (all roles)
    sections.push(format!("## Messaging\n{messaging_text}"));

    // Recovery section (root lead and sub-lead only)
    if matches!(ctx.role, BriefingRole::RootLead | BriefingRole::SubLead) {
        sections.push(format!("## Recovery\n{recovery_text}"));
    }

    // REST API section (all roles)
    sections.push(format!("## REST API\n{rest_api_text}"));

    sections.join("\n\n")
}

fn role_label(role: &BriefingRole) -> &'static str {
    match role {
        BriefingRole::RootLead => "root lead",
        BriefingRole::SubLead => "sub-lead",
        BriefingRole::Leaf => "leaf",
    }
}

/// Prepend briefing to existing system_prompt, separated by `---` if non-empty.
pub fn prepend_briefing(briefing: &str, existing_prompt: &str) -> String {
    if existing_prompt.is_empty() {
        briefing.to_string()
    } else {
        format!("{briefing}\n---\n\n{existing_prompt}")
    }
}

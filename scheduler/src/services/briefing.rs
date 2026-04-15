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

// Fallback text used when the config table row is missing.
// Edit briefing text via POST /api/config, not by changing these constants.

const FALLBACK_DELEGATION: &str = "Use the AgentBeacon `delegate` MCP tool to assign work to child agents.\n\
Use the AgentBeacon `release` MCP tool to terminate a child when done.\n\
Discover available agent configs via `GET $AGENTBEACON_API_BASE/api/executions/$AGENTBEACON_EXECUTION_ID/agents` before delegating.\n\
An **agent** is a configured specialist type (e.g., `backend-dev`). A **session** is a running instance — delegating to the same agent twice creates two independent sessions.";

const FALLBACK_ESCALATE: &str = "Use the AgentBeacon `escalate` REST API to surface questions to the user.\n\n\
`POST $AGENTBEACON_API_BASE/api/escalate`\n\
```json\n\
{\"questions\": [{\"question\": \"Your question here\", \"options\": [{\"label\": \"A\", \"description\": \"...\"}]}], \"importance\": \"blocking\"}\n\
```\n\
- `importance`: `\"blocking\"` (default) means the agent should end its turn and wait for the user's answer in the next turn; `\"fyi\"` is fire-and-forget.\n\
- `options`: optional array of 2-5 `{label, description}` choices per question.\n\
- `context`: optional string with additional context per question.\n\
- Max 4 questions per batch.\n\
- The user's answer is delivered as a normal message to this session.";

const FALLBACK_COORDINATION: &str = "When waiting for a reply from another agent or for a child to complete,\n\
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
- Messaging a peer is requesting cooperation, not issuing commands. You have no authority over peers.";

const FALLBACK_MESSAGING: &str = "Send a message:\n\
  curl -X POST \"$AGENTBEACON_API_BASE/api/messages\" \\\n\
    -H \"Authorization: Bearer $AGENTBEACON_SESSION_ID\" \\\n\
    -H \"Content-Type: application/json\" \\\n\
    -d '{\"to\": \"<hierarchical-name>\", \"parts\": [{\"text\": \"your message\"}]}'\n\
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
Read a wiki page:\n\
  curl \"$AGENTBEACON_API_BASE/api/projects/$AGENTBEACON_PROJECT_ID/wiki/pages/<slug>\" \\\n\
    -H \"Authorization: Bearer $AGENTBEACON_SESSION_ID\"\n\
\n\
Write a wiki page:\n\
  curl -X PUT \"$AGENTBEACON_API_BASE/api/projects/$AGENTBEACON_PROJECT_ID/wiki/pages/<slug>\" \\\n\
    -H \"Authorization: Bearer $AGENTBEACON_SESSION_ID\" \\\n\
    -H \"Content-Type: application/json\" \\\n\
    -d '{\"title\": \"Page Title\", \"body\": \"Content here\"}'";

const FALLBACK_RECOVERY: &str = "When a child session crashes, the system automatically attempts recovery (up to 3 retries).\n\
- Do NOT immediately re-delegate the same work to a new child.\n\
- Do NOT assume the child's work is lost.\n\
- Continue with other work. You will be notified when the child recovers or permanently fails.\n\
- Only re-delegate if the child's status reaches a terminal failure state.";

const FALLBACK_REST_API: &str = "Environment variables for API access:\n\
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
    let overrides = load_project_briefing_overrides(pool, ctx.project_id.as_deref()).await;
    let ov = overrides.as_ref();
    let delegation_text =
        read_briefing_section(pool, "briefing.delegation", FALLBACK_DELEGATION, ov).await;
    let escalate_text =
        read_briefing_section(pool, "briefing.escalate", FALLBACK_ESCALATE, ov).await;
    let coordination_text =
        read_briefing_section(pool, "briefing.coordination", FALLBACK_COORDINATION, ov).await;
    let messaging_text =
        read_briefing_section(pool, "briefing.messaging", FALLBACK_MESSAGING, ov).await;
    let recovery_text =
        read_briefing_section(pool, "briefing.recovery", FALLBACK_RECOVERY, ov).await;
    let rest_api_text =
        read_briefing_section(pool, "briefing.rest_api", FALLBACK_REST_API, ov).await;

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
    let overrides =
        load_project_briefing_overrides_in_tx(pool, tx, ctx.project_id.as_deref()).await;
    let ov = overrides.as_ref();
    let delegation_text =
        read_briefing_section_in_tx(pool, tx, "briefing.delegation", FALLBACK_DELEGATION, ov).await;
    let escalate_text =
        read_briefing_section_in_tx(pool, tx, "briefing.escalate", FALLBACK_ESCALATE, ov).await;
    let coordination_text =
        read_briefing_section_in_tx(pool, tx, "briefing.coordination", FALLBACK_COORDINATION, ov)
            .await;
    let messaging_text =
        read_briefing_section_in_tx(pool, tx, "briefing.messaging", FALLBACK_MESSAGING, ov).await;
    let recovery_text =
        read_briefing_section_in_tx(pool, tx, "briefing.recovery", FALLBACK_RECOVERY, ov).await;
    let rest_api_text =
        read_briefing_section_in_tx(pool, tx, "briefing.rest_api", FALLBACK_REST_API, ov).await;

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

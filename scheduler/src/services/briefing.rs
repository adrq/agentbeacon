use tracing::warn;

use crate::db;
use crate::db::DbPool;

pub struct BriefingContext {
    pub role: BriefingRole,
    pub slug: String,
    pub hierarchical_name: String,
    pub agent_config_name: String,
    pub parent_info: String,
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

const FALLBACK_REST_API: &str = "Environment variables for API access:\n\
- `$AGENTBEACON_SESSION_ID` — your auth token\n\
- `$AGENTBEACON_API_BASE` — scheduler base URL\n\
- `$AGENTBEACON_EXECUTION_ID` — current execution\n\
- `$AGENTBEACON_PROJECT_ID` — current project (if set)\n\n\
`GET $AGENTBEACON_API_BASE/api/docs` for the full API reference.\n\
Discover running sessions via `GET $AGENTBEACON_API_BASE/api/executions/$AGENTBEACON_EXECUTION_ID/sessions`.\n\
You have a REST API for coordinating with other agents — send messages to peers, \
read/write shared knowledge in the wiki, and discover who else is working in this execution. \
Write scripts to interact with the API (e.g. discover agents, filter results, send messages in a loop) \
rather than making one curl call at a time — process data in code, not in your context window.";

/// Read a briefing section from the config table, falling back to a compiled-in
/// default if the row is missing. Logs a warning on fallback so operators know
/// the config table is incomplete.
async fn read_briefing_section(pool: &DbPool, key: &str, fallback: &str) -> String {
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

async fn read_briefing_section_in_tx(
    pool: &DbPool,
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    key: &str,
    fallback: &str,
) -> String {
    let sql = pool.prepare_query("SELECT value FROM config WHERE name = ?");
    match sqlx::query(&sql).bind(key).fetch_one(&mut **tx).await {
        Ok(row) => {
            use sqlx::Row;
            row.get::<String, _>("value")
        }
        Err(_) => fallback.to_string(),
    }
}

pub async fn build_environment_briefing(pool: &DbPool, ctx: &BriefingContext) -> String {
    let delegation_text =
        read_briefing_section(pool, "briefing.delegation", FALLBACK_DELEGATION).await;
    let escalate_text = read_briefing_section(pool, "briefing.escalate", FALLBACK_ESCALATE).await;
    let rest_api_text = read_briefing_section(pool, "briefing.rest_api", FALLBACK_REST_API).await;

    build_environment_briefing_with_sections(ctx, &delegation_text, &escalate_text, &rest_api_text)
}

/// Like `build_environment_briefing` but reads config through an existing transaction.
pub async fn build_environment_briefing_in_tx(
    pool: &DbPool,
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    ctx: &BriefingContext,
) -> String {
    let delegation_text =
        read_briefing_section_in_tx(pool, tx, "briefing.delegation", FALLBACK_DELEGATION).await;
    let escalate_text =
        read_briefing_section_in_tx(pool, tx, "briefing.escalate", FALLBACK_ESCALATE).await;
    let rest_api_text =
        read_briefing_section_in_tx(pool, tx, "briefing.rest_api", FALLBACK_REST_API).await;

    build_environment_briefing_with_sections(ctx, &delegation_text, &escalate_text, &rest_api_text)
}

/// Pure function for testability — no DB dependency.
pub fn build_environment_briefing_with_sections(
    ctx: &BriefingContext,
    delegation_text: &str,
    escalate_text: &str,
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

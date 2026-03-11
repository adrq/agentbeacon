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

// Fallback text used when the config table row is missing or unreadable.
// The authoritative source is the config table, seeded by migration 0014.
// Edit briefing text via POST /api/config, not by changing these constants.

const FALLBACK_DELEGATION: &str = "Use the AgentBeacon `delegate` MCP tool to assign work to child agents.\n\
Use the AgentBeacon `release` MCP tool to terminate a child when done.\n\
Discover available agent configs via `GET $AGENTBEACON_API_BASE/api/executions/$AGENTBEACON_EXECUTION_ID/agents` before delegating.\n\
An **agent** is a configured specialist type (e.g., `backend-dev`). A **session** is a running instance — delegating to the same agent twice creates two independent sessions.";

const FALLBACK_ESCALATE: &str =
    "Use the AgentBeacon `escalate` MCP tool to surface questions to the user.";

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

pub async fn build_environment_briefing(pool: &DbPool, ctx: &BriefingContext) -> String {
    let delegation_text =
        read_briefing_section(pool, "briefing.delegation", FALLBACK_DELEGATION).await;
    let escalate_text = read_briefing_section(pool, "briefing.escalate", FALLBACK_ESCALATE).await;
    let rest_api_text = read_briefing_section(pool, "briefing.rest_api", FALLBACK_REST_API).await;

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

#[cfg(test)]
mod tests {
    use super::*;

    fn make_ctx(role: BriefingRole) -> BriefingContext {
        BriefingContext {
            role,
            slug: "swift-falcon".to_string(),
            hierarchical_name: "swift-falcon".to_string(),
            agent_config_name: "lead-agent".to_string(),
            parent_info: "user".to_string(),
        }
    }

    #[test]
    fn test_root_lead_briefing_has_all_sections() {
        let ctx = make_ctx(BriefingRole::RootLead);
        let briefing = build_environment_briefing_with_sections(
            &ctx,
            FALLBACK_DELEGATION,
            FALLBACK_ESCALATE,
            FALLBACK_REST_API,
        );
        assert!(briefing.contains("# AgentBeacon Environment"));
        assert!(briefing.contains("## Delegation"));
        assert!(briefing.contains("## Escalate"));
        assert!(briefing.contains("## REST API"));
        assert!(briefing.contains("root lead"));
    }

    #[test]
    fn test_sub_lead_briefing_omits_escalate() {
        let ctx = BriefingContext {
            role: BriefingRole::SubLead,
            slug: "bold-eagle".to_string(),
            hierarchical_name: "swift-falcon/bold-eagle".to_string(),
            agent_config_name: "backend-dev".to_string(),
            parent_info: "swift-falcon".to_string(),
        };
        let briefing = build_environment_briefing_with_sections(
            &ctx,
            FALLBACK_DELEGATION,
            FALLBACK_ESCALATE,
            FALLBACK_REST_API,
        );
        assert!(briefing.contains("## Delegation"));
        assert!(!briefing.contains("## Escalate"));
        assert!(briefing.contains("## REST API"));
        assert!(briefing.contains("sub-lead"));
    }

    #[test]
    fn test_leaf_briefing_omits_delegation_and_escalate() {
        let ctx = BriefingContext {
            role: BriefingRole::Leaf,
            slug: "keen-hawk".to_string(),
            hierarchical_name: "swift-falcon/bold-eagle/keen-hawk".to_string(),
            agent_config_name: "frontend-dev".to_string(),
            parent_info: "swift-falcon/bold-eagle".to_string(),
        };
        let briefing = build_environment_briefing_with_sections(
            &ctx,
            FALLBACK_DELEGATION,
            FALLBACK_ESCALATE,
            FALLBACK_REST_API,
        );
        assert!(!briefing.contains("## Delegation"));
        assert!(!briefing.contains("## Escalate"));
        assert!(briefing.contains("## REST API"));
        assert!(briefing.contains("leaf"));
    }

    #[test]
    fn test_briefing_under_800_tokens() {
        let ctx = make_ctx(BriefingRole::RootLead);
        let briefing = build_environment_briefing_with_sections(
            &ctx,
            FALLBACK_DELEGATION,
            FALLBACK_ESCALATE,
            FALLBACK_REST_API,
        );
        // Conservative estimate: ~4 chars per token
        let estimated_tokens = briefing.len() / 4;
        assert!(
            estimated_tokens < 800,
            "briefing is ~{estimated_tokens} tokens ({} chars), should be under 800",
            briefing.len()
        );
    }

    #[test]
    fn test_briefing_prepends_to_existing_prompt() {
        let briefing = "# AgentBeacon Environment\ntest briefing";
        let existing = "You are a helpful assistant.";
        let combined = prepend_briefing(briefing, existing);
        assert!(combined.starts_with("# AgentBeacon Environment"));
        assert!(combined.contains("---"));
        assert!(combined.ends_with(existing));
    }

    #[test]
    fn test_briefing_prepends_to_empty_prompt() {
        let briefing = "# AgentBeacon Environment\ntest briefing";
        let combined = prepend_briefing(briefing, "");
        assert_eq!(combined, briefing);
        assert!(!combined.contains("---"));
    }

    #[test]
    fn test_delegation_contains_api_discovery() {
        let ctx = make_ctx(BriefingRole::RootLead);
        let briefing = build_environment_briefing_with_sections(
            &ctx,
            FALLBACK_DELEGATION,
            FALLBACK_ESCALATE,
            FALLBACK_REST_API,
        );
        assert!(briefing.contains("/api/executions/$AGENTBEACON_EXECUTION_ID/agents"));
    }
}

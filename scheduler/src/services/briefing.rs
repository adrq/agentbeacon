use crate::db::execution_agents::ExecutionAgentInfo;

pub struct BriefingContext {
    pub role: BriefingRole,
    pub slug: String,
    pub hierarchical_name: String,
    pub agent_config_name: String,
    pub parent_info: String,
    pub available_agents: Vec<ExecutionAgentInfo>,
}

pub enum BriefingRole {
    RootLead,
    SubLead,
    Leaf,
}

pub fn build_environment_briefing(ctx: &BriefingContext) -> String {
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
        let agent_list = if ctx.available_agents.is_empty() {
            "  No agents configured for this execution.".to_string()
        } else {
            ctx.available_agents
                .iter()
                .map(|a| {
                    let desc = a.description.as_deref().unwrap_or("no description");
                    format!("- `{}` — {}", a.name, desc)
                })
                .collect::<Vec<_>>()
                .join("\n")
        };
        sections.push(format!(
            "## Delegation\n\
             Use the AgentBeacon `delegate` MCP tool to assign work to child agents.\n\
             Use the AgentBeacon `release` MCP tool to terminate a child when done.\n\
             Available agent configs:\n{agent_list}"
        ));
    }

    // Escalate section (root lead only)
    if matches!(ctx.role, BriefingRole::RootLead) {
        sections.push(
            "## Escalate\n\
             Use the AgentBeacon `escalate` MCP tool to surface questions to the user."
                .to_string(),
        );
    }

    // REST API section (all roles)
    sections.push(
        "## REST API\n\
         Environment variables for API access:\n\
         - `$AGENTBEACON_SESSION_ID` — your auth token\n\
         - `$AGENTBEACON_API_BASE` — scheduler base URL\n\
         - `$AGENTBEACON_EXECUTION_ID` — current execution\n\
         - `$AGENTBEACON_PROJECT_ID` — current project (if set)\n\n\
         `GET $AGENTBEACON_API_BASE/api/docs` for the full API reference.\n\
         You have a REST API for coordinating with other agents — send messages to peers, \
         read/write shared knowledge in the wiki, and discover who else is working in this execution. \
         Write scripts to interact with the API (e.g. discover agents, filter results, send messages in a loop) \
         rather than making one curl call at a time — process data in code, not in your context window."
            .to_string(),
    );

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

    fn sample_agents() -> Vec<ExecutionAgentInfo> {
        vec![
            ExecutionAgentInfo {
                name: "backend-dev".to_string(),
                description: Some("Rust backend developer".to_string()),
            },
            ExecutionAgentInfo {
                name: "frontend-dev".to_string(),
                description: Some("Svelte frontend developer".to_string()),
            },
            ExecutionAgentInfo {
                name: "reviewer".to_string(),
                description: None,
            },
        ]
    }

    #[test]
    fn test_root_lead_briefing_has_all_sections() {
        let ctx = BriefingContext {
            role: BriefingRole::RootLead,
            slug: "swift-falcon".to_string(),
            hierarchical_name: "swift-falcon".to_string(),
            agent_config_name: "lead-agent".to_string(),
            parent_info: "user".to_string(),
            available_agents: sample_agents(),
        };
        let briefing = build_environment_briefing(&ctx);
        assert!(briefing.contains("# AgentBeacon Environment"));
        assert!(briefing.contains("## Delegation"));
        assert!(briefing.contains("## Escalate"));
        assert!(briefing.contains("## REST API"));
        assert!(briefing.contains("`backend-dev`"));
        assert!(briefing.contains("`frontend-dev`"));
        assert!(briefing.contains("no description"));
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
            available_agents: sample_agents(),
        };
        let briefing = build_environment_briefing(&ctx);
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
            available_agents: sample_agents(),
        };
        let briefing = build_environment_briefing(&ctx);
        assert!(!briefing.contains("## Delegation"));
        assert!(!briefing.contains("## Escalate"));
        assert!(briefing.contains("## REST API"));
        assert!(briefing.contains("leaf"));
    }

    #[test]
    fn test_briefing_under_800_tokens() {
        let ctx = BriefingContext {
            role: BriefingRole::RootLead,
            slug: "swift-falcon".to_string(),
            hierarchical_name: "swift-falcon".to_string(),
            agent_config_name: "lead-agent".to_string(),
            parent_info: "user".to_string(),
            available_agents: vec![
                ExecutionAgentInfo {
                    name: "agent-1".to_string(),
                    description: Some("First agent".to_string()),
                },
                ExecutionAgentInfo {
                    name: "agent-2".to_string(),
                    description: Some("Second agent".to_string()),
                },
                ExecutionAgentInfo {
                    name: "agent-3".to_string(),
                    description: Some("Third agent".to_string()),
                },
                ExecutionAgentInfo {
                    name: "agent-4".to_string(),
                    description: Some("Fourth agent".to_string()),
                },
                ExecutionAgentInfo {
                    name: "agent-5".to_string(),
                    description: Some("Fifth agent".to_string()),
                },
            ],
        };
        let briefing = build_environment_briefing(&ctx);
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
    fn test_empty_agent_pool() {
        let ctx = BriefingContext {
            role: BriefingRole::RootLead,
            slug: "swift-falcon".to_string(),
            hierarchical_name: "swift-falcon".to_string(),
            agent_config_name: "lead-agent".to_string(),
            parent_info: "user".to_string(),
            available_agents: vec![],
        };
        let briefing = build_environment_briefing(&ctx);
        assert!(briefing.contains("No agents configured"));
    }
}

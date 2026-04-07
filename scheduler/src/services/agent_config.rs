//! Agent config composition.

use crate::db::{self, DbPool};
use crate::error::SchedulerError;
use crate::services::briefing::{self, BriefingContext, BriefingRole};

/// Build effective agent_config with composed briefing from a pre-built BriefingContext.
///
/// Handles defensive parsing (non-object config JSON → `{}`), prepends the
/// AgentBeacon environment briefing to the agent's system_prompt.
pub async fn compose_agent_config(
    pool: &DbPool,
    agent: &db::agents::Agent,
    briefing_ctx: &BriefingContext,
) -> serde_json::Value {
    let mut agent_config: serde_json::Value =
        serde_json::from_str::<serde_json::Value>(&agent.config)
            .ok()
            .filter(|v| v.is_object())
            .unwrap_or_else(|| serde_json::json!({}));

    let briefing = briefing::build_environment_briefing(pool, briefing_ctx).await;
    let existing_prompt = agent.system_prompt.as_deref().unwrap_or("");
    agent_config["system_prompt"] =
        serde_json::Value::String(briefing::prepend_briefing(&briefing, existing_prompt));

    agent_config
}

/// Like `compose_agent_config` but reads briefing config through an existing transaction.
pub async fn compose_agent_config_in_tx(
    pool: &DbPool,
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    agent: &db::agents::Agent,
    briefing_ctx: &BriefingContext,
) -> serde_json::Value {
    let mut agent_config: serde_json::Value =
        serde_json::from_str::<serde_json::Value>(&agent.config)
            .ok()
            .filter(|v| v.is_object())
            .unwrap_or_else(|| serde_json::json!({}));

    let briefing = briefing::build_environment_briefing_in_tx(pool, tx, briefing_ctx).await;
    let existing_prompt = agent.system_prompt.as_deref().unwrap_or("");
    agent_config["system_prompt"] =
        serde_json::Value::String(briefing::prepend_briefing(&briefing, existing_prompt));

    agent_config
}

/// Build briefing context from an existing session.
pub async fn briefing_context_for_session(
    pool: &DbPool,
    session: &db::sessions::Session,
    agent: &db::agents::Agent,
    execution: &db::Execution,
) -> Result<BriefingContext, SchedulerError> {
    let depth = db::sessions::compute_depth(pool, &session.id, &session.execution_id).await?;
    let role = if session.parent_session_id.is_none() {
        BriefingRole::RootLead
    } else if depth >= execution.max_depth {
        BriefingRole::Leaf
    } else {
        BriefingRole::SubLead
    };
    let hier_name =
        crate::services::messaging::hierarchical_name_for_session(pool, &session.id).await?;
    let parent_info = match &session.parent_session_id {
        Some(pid) => crate::services::messaging::hierarchical_name_for_session(pool, pid).await?,
        None => "user".to_string(),
    };
    let slug = if session.slug.is_empty() {
        session.id[..session.id.len().min(8)].to_string()
    } else {
        session.slug.clone()
    };
    Ok(BriefingContext {
        role,
        slug,
        hierarchical_name: hier_name,
        agent_config_name: agent.name.clone(),
        parent_info,
    })
}

/// Like `briefing_context_for_session` but reads through an existing transaction.
pub async fn briefing_context_for_session_in_tx(
    pool: &DbPool,
    tx: &mut sqlx::Transaction<'_, sqlx::Any>,
    session: &db::sessions::Session,
    agent: &db::agents::Agent,
    execution: &db::Execution,
) -> Result<BriefingContext, SchedulerError> {
    let depth =
        db::sessions::compute_depth_in_tx(pool, tx, &session.id, &session.execution_id).await?;
    let role = if session.parent_session_id.is_none() {
        BriefingRole::RootLead
    } else if depth >= execution.max_depth {
        BriefingRole::Leaf
    } else {
        BriefingRole::SubLead
    };
    let hier_name =
        crate::services::messaging::hierarchical_name_for_session_in_tx(pool, tx, &session.id)
            .await?;
    let parent_info = match &session.parent_session_id {
        Some(pid) => {
            crate::services::messaging::hierarchical_name_for_session_in_tx(pool, tx, pid).await?
        }
        None => "user".to_string(),
    };
    let slug = if session.slug.is_empty() {
        session.id[..session.id.len().min(8)].to_string()
    } else {
        session.slug.clone()
    };
    Ok(BriefingContext {
        role,
        slug,
        hierarchical_name: hier_name,
        agent_config_name: agent.name.clone(),
        parent_info,
    })
}

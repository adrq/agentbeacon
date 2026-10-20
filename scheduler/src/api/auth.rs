// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

use axum::{
    extract::FromRequestParts,
    http::request::Parts,
    response::{IntoResponse, Response},
};

use crate::api::authz::forbidden;
use crate::app::AppState;
use crate::db;
use crate::services::auth::principal::Principal;

#[derive(Debug, Clone, PartialEq)]
pub enum McpRole {
    RootLead,
    SubLead,
    Leaf,
}

pub struct McpSession {
    pub session_id: String,
    pub execution_id: String,
    pub agent_id: String,
    pub role: McpRole,
    pub depth: i64,
    pub max_depth: i64,
    pub max_width: i64,
    pub desired: String,
    pub executor_state: String,
    pub outcome: Option<String>,
    pub project_id: Option<String>,
    pub sandbox_policy: String,
}

impl FromRequestParts<AppState> for McpSession {
    type Rejection = Response;

    fn from_request_parts(
        parts: &mut Parts,
        state: &AppState,
    ) -> impl std::future::Future<Output = Result<Self, Self::Rejection>> + Send {
        let db_pool = state.db_pool.clone();
        let agent = parts
            .extensions
            .get::<Principal>()
            .and_then(|p| p.agent().cloned());

        async move {
            let agent = agent.ok_or_else(forbidden)?;
            let internal = |e: crate::error::SchedulerError| e.into_response();

            let session = db::sessions::get_by_id(&db_pool, &agent.session_id)
                .await
                .map_err(internal)?;
            let execution = db::executions::get_by_id(&db_pool, &session.execution_id)
                .await
                .map_err(internal)?;
            let depth = db::sessions::compute_depth(&db_pool, &session.id, &session.execution_id)
                .await
                .map_err(internal)?;

            let role = if session.parent_session_id.is_none() {
                McpRole::RootLead
            } else if depth >= execution.max_depth {
                McpRole::Leaf
            } else {
                McpRole::SubLead
            };

            Ok(McpSession {
                session_id: session.id,
                execution_id: session.execution_id,
                agent_id: session.agent_id,
                role,
                depth,
                max_depth: execution.max_depth,
                max_width: execution.max_width,
                desired: session.desired,
                executor_state: session.executor_state,
                outcome: session.outcome,
                project_id: execution.project_id.clone(),
                sandbox_policy: session.sandbox_policy,
            })
        }
    }
}

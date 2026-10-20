// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

use crate::db;
use crate::db::DbPool;
use crate::db::wiki_sharing::{
    ACCESS_READ, ACCESS_READ_WRITE, CROSS_PROJECT_WRITES_FLAG, SharedTagLink,
};
use crate::error::SchedulerError;
use crate::search::SearchScope;
use crate::services::auth::principal::{AgentPrincipal, Principal};

/// Whether writes to another project's page are allowed. Enabled unless the
/// stored value is exactly "false".
async fn cross_project_writes_enabled(pool: &DbPool) -> bool {
    match db::config::get(pool, CROSS_PROJECT_WRITES_FLAG).await {
        Ok(cfg) => match cfg.value.as_str() {
            "false" => false,
            "true" => true,
            other => {
                tracing::warn!(
                    "config '{}' has invalid value '{}', using default (enabled)",
                    CROSS_PROJECT_WRITES_FLAG,
                    other
                );
                true
            }
        },
        Err(SchedulerError::NotFound(_)) => true,
        Err(e) => {
            tracing::warn!(
                error = %e,
                "cross-project writes: config read failed; defaulting to enabled"
            );
            true
        }
    }
}

/// What a caller may do with one page.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Access {
    Read,
    ReadWrite,
}

impl Access {
    pub fn as_str(self) -> &'static str {
        match self {
            Access::Read => ACCESS_READ,
            Access::ReadWrite => ACCESS_READ_WRITE,
        }
    }
}

/// The identified caller: a session when an agent is calling, and always the
/// project the request is evaluated against.
pub struct Caller {
    pub session_id: Option<String>,
    pub project_id: String,
    /// The hash of the agent's credential, re-checked on held snapshots.
    pub credential_hash: Option<String>,
}

impl Caller {
    pub fn owns(&self, project_id: &str) -> bool {
        self.project_id == project_id
    }
}

/// Identify the caller of a project-scoped wiki route.
pub fn resolve_caller(
    principal: &Principal,
    target_project_id: &str,
) -> Result<Caller, SchedulerError> {
    match principal {
        Principal::Human { .. } => Ok(Caller {
            session_id: None,
            project_id: target_project_id.to_string(),
            credential_hash: None,
        }),
        Principal::Agent(agent) => agent_caller(agent),
        Principal::Worker { .. } => Err(SchedulerError::Forbidden("worker".into())),
    }
}

/// The caller for an agent, which must belong to a project.
pub fn agent_caller(agent: &AgentPrincipal) -> Result<Caller, SchedulerError> {
    let Some(project_id) = agent.project_id.clone() else {
        return Err(SchedulerError::Forbidden(
            "session is not scoped to a project".into(),
        ));
    };
    Ok(Caller {
        session_id: Some(agent.session_id.clone()),
        project_id,
        credential_hash: Some(agent.credential_hash.clone()),
    })
}

/// The links through which a caller reaches other projects' pages.
pub struct SharingContext {
    pub caller_project_id: String,
    /// The agent session calling, when one is. Re-resolved inside a read snapshot.
    session_id: Option<String>,
    credential_hash: Option<String>,
    pub links: Vec<SharedTagLink>,
    pub cross_project_writes: bool,
    caller_active: bool,
}

impl SharingContext {
    pub async fn load(pool: &DbPool, caller: &Caller) -> Result<Self, SchedulerError> {
        let caller_project_id = &caller.project_id;
        Ok(Self {
            caller_project_id: caller_project_id.to_string(),
            session_id: caller.session_id.clone(),
            credential_hash: caller.credential_hash.clone(),
            links: db::wiki_sharing::shared_links(pool, caller_project_id).await?,
            cross_project_writes: cross_project_writes_enabled(pool).await,
            caller_active: db::projects::resolve(pool, caller_project_id).await.is_ok(),
        })
    }

    /// Every project the caller can see pages in, its own first.
    pub fn reachable_project_ids(&self) -> Vec<String> {
        if !self.caller_active {
            return Vec::new();
        }
        let mut ids = vec![self.caller_project_id.clone()];
        for link in &self.links {
            if !ids.contains(&link.owner_project_id) {
                ids.push(link.owner_project_id.clone());
            }
        }
        ids
    }

    /// The same context with membership and project state re-read on a held snapshot.
    pub async fn in_tx(
        &self,
        tx: &mut sqlx::Transaction<'_, sqlx::Any>,
        pool: &DbPool,
    ) -> Result<Self, SchedulerError> {
        if let Some(session_id) = &self.session_id {
            let credential = self.credential_hash.as_deref().unwrap_or_default();
            match db::credentials::live_session_project_in_tx(tx, pool, session_id, credential)
                .await?
            {
                Some(Some(project_id)) if project_id == self.caller_project_id => {}
                _ => return Err(SchedulerError::Unauthorized("session is not live".into())),
            }
        }

        Ok(Self {
            caller_project_id: self.caller_project_id.clone(),
            session_id: self.session_id.clone(),
            credential_hash: self.credential_hash.clone(),
            links: db::wiki_sharing::shared_links_in_tx(tx, pool, &self.caller_project_id).await?,
            cross_project_writes: db::wiki::cross_project_writes_in_tx(tx, pool).await,
            caller_active: db::projects::is_active_in_tx(tx, pool, &self.caller_project_id).await?,
        })
    }

    /// Access to a page in `project_id` carrying `tags`, or `None` if unreachable.
    pub fn access_to(&self, project_id: &str, tags: &[String]) -> Option<Access> {
        if !self.caller_active {
            return None;
        }
        if project_id == self.caller_project_id {
            return Some(Access::ReadWrite);
        }

        let mut access = None;
        for link in &self.links {
            if link.owner_project_id != project_id {
                continue;
            }
            if !tags.contains(&link.tag_name) {
                continue;
            }
            if link.access_level == ACCESS_READ_WRITE && self.cross_project_writes {
                return Some(Access::ReadWrite);
            }
            access = Some(Access::Read);
        }
        access
    }

    /// Tag names by owning project, for scope filtering.
    pub fn tag_pairs(&self) -> Vec<(String, String)> {
        self.links
            .iter()
            .map(|l| (l.owner_project_id.clone(), l.tag_name.clone()))
            .collect()
    }

    /// Search scope: the caller's own project plus every explicit pair.
    pub fn search_scope(&self) -> SearchScope {
        if !self.caller_active {
            return SearchScope::default();
        }
        SearchScope {
            projects: vec![self.caller_project_id.clone()],
            tagged: self.tag_pairs(),
        }
    }

    /// Co-member projects reachable through `tag_name`.
    pub fn co_members_of(&self, tag_name: &str) -> Vec<String> {
        self.links
            .iter()
            .filter(|l| l.tag_name == tag_name)
            .map(|l| l.owner_project_id.clone())
            .collect()
    }
}

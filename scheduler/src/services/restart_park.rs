use std::collections::{HashMap, HashSet};

use crate::db::{self, DbPool};
use crate::error::SchedulerError;
use crate::services::transition::{self, Action, Desired};

#[derive(Debug, Default)]
pub struct RestartPauseSummary {
    pub paused: usize,
    pub failures: Vec<String>,
}

pub async fn park_live_sessions_on_boot(pool: &DbPool) -> RestartPauseSummary {
    let mut summary = RestartPauseSummary::default();

    if !pause_enabled(pool).await {
        return summary;
    }

    let candidates = match db::sessions::find_restart_pause_candidates(pool).await {
        Ok(c) => c,
        Err(e) => {
            tracing::warn!(error = %e, "startup pause: candidate snapshot failed; leaving sessions running");
            return summary;
        }
    };
    if candidates.is_empty() {
        return summary;
    }

    let mut exec_ids: Vec<String> = candidates.iter().map(|c| c.execution_id.clone()).collect();
    exec_ids.sort();
    exec_ids.dedup();
    let ancestry = match db::sessions::load_session_ancestry(pool, &exec_ids).await {
        Ok(a) => a,
        Err(e) => {
            tracing::warn!(error = %e, "startup pause: ancestry load failed; leaving sessions running");
            return summary;
        }
    };
    let nodes: HashMap<String, db::sessions::SessionAncestryNode> =
        ancestry.into_iter().map(|n| (n.id.clone(), n)).collect();

    for cand in &candidates {
        match classify_ancestry(&cand.id, &nodes) {
            AncestorCheck::Clean => {}
            AncestorCheck::Terminating => continue,
            AncestorCheck::Corrupt => {
                tracing::warn!(
                    session_id = %cand.id,
                    "startup pause: malformed ancestry; leaving session running"
                );
                note_left_running(pool, cand, &mut summary).await;
                continue;
            }
        }

        let pending = match db::sessions::count_pending_turns(pool, &cand.id).await {
            Ok(n) => n,
            Err(e) => {
                tracing::warn!(
                    session_id = %cand.id, error = %e,
                    "startup pause: pending count failed; leaving session running"
                );
                note_left_running(pool, cand, &mut summary).await;
                continue;
            }
        };
        if pending > 0 {
            let payload = serde_json::json!({
                "message": format!("{pending} queued message(s) discarded on server restart.")
            });
            let payload_str = serde_json::to_string(&payload).unwrap_or_default();
            if let Err(e) = db::events::insert(
                pool,
                &cand.execution_id,
                Some(&cand.id),
                "platform",
                &payload_str,
            )
            .await
            {
                tracing::warn!(
                    session_id = %cand.id, error = %e,
                    "startup pause: discard warning insert failed; leaving session running"
                );
                note_left_running(pool, cand, &mut summary).await;
                continue;
            }
        }

        if cand.command_has_payload {
            let payload = serde_json::json!({
                "message": "Agent recovered from a crash. A message may have been lost."
            });
            let payload_str = serde_json::to_string(&payload).unwrap_or_default();
            let _ = db::events::insert(
                pool,
                &cand.execution_id,
                Some(&cand.id),
                "platform",
                &payload_str,
            )
            .await;
        }

        match transition::transition(
            pool,
            &cand.execution_id,
            &cand.id,
            Action::SetDesired(Desired::Stop, "system:restart".into()),
        )
        .await
        {
            Ok(_) => summary.paused += 1,
            Err(_) => note_left_running(pool, cand, &mut summary).await,
        }
    }

    if summary.failures.is_empty() {
        tracing::info!(paused = summary.paused, "startup pause complete");
    } else {
        tracing::warn!(
            paused = summary.paused,
            left_running = ?summary.failures,
            "startup pause complete; {} session(s) left running after server restart",
            summary.failures.len()
        );
    }

    summary
}

async fn pause_enabled(pool: &DbPool) -> bool {
    match db::config::get(pool, "restart.pause_sessions").await {
        Ok(cfg) => match cfg.value.as_str() {
            "false" => false,
            "true" => true,
            other => {
                tracing::warn!(
                    "config 'restart.pause_sessions' has invalid value '{}', using default (enabled)",
                    other
                );
                true
            }
        },
        Err(SchedulerError::NotFound(_)) => true,
        Err(e) => {
            tracing::warn!(error = %e, "startup pause: config read failed; defaulting to pause enabled");
            true
        }
    }
}

async fn note_left_running(
    pool: &DbPool,
    cand: &db::sessions::RestartPauseCandidate,
    summary: &mut RestartPauseSummary,
) {
    summary.failures.push(cand.id.clone());
    let payload = serde_json::json!({"message": "Session left running after server restart."});
    let payload_str = serde_json::to_string(&payload).unwrap_or_default();
    let _ = db::events::insert(
        pool,
        &cand.execution_id,
        Some(&cand.id),
        "platform",
        &payload_str,
    )
    .await;
}

#[derive(Debug, PartialEq)]
enum AncestorCheck {
    Clean,
    Terminating,
    Corrupt,
}

fn classify_ancestry(
    start_id: &str,
    nodes: &HashMap<String, db::sessions::SessionAncestryNode>,
) -> AncestorCheck {
    let cap = nodes.len();
    let mut visited: HashSet<&str> = HashSet::new();
    let mut current = start_id;
    let mut steps = 0usize;

    loop {
        if steps > cap {
            return AncestorCheck::Corrupt;
        }
        let node = match nodes.get(current) {
            Some(n) => n,
            None => return AncestorCheck::Corrupt,
        };
        if !visited.insert(node.id.as_str()) {
            return AncestorCheck::Corrupt;
        }
        if current != start_id && (node.desired == "terminate" || node.outcome.is_some()) {
            return AncestorCheck::Terminating;
        }
        match node.parent_session_id.as_deref() {
            None => return AncestorCheck::Clean,
            Some(pid) => match nodes.get(pid) {
                None => return AncestorCheck::Corrupt,
                Some(pnode) if pnode.execution_id != node.execution_id => {
                    return AncestorCheck::Corrupt;
                }
                Some(_) => {
                    current = pid;
                    steps += 1;
                }
            },
        }
    }
}

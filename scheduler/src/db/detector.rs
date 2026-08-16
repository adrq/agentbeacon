//! Post-cutover orphan-source detector: an admin CLI audit that reports any answer source
//! lacking a matching marker under its batch owner.

use super::DbPool;
use super::backfill;
use crate::error::SchedulerError;
use sqlx::{Any, Transaction};

/// Open the detector's read-only, point-in-time snapshot on the transaction.
async fn set_snapshot_isolation(
    pool: &DbPool,
    tx: &mut Transaction<'_, Any>,
) -> Result<(), SchedulerError> {
    if pool.is_postgres() {
        sqlx::query("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            .execute(&mut **tx)
            .await
            .map_err(|e| SchedulerError::Database(format!("set snapshot isolation: {e}")))?;
        sqlx::query("SET LOCAL statement_timeout = 600000")
            .execute(&mut **tx)
            .await
            .map_err(|e| {
                SchedulerError::Database(format!("set detector statement_timeout: {e}"))
            })?;
    }
    Ok(())
}

/// Run the detector against an explicitly resolved database and return the process exit code.
pub async fn run_orphan_detector(db_url: &str) -> i32 {
    match detect(db_url).await {
        Ok(code) => code,
        Err(e) => {
            eprintln!("orphan detector error: {e}");
            2
        }
    }
}

async fn detect(db_url: &str) -> Result<i32, SchedulerError> {
    let pool = super::pool::create(db_url).await?;

    let version = super::migrations::get_current_version(&pool)
        .await
        .unwrap_or(0);
    let applied = super::migrations::applied_versions(&pool)
        .await
        .unwrap_or_default();
    if !applied.contains(&24) {
        eprintln!(
            "orphan detector: schema version {version} is below 24; run the 0024 migration first"
        );
        return Ok(2);
    }

    let mut tx = pool
        .begin()
        .await
        .map_err(|e| SchedulerError::Database(format!("begin detector snapshot: {e}")))?;
    set_snapshot_isolation(&pool, &mut tx).await?;

    let cap = backfill::map_cap_bytes();
    let (mut orphans, stats) =
        backfill::collect_uncovered_sources(&pool, &mut tx, cap, None, false).await?;
    let _ = tx.rollback().await;

    eprintln!(
        "orphan detector: {} candidate(s) skipped: no representable marker within cap",
        stats.envelope_oversize_skipped
    );

    orphans.sort_by_key(|k| k.3);
    for (execution_id, session_id, batch_id, source_event_id) in &orphans {
        let line = serde_json::json!({
            "schema_version": version,
            "owner_execution_id": execution_id,
            "owner_session_id": session_id,
            "batch_id": batch_id,
            "source_event_id": source_event_id,
        });
        println!("{}", serde_json::to_string(&line).unwrap_or_default());
    }

    Ok(if orphans.is_empty() { 0 } else { 1 })
}

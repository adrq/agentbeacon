// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

//! Recording an execution's network ceiling.

use serde_json::{Value, json};

use crate::api::problem::{Problem, ProblemCode};
use crate::db::{self, DbPool};
use crate::error::SchedulerError;
use crate::services::network_policy::Ceiling;

/// `data.type` of the platform event recording a ceiling.
pub const CEILING_CHANGED: &str = "ceiling_changed";

/// Platform payload recording that `ceiling` replaced `previous` (`None` at launch).
pub fn ceiling_changed_payload(
    execution_id: &str,
    ceiling: &Ceiling,
    previous: Option<&Ceiling>,
    by: &str,
    at: &str,
) -> Value {
    json!({
        "parts": [{
            "data": {
                "type": CEILING_CHANGED,
                "execution_id": execution_id,
                "ceiling": ceiling.to_wire(),
                "previous": previous.map(Ceiling::to_wire),
                "by": by,
                "at": at,
            }
        }]
    })
}

/// Write the `ceiling_changed` event for the ceiling now stored on the
/// transaction's execution.
pub async fn write_ceiling_changed_in_tx(
    pool: &DbPool,
    tx: &mut db::executions::ExecutionTx<'_>,
    previous: Option<&Ceiling>,
) -> Result<(), SchedulerError> {
    let execution_id = tx.execution_id().to_string();
    let exec = db::executions::get_in_tx(pool, tx, &execution_id).await?;
    let ceiling = Ceiling::from_columns(&exec.ceiling_mode, &exec.ceiling_entries)?;
    let by = exec.ceiling_by.as_deref().unwrap_or("user");
    let at = exec
        .ceiling_at
        .map(|t| t.to_rfc3339())
        .ok_or_else(|| SchedulerError::Database("ceiling_at missing after write".into()))?;
    let payload = ceiling_changed_payload(&execution_id, &ceiling, previous, by, &at);
    db::events::insert_in_tx(pool, tx, None, "platform", &payload.to_string()).await?;
    Ok(())
}

fn problem(code: ProblemCode, detail: &str) -> SchedulerError {
    SchedulerError::Problem(Box::new(Problem::new(code).with_detail(detail)))
}

/// Replace a sandboxed execution's ceiling and record the change.
///
/// Returns whether anything was written: a ceiling equal to the current one
/// writes nothing.
pub async fn set_ceiling(
    pool: &DbPool,
    execution_id: &str,
    ceiling: &Ceiling,
) -> Result<bool, SchedulerError> {
    let mut tx = db::executions::begin_execution_tx(pool, execution_id).await?;
    let result = async {
        let exec = db::executions::get_in_tx(pool, &mut tx, execution_id).await?;
        if exec.desired == "terminate" || exec.outcome.is_some() {
            return Err(problem(
                ProblemCode::ExecutionTerminated,
                "the execution has ended; its ceiling cannot be changed",
            ));
        }
        let current = Ceiling::from_columns(&exec.ceiling_mode, &exec.ceiling_entries)?;
        if current == Ceiling::Unsandboxed {
            return Err(problem(
                ProblemCode::ResourceConflict,
                "this execution is unsandboxed; its ceiling cannot be changed",
            ));
        }
        if current == *ceiling {
            return Ok(false);
        }
        let (mode, entries) = ceiling.to_columns();
        let sql = pool.prepare_query(
            "UPDATE executions SET ceiling_mode = ?, ceiling_entries = ?, ceiling_by = 'user', \
             ceiling_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP \
             WHERE id = ? AND desired = 'run' AND outcome IS NULL",
        );
        let updated = sqlx::query(&sql)
            .bind(mode)
            .bind(&entries)
            .bind(execution_id)
            .execute(&mut **tx)
            .await
            .map_err(|e| SchedulerError::Database(format!("update ceiling failed: {e}")))?
            .rows_affected();
        if updated == 0 {
            return Err(problem(
                ProblemCode::ExecutionTerminated,
                "the execution has ended; its ceiling cannot be changed",
            ));
        }
        write_ceiling_changed_in_tx(pool, &mut tx, Some(&current)).await?;
        Ok(true)
    }
    .await;
    match result {
        Ok(changed) => {
            tx.commit()
                .await
                .map_err(|e| SchedulerError::Database(format!("commit ceiling failed: {e}")))?;
            Ok(changed)
        }
        Err(e) => {
            let _ = tx.rollback().await;
            Err(e)
        }
    }
}

use sqlx::pool::PoolConnection;
use sqlx::{Acquire, Executor};

use super::DbPool;
use crate::error::SchedulerError;

/// Highest schema version this binary ships; also the PostgreSQL `application_name` tag
/// (`agentbeacon-schema{LATEST_SCHEMA_VERSION}`).
pub const LATEST_SCHEMA_VERSION: i32 = 24;

/// Embedded migration files
const MIGRATION_0001: &str = include_str!("../../migrations/0001_initial.sql");
const MIGRATION_0002: &str =
    include_str!("../../migrations/0002_rename_workspaces_to_projects.sql");
const MIGRATION_0002_PG: &str =
    include_str!("../../migrations/0002_pg_rename_workspaces_to_projects.sql");
const MIGRATION_0003: &str = include_str!("../../migrations/0003_add_msg_seq.sql");
const MIGRATION_0003_PG: &str = include_str!("../../migrations/0003_pg_add_msg_seq.sql");
const MIGRATION_0004: &str = include_str!("../../migrations/0004_add_parent_session_index.sql");
const MIGRATION_0004_PG: &str =
    include_str!("../../migrations/0004_pg_add_parent_session_index.sql");
const MIGRATION_0005: &str = include_str!("../../migrations/0005_data_model_split.sql");
const MIGRATION_0005_PG: &str = include_str!("../../migrations/0005_pg_data_model_split.sql");
const MIGRATION_0006: &str = include_str!("../../migrations/0006_hierarchy_limits.sql");
const MIGRATION_0006_PG: &str = include_str!("../../migrations/0006_pg_hierarchy_limits.sql");
const MIGRATION_0007: &str = include_str!("../../migrations/0007_session_slugs.sql");
const MIGRATION_0007_PG: &str = include_str!("../../migrations/0007_pg_session_slugs.sql");
const MIGRATION_0008: &str = include_str!("../../migrations/0008_fix_slug_index.sql");
const MIGRATION_0008_PG: &str = include_str!("../../migrations/0008_pg_fix_slug_index.sql");
const MIGRATION_0009: &str = include_str!("../../migrations/0009_wiki.sql");
const MIGRATION_0009_PG: &str = include_str!("../../migrations/0009_pg_wiki.sql");
const MIGRATION_0010: &str = include_str!("../../migrations/0010_recovery_attempts.sql");
const MIGRATION_0010_PG: &str = include_str!("../../migrations/0010_pg_recovery_attempts.sql");
const MIGRATION_0011: &str = include_str!("../../migrations/0011_drop_coordination_mode.sql");
const MIGRATION_0011_PG: &str = include_str!("../../migrations/0011_pg_drop_coordination_mode.sql");
const MIGRATION_0012: &str = include_str!("../../migrations/0012_worktree_to_sessions.sql");
const MIGRATION_0012_PG: &str = include_str!("../../migrations/0012_pg_worktree_to_sessions.sql");
const MIGRATION_0013: &str = include_str!("../../migrations/0013_wiki_extras.sql");
const MIGRATION_0013_PG: &str = include_str!("../../migrations/0013_pg_wiki_extras.sql");
const MIGRATION_0014: &str = include_str!("../../migrations/0014_dynamic_briefing.sql");
const MIGRATION_0014_PG: &str = include_str!("../../migrations/0014_pg_dynamic_briefing.sql");
const MIGRATION_0015: &str = include_str!("../../migrations/0015_mcp_servers.sql");
const MIGRATION_0015_PG: &str = include_str!("../../migrations/0015_pg_mcp_servers.sql");
const MIGRATION_0016: &str = include_str!("../../migrations/0016_base_commit_sha.sql");
const MIGRATION_0016_PG: &str = include_str!("../../migrations/0016_pg_base_commit_sha.sql");
const MIGRATION_0017: &str = include_str!("../../migrations/0017_a2a_v1_part_format.sql");
const MIGRATION_0017_PG: &str = include_str!("../../migrations/0017_pg_a2a_v1_part_format.sql");
const MIGRATION_0018: &str = include_str!("../../migrations/0018_last_progress_at.sql");
const MIGRATION_0018_PG: &str = include_str!("../../migrations/0018_pg_last_progress_at.sql");
const MIGRATION_0019: &str = include_str!("../../migrations/0019_state_machine.sql");
const MIGRATION_0019_PG: &str = include_str!("../../migrations/0019_pg_state_machine.sql");
const MIGRATION_0020: &str = include_str!("../../migrations/0020_briefing_sections.sql");
const MIGRATION_0020_PG: &str = include_str!("../../migrations/0020_pg_briefing_sections.sql");
const MIGRATION_0021: &str = include_str!("../../migrations/0021_briefing_messaging_patch.sql");
const MIGRATION_0021_PG: &str =
    include_str!("../../migrations/0021_pg_briefing_messaging_patch.sql");
const MIGRATION_0022: &str = include_str!("../../migrations/0022_sandbox_policy.sql");
const MIGRATION_0022_PG: &str = include_str!("../../migrations/0022_pg_sandbox_policy.sql");
const MIGRATION_0023: &str = include_str!("../../migrations/0023_briefing_defaults.sql");
const MIGRATION_0023_PG: &str = include_str!("../../migrations/0023_pg_briefing_defaults.sql");

/// Replace SQL type keyword using sqlparser tokenizer for correctness
///
/// Only replaces when keyword appears as a type (after column name),
/// not when used as a column name itself.
///
/// # Example
/// ```ignore
/// replace_type_with_tokenizer("is_latest BOOLEAN NOT NULL", "BOOLEAN", "INTEGER")
/// // Returns: "is_latest INTEGER NOT NULL"
/// ```
fn replace_type_with_tokenizer(sql: &str, from_type: &str, to_type: &str) -> String {
    use sqlparser::dialect::GenericDialect;
    use sqlparser::tokenizer::{Token, Tokenizer};

    let dialect = GenericDialect {};
    let mut tokenizer = Tokenizer::new(&dialect, sql);

    let tokens = match tokenizer.tokenize() {
        Ok(tokens) => tokens,
        Err(_) => {
            // Fallback for malformed SQL
            return sql.to_string();
        }
    };

    let mut result = String::new();
    let mut prev_word_token: Option<Token> = None;

    for token in tokens {
        if let Token::Word(ref w) = token {
            // Only replace uppercase type keyword
            if w.value == from_type {
                // Only replace if previous WORD token exists (column/table name)
                let should_replace = prev_word_token.is_some();

                if should_replace {
                    result.push_str(to_type);
                    prev_word_token = Some(token.clone());
                    continue;
                }
            }
            // Update prev_word_token for any Word token
            prev_word_token = Some(token.clone());
        }
        // Preserve all other tokens
        result.push_str(&token.to_string());
    }

    result
}

/// Replace BOOLEAN with INTEGER for SQLite using sqlparser tokenizer for correctness
///
/// SQLite doesn't have a native BOOLEAN type - it stores booleans as INTEGER (0/1).
/// This function replaces BOOLEAN type declarations with INTEGER to ensure schema
/// metadata matches the actual storage type, preventing SQLx type checking errors.
///
/// Only replaces BOOLEAN when it appears as a SQL type (after a column name),
/// not when it's used as a column name itself.
///
/// # Example
/// ```ignore
/// replace_boolean_with_integer("is_latest BOOLEAN NOT NULL")
/// // Returns: "is_latest INTEGER NOT NULL"
/// ```
fn replace_boolean_with_integer(sql: &str) -> String {
    replace_type_with_tokenizer(sql, "BOOLEAN", "INTEGER")
}

/// Replace TIMESTAMP with TEXT for SQLite using sqlparser tokenizer for correctness
///
/// SQLite doesn't have a native TIMESTAMP type - we store RFC3339 strings as TEXT.
/// This function replaces TIMESTAMP type declarations with TEXT to ensure schema
/// metadata matches the actual storage type, preventing SQLx type checking errors.
///
/// Only replaces TIMESTAMP when it appears as a SQL type (after a column name),
/// not when it's used as a column name itself (e.g., `timestamp TIMESTAMP`).
///
/// # Example
/// ```ignore
/// replace_timestamp_with_text("created_at TIMESTAMP NOT NULL")
/// // Returns: "created_at TEXT NOT NULL"
/// ```
fn replace_timestamp_with_text(sql: &str) -> String {
    replace_type_with_tokenizer(sql, "TIMESTAMP", "TEXT")
}

/// Replace TIMESTAMP with TIMESTAMPTZ using sqlparser tokenizer for correctness
///
/// This function uses sqlparser's tokenizer to safely identify TIMESTAMP type keywords
/// vs column names, string literals, or comments, preventing corruption.
///
/// Only replaces TIMESTAMP when it appears as a SQL type (after a column name),
/// not when it's used as a column name itself (e.g., `timestamp TIMESTAMP`).
///
/// Handles all cases: `TIMESTAMP `, `TIMESTAMP,`, `TIMESTAMP)`, `TIMESTAMP;`, etc.
///
/// # Example
/// ```ignore
/// replace_timestamp_with_timestamptz("completed_at TIMESTAMP, created_at TIMESTAMP NOT NULL")
/// // Returns: "completed_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL"
///
/// replace_timestamp_with_timestamptz("timestamp TIMESTAMP NOT NULL")
/// // Returns: "timestamp TIMESTAMPTZ NOT NULL" (column name preserved, type replaced)
/// ```
fn replace_timestamp_with_timestamptz(sql: &str) -> String {
    replace_type_with_tokenizer(sql, "TIMESTAMP", "TIMESTAMPTZ")
}

/// One ordered migration step: a SQL file or the 0024 Rust code-step. Both flow through a single
/// version-ordered loop, so the code-step sits between v23 and any future step by construction.
enum MigrationStep {
    Sql(&'static str),
    Code0024,
}

/// The ordered migration steps this binary ships: SQL 1..23, then the 0024 code-step.
// v2/v3/... use a separate PostgreSQL migration file vs SQLite; v1 is shared.
fn default_migration_steps(is_postgres: bool) -> Vec<(MigrationStep, i32)> {
    let pick = |pg: &'static str, lite: &'static str| if is_postgres { pg } else { lite };
    vec![
        (MigrationStep::Sql(MIGRATION_0001), 1),
        (
            MigrationStep::Sql(pick(MIGRATION_0002_PG, MIGRATION_0002)),
            2,
        ),
        (
            MigrationStep::Sql(pick(MIGRATION_0003_PG, MIGRATION_0003)),
            3,
        ),
        (
            MigrationStep::Sql(pick(MIGRATION_0004_PG, MIGRATION_0004)),
            4,
        ),
        (
            MigrationStep::Sql(pick(MIGRATION_0005_PG, MIGRATION_0005)),
            5,
        ),
        (
            MigrationStep::Sql(pick(MIGRATION_0006_PG, MIGRATION_0006)),
            6,
        ),
        (
            MigrationStep::Sql(pick(MIGRATION_0007_PG, MIGRATION_0007)),
            7,
        ),
        (
            MigrationStep::Sql(pick(MIGRATION_0008_PG, MIGRATION_0008)),
            8,
        ),
        (
            MigrationStep::Sql(pick(MIGRATION_0009_PG, MIGRATION_0009)),
            9,
        ),
        (
            MigrationStep::Sql(pick(MIGRATION_0010_PG, MIGRATION_0010)),
            10,
        ),
        (
            MigrationStep::Sql(pick(MIGRATION_0011_PG, MIGRATION_0011)),
            11,
        ),
        (
            MigrationStep::Sql(pick(MIGRATION_0012_PG, MIGRATION_0012)),
            12,
        ),
        (
            MigrationStep::Sql(pick(MIGRATION_0013_PG, MIGRATION_0013)),
            13,
        ),
        (
            MigrationStep::Sql(pick(MIGRATION_0014_PG, MIGRATION_0014)),
            14,
        ),
        (
            MigrationStep::Sql(pick(MIGRATION_0015_PG, MIGRATION_0015)),
            15,
        ),
        (
            MigrationStep::Sql(pick(MIGRATION_0016_PG, MIGRATION_0016)),
            16,
        ),
        (
            MigrationStep::Sql(pick(MIGRATION_0017_PG, MIGRATION_0017)),
            17,
        ),
        (
            MigrationStep::Sql(pick(MIGRATION_0018_PG, MIGRATION_0018)),
            18,
        ),
        (
            MigrationStep::Sql(pick(MIGRATION_0019_PG, MIGRATION_0019)),
            19,
        ),
        (
            MigrationStep::Sql(pick(MIGRATION_0020_PG, MIGRATION_0020)),
            20,
        ),
        (
            MigrationStep::Sql(pick(MIGRATION_0021_PG, MIGRATION_0021)),
            21,
        ),
        (
            MigrationStep::Sql(pick(MIGRATION_0022_PG, MIGRATION_0022)),
            22,
        ),
        (
            MigrationStep::Sql(pick(MIGRATION_0023_PG, MIGRATION_0023)),
            23,
        ),
        (MigrationStep::Code0024, 24),
    ]
}

/// Run all pending migrations on the database.
pub async fn run(pool: &DbPool, database_url: &str) -> Result<(), SchedulerError> {
    // PostgreSQL supports both postgres:// and postgresql:// schemes (RFC 3986).
    let is_postgres =
        database_url.starts_with("postgres:") || database_url.starts_with("postgresql:");
    let current_version = get_current_version(pool).await.unwrap_or(0);
    run_migration_steps(
        pool,
        is_postgres,
        current_version,
        default_migration_steps(is_postgres),
    )
    .await
}

/// Apply the given steps in version order, skipping already-applied versions (idempotent).
async fn run_migration_steps(
    pool: &DbPool,
    is_postgres: bool,
    current_version: i32,
    steps: Vec<(MigrationStep, i32)>,
) -> Result<(), SchedulerError> {
    for (step, version) in steps {
        if version <= current_version {
            continue;
        }
        match step {
            MigrationStep::Sql(migration_sql) => {
                apply_sql_migration(pool, is_postgres, migration_sql, version).await?;
            }
            MigrationStep::Code0024 => run_code_step_0024(pool).await?,
        }
    }
    Ok(())
}

/// Apply one SQL migration: adapt dialect, split statements, and run them in a per-version
/// transaction on a single connection, toggling foreign_keys around the versions that need it.
async fn apply_sql_migration(
    pool: &DbPool,
    is_postgres: bool,
    migration_sql: &str,
    version: i32,
) -> Result<(), SchedulerError> {
    // Migration 0002 uses DROP TABLE which triggers CASCADE with foreign_keys ON.
    // Disable FKs before the migration and re-enable after.
    let needs_fk_disable = !is_postgres
        && (version == 2
            || version == 5
            || version == 14
            || version == 17
            || version == 19
            || version == 22);

    // Adapt migration for database-specific syntax
    let migration = if is_postgres {
        // Replace SQLite-specific syntax with PostgreSQL equivalents
        // 1. AUTOINCREMENT -> SERIAL for auto-increment columns
        // 2. INSERT OR IGNORE -> INSERT ... ON CONFLICT DO NOTHING
        // 3. TIMESTAMP -> TIMESTAMPTZ for timezone-aware storage (fixes timezone bug)
        //    Uses sqlparser tokenizer to handle all cases: TIMESTAMP, TIMESTAMP,
        //    TIMESTAMP), TIMESTAMP;, etc. Prevents timezone shifts on non-UTC servers.
        let m = migration_sql
            .replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
            .replace(
                &format!("INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES ({version}, CURRENT_TIMESTAMP)"),
                &format!("INSERT INTO schema_migrations (version, applied_at) VALUES ({version}, CURRENT_TIMESTAMP) ON CONFLICT (version) DO NOTHING")
            );

        // Use tokenizer to replace all TIMESTAMP → TIMESTAMPTZ comprehensively
        replace_timestamp_with_timestamptz(&m)
    } else {
        // SQLite: Replace semantic types with storage types for schema metadata compatibility
        // 1. BOOLEAN → INTEGER (SQLite stores booleans as 0/1)
        // 2. TIMESTAMP → TEXT (we store RFC3339 strings)
        let m = replace_boolean_with_integer(migration_sql);
        replace_timestamp_with_text(&m)
    };

    // Remove comments first, then split by semicolon
    let cleaned_migration = migration
        .lines()
        .filter(|line| {
            let trimmed = line.trim();
            !trimmed.is_empty() && !trimmed.starts_with("--")
        })
        .collect::<Vec<&str>>()
        .join("\n");

    let statements: Vec<&str> = cleaned_migration
        .split(';')
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .collect();

    // Acquire a single connection — PRAGMA and transaction MUST share it.
    let mut conn = pool.as_ref().acquire().await.map_err(|e| {
        SchedulerError::Database(format!(
            "acquire connection for migration v{version} failed: {e}"
        ))
    })?;

    if needs_fk_disable {
        conn.execute("PRAGMA foreign_keys = OFF")
            .await
            .map_err(|e| SchedulerError::Database(format!("disable foreign_keys failed: {e}")))?;
    }

    let tx_result = execute_migration_version(&mut conn, version, &statements).await;

    // ALWAYS re-enable FKs on the SAME connection.
    if needs_fk_disable && let Err(fk_err) = conn.execute("PRAGMA foreign_keys = ON").await {
        tracing::error!("failed to re-enable foreign_keys: {fk_err}");
        // Detach the poisoned connection so it is NOT returned to the pool.
        conn.detach();
        let msg = if let Err(ref mig_err) = tx_result {
            format!(
                "migration v{version} failed: {mig_err}; \
                 additionally, FK re-enable failed: {fk_err}"
            )
        } else {
            format!("enable foreign_keys failed after migration v{version}: {fk_err}")
        };
        return Err(SchedulerError::Database(msg));
    }

    tx_result?;
    Ok(())
}

/// Migration 0024 code-step: backfill markers and record the version.
async fn run_code_step_0024(pool: &DbPool) -> Result<(), SchedulerError> {
    let mut tx = pool
        .begin()
        .await
        .map_err(|e| SchedulerError::Database(format!("begin migration v24 tx failed: {e}")))?;

    // The wrapper owns the offline/lock deadline and re-checks it through the version-record
    // INSERT and COMMIT, so the whole 0024 transaction (not just the scan) stays bounded.
    let deadline = Some(crate::db::backfill::offline_deadline());
    let stats = crate::db::backfill::run_backfill_0024(pool, &mut tx, deadline).await?;

    crate::db::backfill::enforce_deadline(pool, &mut tx, deadline).await?;
    let insert_sql = pool.prepare_query(
        "INSERT INTO schema_migrations (version, applied_at) VALUES (?, CURRENT_TIMESTAMP)",
    );
    sqlx::query(&insert_sql)
        .bind(24_i32)
        .execute(&mut *tx)
        .await
        .map_err(|e| SchedulerError::Database(format!("record migration v24 failed: {e}")))?;

    crate::db::backfill::enforce_deadline(pool, &mut tx, deadline).await?;
    tx.commit()
        .await
        .map_err(|e| SchedulerError::Database(format!("commit migration v24 failed: {e}")))?;

    tracing::info!(
        "migration 0024 backfill: {} markers synthesized ({} sources seen, {} already covered, \
         {} skipped: no representable marker within cap)",
        stats.markers_synthesized,
        stats.sources_seen,
        stats.already_covered,
        stats.envelope_oversize_skipped,
    );
    Ok(())
}

/// Execute a single migration version's statements within a transaction.
///
/// Takes an acquired connection — caller is responsible for FK pragma handling
/// on the SAME connection (must be outside transaction).
async fn execute_migration_version(
    conn: &mut PoolConnection<sqlx::Any>,
    version: i32,
    statements: &[&str],
) -> Result<(), SchedulerError> {
    let mut tx: sqlx::Transaction<'_, sqlx::Any> = conn.begin().await.map_err(|e| {
        SchedulerError::Database(format!(
            "begin migration v{version} transaction failed: {e}"
        ))
    })?;
    for stmt in statements {
        tx.execute(*stmt).await.map_err(|e| {
            SchedulerError::Database(format!(
                "run migration failed: v{} statement: {e}\nStatement: {}",
                version,
                &stmt[..std::cmp::min(200, stmt.len())]
            ))
        })?;
    }
    tx.commit()
        .await
        .map_err(|e| SchedulerError::Database(format!("commit migration v{version} failed: {e}")))
}

/// Check if migrations have been applied (for validation/testing)
pub async fn get_current_version(pool: &DbPool) -> Result<i32, SchedulerError> {
    // Try to query the schema_migrations table
    let result = sqlx::query_scalar::<_, i32>("SELECT MAX(version) FROM schema_migrations")
        .fetch_optional(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("check migration version failed: {e}")))?;

    Ok(result.unwrap_or(0))
}

use sqlx::pool::PoolConnection;
use sqlx::{Acquire, Executor};

use super::DbPool;
use crate::error::SchedulerError;

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

/// Run all pending migrations on the database
pub async fn run(pool: &DbPool, database_url: &str) -> Result<(), SchedulerError> {
    // Detect database type from provided URL
    // PostgreSQL supports both postgres:// and postgresql:// schemes (RFC 3986)
    let is_postgres =
        database_url.starts_with("postgres:") || database_url.starts_with("postgresql:");

    // Get current migration version (0 if no migrations applied yet)
    let current_version = get_current_version(pool).await.unwrap_or(0);

    // List of all migrations in order
    // v2 uses a separate PG migration (ALTER RENAME COLUMN) vs SQLite (recreate-table)
    let migration_0002 = if is_postgres {
        MIGRATION_0002_PG
    } else {
        MIGRATION_0002
    };
    let migration_0003 = if is_postgres {
        MIGRATION_0003_PG
    } else {
        MIGRATION_0003
    };
    let migration_0004 = if is_postgres {
        MIGRATION_0004_PG
    } else {
        MIGRATION_0004
    };
    let migration_0005 = if is_postgres {
        MIGRATION_0005_PG
    } else {
        MIGRATION_0005
    };
    let migration_0006 = if is_postgres {
        MIGRATION_0006_PG
    } else {
        MIGRATION_0006
    };
    let migration_0007 = if is_postgres {
        MIGRATION_0007_PG
    } else {
        MIGRATION_0007
    };
    let migration_0008 = if is_postgres {
        MIGRATION_0008_PG
    } else {
        MIGRATION_0008
    };
    let migration_0009 = if is_postgres {
        MIGRATION_0009_PG
    } else {
        MIGRATION_0009
    };
    let migration_0010 = if is_postgres {
        MIGRATION_0010_PG
    } else {
        MIGRATION_0010
    };
    let migration_0011 = if is_postgres {
        MIGRATION_0011_PG
    } else {
        MIGRATION_0011
    };
    let migration_0012 = if is_postgres {
        MIGRATION_0012_PG
    } else {
        MIGRATION_0012
    };
    let migration_0013 = if is_postgres {
        MIGRATION_0013_PG
    } else {
        MIGRATION_0013
    };
    let migration_0014 = if is_postgres {
        MIGRATION_0014_PG
    } else {
        MIGRATION_0014
    };
    let migration_0015 = if is_postgres {
        MIGRATION_0015_PG
    } else {
        MIGRATION_0015
    };
    let migration_0016 = if is_postgres {
        MIGRATION_0016_PG
    } else {
        MIGRATION_0016
    };
    let migration_0017 = if is_postgres {
        MIGRATION_0017_PG
    } else {
        MIGRATION_0017
    };
    let migration_0018 = if is_postgres {
        MIGRATION_0018_PG
    } else {
        MIGRATION_0018
    };
    let migration_0019 = if is_postgres {
        MIGRATION_0019_PG
    } else {
        MIGRATION_0019
    };
    let migration_0020 = if is_postgres {
        MIGRATION_0020_PG
    } else {
        MIGRATION_0020
    };
    let migration_0021 = if is_postgres {
        MIGRATION_0021_PG
    } else {
        MIGRATION_0021
    };
    let migration_0022 = if is_postgres {
        MIGRATION_0022_PG
    } else {
        MIGRATION_0022
    };
    let migrations = vec![
        (MIGRATION_0001, 1),
        (migration_0002, 2),
        (migration_0003, 3),
        (migration_0004, 4),
        (migration_0005, 5),
        (migration_0006, 6),
        (migration_0007, 7),
        (migration_0008, 8),
        (migration_0009, 9),
        (migration_0010, 10),
        (migration_0011, 11),
        (migration_0012, 12),
        (migration_0013, 13),
        (migration_0014, 14),
        (migration_0015, 15),
        (migration_0016, 16),
        (migration_0017, 17),
        (migration_0018, 18),
        (migration_0019, 19),
        (migration_0020, 20),
        (migration_0021, 21),
        (migration_0022, 22),
    ];

    // Process each migration
    for (migration_sql, version) in migrations {
        // Skip already-applied migrations (makes runner idempotent)
        if version <= current_version {
            continue;
        }

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
                .map_err(|e| {
                    SchedulerError::Database(format!("disable foreign_keys failed: {e}"))
                })?;
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
    }

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

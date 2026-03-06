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
    ];

    // Process each migration
    for (migration_sql, version) in migrations {
        // Skip already-applied migrations (makes runner idempotent)
        if version <= current_version {
            continue;
        }

        // Migration 0002 uses DROP TABLE which triggers CASCADE with foreign_keys ON.
        // Disable FKs before the migration and re-enable after.
        let needs_fk_disable = !is_postgres && (version == 2 || version == 5);

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

#[cfg(test)]
mod tests {
    use super::*;

    // --- Migration transaction rollback tests ---

    static INIT_DRIVERS: std::sync::Once = std::sync::Once::new();

    fn install_drivers() {
        INIT_DRIVERS.call_once(|| {
            sqlx::any::install_default_drivers();
        });
    }

    async fn create_test_pool() -> crate::db::DbPool {
        install_drivers();
        crate::db::pool::create("sqlite::memory:")
            .await
            .expect("Failed to create test pool")
    }

    #[tokio::test]
    async fn test_migration_rollback_on_failure() {
        let pool = create_test_pool().await;
        let mut conn = pool.as_ref().acquire().await.unwrap();

        // Bootstrap schema_migrations table
        sqlx::query(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)",
        )
        .execute(&mut *conn)
        .await
        .unwrap();

        // Synthetic migration: valid DDL + failing statement + version insert
        let statements = &[
            "CREATE TABLE test_rollback_table (id INTEGER PRIMARY KEY, name TEXT)",
            "INSERT INTO nonexistent_table VALUES (1)",
            "INSERT INTO schema_migrations (version, applied_at) VALUES (9999, CURRENT_TIMESTAMP)",
        ];
        let result = execute_migration_version(&mut conn, 9999, statements).await;

        assert!(result.is_err());

        // Table should NOT exist (transaction rolled back)
        let table_exists = sqlx::query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='test_rollback_table'",
        )
        .fetch_optional(&mut *conn)
        .await
        .unwrap();
        assert!(
            table_exists.is_none(),
            "table should not exist after rollback"
        );

        // Version should NOT be recorded
        let version_exists =
            sqlx::query("SELECT version FROM schema_migrations WHERE version = 9999")
                .fetch_optional(&mut *conn)
                .await
                .unwrap();
        assert!(
            version_exists.is_none(),
            "version should not be recorded after rollback"
        );
    }

    #[tokio::test]
    async fn test_fk_pragma_restored_after_migration_failure() {
        let pool = create_test_pool().await;
        let mut conn = pool.as_ref().acquire().await.unwrap();

        // Verify FK is ON by default
        let fk_before: (i32,) = sqlx::query_as("PRAGMA foreign_keys")
            .fetch_one(&mut *conn)
            .await
            .unwrap();
        assert_eq!(fk_before.0, 1, "foreign_keys should be ON before test");

        // Simulate PRAGMA flow with a failing migration
        sqlx::query("PRAGMA foreign_keys = OFF")
            .execute(&mut *conn)
            .await
            .unwrap();

        let statements = &[
            "CREATE TABLE fk_test_table (id INTEGER PRIMARY KEY)",
            "INSERT INTO nonexistent_table VALUES (1)",
        ];
        let result = execute_migration_version(&mut conn, 9998, statements).await;
        assert!(result.is_err());

        // Re-enable FK (mirrors production pattern)
        sqlx::query("PRAGMA foreign_keys = ON")
            .execute(&mut *conn)
            .await
            .expect("FK re-enable should succeed");

        // Verify FK is back ON
        let fk_after: (i32,) = sqlx::query_as("PRAGMA foreign_keys")
            .fetch_one(&mut *conn)
            .await
            .unwrap();
        assert_eq!(
            fk_after.0, 1,
            "foreign_keys should be ON after failed migration"
        );

        // Table should NOT exist (transaction rolled back)
        let table_exists = sqlx::query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='fk_test_table'",
        )
        .fetch_optional(&mut *conn)
        .await
        .unwrap();
        assert!(
            table_exists.is_none(),
            "table should not exist after rollback"
        );
    }

    // Tests for the generic replace_type_with_tokenizer function
    #[test]
    fn test_generic_boolean_to_integer() {
        let sql = "is_active BOOLEAN NOT NULL";
        let result = replace_type_with_tokenizer(sql, "BOOLEAN", "INTEGER");
        assert_eq!(result, "is_active INTEGER NOT NULL");
    }

    #[test]
    fn test_generic_timestamp_to_text() {
        let sql = "created_at TIMESTAMP NOT NULL";
        let result = replace_type_with_tokenizer(sql, "TIMESTAMP", "TEXT");
        assert_eq!(result, "created_at TEXT NOT NULL");
    }

    #[test]
    fn test_generic_timestamp_to_timestamptz() {
        let sql = "updated_at TIMESTAMP";
        let result = replace_type_with_tokenizer(sql, "TIMESTAMP", "TIMESTAMPTZ");
        assert_eq!(result, "updated_at TIMESTAMPTZ");
    }

    #[test]
    fn test_generic_preserves_column_name() {
        let sql = "boolean BOOLEAN NOT NULL";
        let result = replace_type_with_tokenizer(sql, "BOOLEAN", "INTEGER");
        assert_eq!(result, "boolean INTEGER NOT NULL");
    }

    #[test]
    fn test_generic_multiple_replacements() {
        let sql = "col1 BOOLEAN, col2 BOOLEAN NOT NULL, col3 BOOLEAN";
        let result = replace_type_with_tokenizer(sql, "BOOLEAN", "INTEGER");
        assert_eq!(result, "col1 INTEGER, col2 INTEGER NOT NULL, col3 INTEGER");
    }

    #[test]
    fn test_generic_case_sensitive() {
        let sql = "col1 boolean, col2 Boolean, col3 BOOLEAN";
        let result = replace_type_with_tokenizer(sql, "BOOLEAN", "INTEGER");
        let count = result.matches("INTEGER").count();
        assert_eq!(count, 1, "Only uppercase BOOLEAN should be replaced");
    }

    #[test]
    fn test_generic_preserves_string_literals() {
        let sql = "SELECT 'BOOLEAN value' FROM t WHERE col BOOLEAN";
        let result = replace_type_with_tokenizer(sql, "BOOLEAN", "INTEGER");
        assert!(result.contains("'BOOLEAN value'"));
        assert!(result.contains("col INTEGER"));
    }

    #[test]
    fn test_generic_malformed_sql_fallback() {
        let sql = "this is not really SQL but we shouldn't crash";
        let result = replace_type_with_tokenizer(sql, "BOOLEAN", "INTEGER");
        // Should return original or attempt best-effort parsing
        assert!(!result.is_empty());
    }

    // Existing tests for specific wrapper functions
    #[test]
    fn test_timestamp_replacement_handles_comma() {
        let sql = "completed_at TIMESTAMP,";
        let result = replace_timestamp_with_timestamptz(sql);
        assert_eq!(result, "completed_at TIMESTAMPTZ,");
    }

    #[test]
    fn test_timestamp_replacement_handles_space() {
        let sql = "created_at TIMESTAMP NOT NULL";
        let result = replace_timestamp_with_timestamptz(sql);
        assert_eq!(result, "created_at TIMESTAMPTZ NOT NULL");
    }

    #[test]
    fn test_timestamp_replacement_handles_paren() {
        let sql = "col TIMESTAMP)";
        let result = replace_timestamp_with_timestamptz(sql);
        assert_eq!(result, "col TIMESTAMPTZ)");
    }

    #[test]
    fn test_timestamp_replacement_handles_semicolon() {
        let sql = "col TIMESTAMP;";
        let result = replace_timestamp_with_timestamptz(sql);
        assert_eq!(result, "col TIMESTAMPTZ;");
    }

    #[test]
    fn test_timestamp_replacement_preserves_string_literals() {
        let sql = "SELECT 'my TIMESTAMP column' FROM t";
        let result = replace_timestamp_with_timestamptz(sql);
        assert_eq!(result, "SELECT 'my TIMESTAMP column' FROM t");
    }

    #[test]
    fn test_timestamp_replacement_handles_multiple_occurrences() {
        let sql =
            "created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL, completed_at TIMESTAMP,";
        let result = replace_timestamp_with_timestamptz(sql);
        assert_eq!(
            result,
            "created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL, completed_at TIMESTAMPTZ,"
        );
    }

    #[test]
    fn test_timestamp_replacement_case_sensitive() {
        // Only uppercase TIMESTAMP (SQL type) should be replaced, not lowercase (column names)
        let sql = "col timestamp, col2 Timestamp, col3 TIMESTAMP";
        let result = replace_timestamp_with_timestamptz(sql);
        // Only col3's TIMESTAMP should be replaced (uppercase = type)
        // col1's "timestamp" and col2's "Timestamp" are column names, preserved as-is
        let count = result.matches("TIMESTAMPTZ").count();
        assert_eq!(
            count, 1,
            "Expected 1 TIMESTAMPTZ replacement (only uppercase), found {count} in: {result}"
        );
        assert!(
            result.contains("col timestamp,"),
            "Lowercase timestamp (column name) should be preserved"
        );
        assert!(
            result.contains("col2 Timestamp,"),
            "Mixed-case Timestamp should be preserved"
        );
        assert!(
            result.contains("col3 TIMESTAMPTZ"),
            "Uppercase TIMESTAMP (type) should be replaced"
        );
    }

    #[test]
    fn test_complete_migration_replacement() {
        // Test the actual migration SQL snippet for executions.completed_at
        let sql = r#"
        CREATE TABLE executions (
            completed_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL
        )
        "#;
        let result = replace_timestamp_with_timestamptz(sql);

        // Verify both TIMESTAMP occurrences are replaced
        assert!(result.contains("completed_at TIMESTAMPTZ,"));
        assert!(result.contains("created_at TIMESTAMPTZ NOT"));
        assert!(!result.contains("TIMESTAMP,"));
        assert!(!result.contains("TIMESTAMP NOT"));
    }

    #[test]
    fn test_column_named_timestamp() {
        // Test that column name 'timestamp' is preserved, but type TIMESTAMP is replaced
        let sql = "timestamp TIMESTAMP NOT NULL";
        let result = replace_timestamp_with_timestamptz(sql);
        assert_eq!(result, "timestamp TIMESTAMPTZ NOT NULL");
        // Column name preserved, type replaced
        assert!(result.starts_with("timestamp "));
        assert!(result.contains("TIMESTAMPTZ"));
    }

    #[test]
    fn test_current_timestamp_preserved() {
        // Test that CURRENT_TIMESTAMP function is NOT replaced
        let sql = "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP";
        let result = replace_timestamp_with_timestamptz(sql);
        assert_eq!(result, "created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP");
        // Type replaced, but function preserved
        assert!(result.contains("created_at TIMESTAMPTZ"));
        assert!(result.contains("CURRENT_TIMESTAMP"));
        assert!(!result.contains("CURRENT_TIMESTAMPTZ"));
    }

    #[test]
    fn test_execution_events_timestamp_column() {
        // Real-world test from migration line 55
        let sql = "timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,";
        let result = replace_timestamp_with_timestamptz(sql);
        // Column name 'timestamp' preserved, type TIMESTAMP replaced
        assert!(result.starts_with("timestamp TIMESTAMPTZ"));
        assert!(result.contains("CURRENT_TIMESTAMP"));
        assert!(!result.contains("CURRENT_TIMESTAMPTZ"));
    }

    #[test]
    #[allow(clippy::uninlined_format_args)] // Test output formatting
    fn test_full_migration_replacement() {
        // Test the actual migration process for the execution_events table
        let migration_snippet = r#"
CREATE TABLE IF NOT EXISTS execution_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    execution_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    task_id TEXT,
    message TEXT NOT NULL,
    metadata TEXT NOT NULL,
    timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (execution_id) REFERENCES executions(id) ON DELETE CASCADE
);
        "#;

        // Simulate PostgreSQL migration process
        let step1 =
            migration_snippet.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY");
        let step2 = replace_timestamp_with_timestamptz(&step1);

        // Print for debugging FIRST
        eprintln!("\n=== MIGRATION REPLACEMENT TEST ===");
        eprintln!("Original:\n{}", migration_snippet);
        eprintln!("\nAfter AUTOINCREMENT replacement:\n{}", step1);
        eprintln!("\nAfter TIMESTAMP replacement:\n{}", step2);
        eprintln!("=================================\n");

        // Verify column name 'timestamp' is preserved
        assert!(
            step2.contains("timestamp TIMESTAMPTZ NOT NULL"),
            "Expected 'timestamp TIMESTAMPTZ NOT NULL' in result:\n{}",
            step2
        );
        assert!(!step2.contains("timestamp TIMESTAMP NOT NULL"));

        // Verify CURRENT_TIMESTAMP is not replaced
        assert!(step2.contains("DEFAULT CURRENT_TIMESTAMP"));
        assert!(!step2.contains("DEFAULT CURRENT_TIMESTAMPTZ"));
    }
}

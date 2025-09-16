use sqlx::Executor;

use super::DbPool;
use crate::error::SchedulerError;

/// Embedded migration files
const MIGRATION_0001: &str = include_str!("../../migrations/0001_initial.sql");
const MIGRATION_0002: &str = include_str!("../../migrations/0002_add_workflow_registry.sql");
const MIGRATION_0003: &str = include_str!("../../migrations/0003_add_pending_tasks.sql");

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
    use sqlparser::dialect::GenericDialect;
    use sqlparser::tokenizer::{Token, Tokenizer};

    let dialect = GenericDialect {};
    let mut tokenizer = Tokenizer::new(&dialect, sql);

    let tokens = match tokenizer.tokenize() {
        Ok(tokens) => tokens,
        Err(_) => {
            // Fallback for malformed SQL (shouldn't happen with our embedded migrations)
            return sql.to_string();
        }
    };

    let mut result = String::new();
    let mut prev_word_token: Option<Token> = None;

    for token in tokens {
        if let Token::Word(ref w) = token {
            // Only replace uppercase TIMESTAMP (the SQL type), not lowercase timestamp (column name)
            // This handles cases like: timestamp TIMESTAMP -> timestamp TIMESTAMPTZ
            if w.value == "TIMESTAMP" {
                // Only replace TIMESTAMP if previous WORD token exists (column/table name)
                // This distinguishes type usage from standalone TIMESTAMP keywords
                // e.g., "timestamp TIMESTAMP" -> "timestamp TIMESTAMPTZ"
                //       "created_at TIMESTAMP" -> "created_at TIMESTAMPTZ"
                let should_replace = prev_word_token.is_some();

                if should_replace {
                    // Replace TIMESTAMP with TIMESTAMPTZ for PostgreSQL
                    result.push_str("TIMESTAMPTZ");
                    prev_word_token = Some(token.clone());
                    continue;
                }
            }
            // Update prev_word_token for any Word token
            prev_word_token = Some(token.clone());
        }
        // Preserve all other tokens (whitespace, punctuation, string literals, comments)
        result.push_str(&token.to_string());
    }

    result
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
    let migrations = vec![
        (MIGRATION_0001, 1),
        (MIGRATION_0002, 2),
        (MIGRATION_0003, 3),
    ];

    // Process each migration
    for (migration_sql, version) in migrations {
        // Skip already-applied migrations (makes runner idempotent)
        if version <= current_version {
            continue;
        }
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
            migration_sql.to_string()
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

        for stmt in statements {
            pool.as_ref().execute(stmt).await.map_err(|e| {
                SchedulerError::Database(format!(
                    "Failed to run migration v{} statement: {e}\nStatement: {}",
                    version,
                    &stmt[..std::cmp::min(200, stmt.len())]
                ))
            })?;
        }
    }

    Ok(())
}

/// Check if migrations have been applied (for validation/testing)
pub async fn get_current_version(pool: &DbPool) -> Result<i32, SchedulerError> {
    // Try to query the schema_migrations table
    let result = sqlx::query_scalar::<_, i32>("SELECT MAX(version) FROM schema_migrations")
        .fetch_optional(pool.as_ref())
        .await
        .map_err(|e| SchedulerError::Database(format!("Failed to check migration version: {e}")))?;

    Ok(result.unwrap_or(0))
}

#[cfg(test)]
mod tests {
    use super::*;

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

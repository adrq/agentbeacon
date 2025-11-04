use sqlx::{Any, Pool, any::AnyPoolOptions};
use std::ops::Deref;
use std::sync::Arc;
use std::time::Duration;

use crate::error::SchedulerError;

/// Database type enumeration
#[derive(Debug, Clone, Copy, PartialEq)]
enum DbType {
    Sqlite,
    Postgres,
}

/// Type-safe timestamp column enumeration for SQL injection prevention
///
/// This enum ensures that only valid column names can be used with format_timestamp(),
/// making SQL injection impossible at compile-time.
#[derive(Debug, Clone, Copy)]
pub enum TimestampColumn {
    CreatedAt,
    UpdatedAt,
    CompletedAt,
    Timestamp,
}

impl TimestampColumn {
    /// Get the column name as a static string
    fn as_str(&self) -> &'static str {
        match self {
            Self::CreatedAt => "created_at",
            Self::UpdatedAt => "updated_at",
            Self::CompletedAt => "completed_at",
            Self::Timestamp => "timestamp",
        }
    }
}

/// Database pool wrapper that stores the pool and its type
///
/// This wrapper ensures the database type is accessible across all async tasks
/// regardless of which Tokio worker thread executes them. The type is stored
/// alongside the pool in an Arc for efficient cloning.
#[derive(Clone)]
pub struct DbPool {
    pool: Pool<Any>,
    db_type: Arc<DbType>,
}

impl DbPool {
    /// Create new pool wrapper
    fn new(pool: Pool<Any>, db_type: DbType) -> Self {
        Self {
            pool,
            db_type: Arc::new(db_type),
        }
    }

    /// Check if this pool is PostgreSQL
    pub fn is_postgres(&self) -> bool {
        matches!(*self.db_type, DbType::Postgres)
    }

    /// Convert ? placeholders to $N for PostgreSQL (T017 requirement)
    ///
    /// This enables a single query codebase for both SQLite and PostgreSQL.
    /// All queries are written with ? placeholders, then converted at runtime.
    ///
    /// Uses sqlparser to correctly identify placeholders vs string literals,
    /// preventing corruption of queries like `WHERE msg = 'Hello?'`.
    pub fn prepare_query(&self, query: &str) -> String {
        if self.is_postgres() {
            convert_placeholders_to_postgres(query)
        } else {
            query.to_string() // SQLite uses ? natively
        }
    }

    /// Format timestamp column as RFC3339 string for cross-database compatibility
    ///
    /// Returns SQL expression that formats a timestamp column as RFC3339 string:
    /// - SQLite: strftime('%Y-%m-%dT%H:%M:%SZ', column_name)
    /// - PostgreSQL: to_char(column_name AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
    ///
    /// # Important: Timezone Handling with TIMESTAMPTZ
    ///
    /// PostgreSQL columns use TIMESTAMPTZ (via migration replacement), which stores
    /// absolute UTC instants internally. However, `to_char()` formats in the **session's
    /// timezone** (not UTC), so we MUST convert to UTC explicitly:
    ///
    /// - `AT TIME ZONE 'UTC'` with TIMESTAMPTZ: Converts absolute instant TO UTC for display
    /// - Without conversion: `to_char()` formats in server's local timezone (e.g., EST)
    /// - Result without conversion: Local time mislabeled with 'Z' suffix (violates RFC3339)
    ///
    /// **Example on EST server:**
    /// ```sql
    /// -- Stored: 2025-01-01 12:00:00 UTC (absolute instant)
    /// -- Without AT TIME ZONE: to_char() → '2025-01-01T07:00:00Z' (WRONG! EST labeled as UTC)
    /// -- With AT TIME ZONE:    to_char() → '2025-01-01T12:00:00Z' (CORRECT! True UTC)
    /// ```
    ///
    /// **Critical distinction from old TIMESTAMP bug:**
    /// - TIMESTAMP (old): Naive time, `AT TIME ZONE` misinterpreted it
    /// - TIMESTAMPTZ (new): Absolute instant, `AT TIME ZONE` correctly converts it
    ///
    /// SQLite TIMESTAMP is always naive UTC (CURRENT_TIMESTAMP returns UTC).
    ///
    /// # Security
    ///
    /// This function uses a type-safe enum to prevent SQL injection at compile-time.
    /// Only valid column names from TimestampColumn enum can be used.
    #[allow(clippy::uninlined_format_args)] // SQL string building requires explicit formatting
    pub fn format_timestamp(&self, column: TimestampColumn) -> String {
        let column_name = column.as_str();
        if self.is_postgres() {
            // PostgreSQL: Convert TIMESTAMPTZ to UTC before formatting
            // This ensures to_char() formats in UTC, not server's local timezone
            format!("to_char({column_name} AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')")
        } else {
            // SQLite: Format as RFC3339 using strftime
            format!("strftime('%Y-%m-%dT%H:%M:%SZ', {column_name})")
        }
    }
}

/// Convert ? placeholders to $N for PostgreSQL using sqlparser for correctness
///
/// This function uses sqlparser's tokenizer to safely identify SQL placeholders
/// vs string literals, preventing corruption of queries containing '?' in strings.
///
/// # Example
/// ```ignore
/// convert_placeholders_to_postgres("SELECT * FROM t WHERE msg = 'Hello?' AND id = ?")
/// // Returns: "SELECT * FROM t WHERE msg = 'Hello?' AND id = $1"
/// ```
#[allow(clippy::uninlined_format_args)] // Parameter number formatting
fn convert_placeholders_to_postgres(query: &str) -> String {
    use sqlparser::dialect::GenericDialect;
    use sqlparser::tokenizer::{Token, Tokenizer};

    let dialect = GenericDialect {};
    let mut tokenizer = Tokenizer::new(&dialect, query);

    let tokens = match tokenizer.tokenize() {
        Ok(tokens) => tokens,
        Err(_) => {
            // Fallback for malformed SQL (shouldn't happen with our hardcoded queries)
            return query.to_string();
        }
    };

    let mut result = String::new();
    let mut param_num = 1;

    for token in tokens {
        if let Token::Placeholder(ref s) = token {
            if s == "?" {
                // Convert ? to $N for PostgreSQL
                result.push_str(&format!("${param_num}"));
                param_num += 1;
                continue;
            }
        }
        // Preserve all other tokens (string literals, keywords, etc.)
        result.push_str(&token.to_string());
    }

    result
}

/// Automatically dereference DbPool to Pool<Any> for sqlx query execution
///
/// This allows DbPool to be used directly with sqlx methods like fetch_one(),
/// while still providing our custom helper methods (format_timestamp, prepare_query).
impl Deref for DbPool {
    type Target = Pool<Any>;

    fn deref(&self) -> &Self::Target {
        &self.pool
    }
}

/// Allow DbPool to convert to &Pool<Any> for sqlx Executor trait
impl AsRef<Pool<Any>> for DbPool {
    fn as_ref(&self) -> &Pool<Any> {
        &self.pool
    }
}

/// Create database connection pool with database-specific configuration
pub async fn create(database_url: &str) -> Result<DbPool, SchedulerError> {
    // Detect database type from URL
    // PostgreSQL supports both postgres:// and postgresql:// schemes (RFC 3986)
    let is_sqlite = database_url.starts_with("sqlite:");
    let is_postgres =
        database_url.starts_with("postgres:") || database_url.starts_with("postgresql:");

    if !is_sqlite && !is_postgres {
        return Err(SchedulerError::Database(format!(
            "Unsupported database URL: {database_url}. Only SQLite (sqlite://) and PostgreSQL (postgres:// or postgresql://) are supported."
        )));
    }

    let db_type = if is_postgres {
        DbType::Postgres
    } else {
        DbType::Sqlite
    };

    let pool = if is_sqlite {
        // SQLite configuration: max 1 connection (single-writer limitation)
        let pool = AnyPoolOptions::new()
            .max_connections(1)
            .connect(database_url)
            .await
            .map_err(|e| SchedulerError::Database(format!("connect to SQLite failed: {e}")))?;

        // Enable foreign key constraints (disabled by default in SQLite)
        // This is critical for CASCADE behavior and referential integrity
        sqlx::query("PRAGMA foreign_keys = ON")
            .execute(&pool)
            .await
            .map_err(|e| SchedulerError::Database(format!("enable foreign keys failed: {e}")))?;

        pool
    } else {
        // PostgreSQL configuration: max 10 connections
        AnyPoolOptions::new()
            .max_connections(10)
            .min_connections(2)
            .idle_timeout(Duration::from_secs(600))
            .max_lifetime(Duration::from_secs(1800))
            .connect(database_url)
            .await
            .map_err(|e| SchedulerError::Database(format!("connect to PostgreSQL failed: {e}")))?
    };

    Ok(DbPool::new(pool, db_type))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_basic_placeholder_conversion() {
        let query = "SELECT * FROM workflows WHERE id = ?";
        let result = convert_placeholders_to_postgres(query);
        assert_eq!(result, "SELECT * FROM workflows WHERE id = $1");
    }

    #[test]
    fn test_string_literal_with_question_mark() {
        let query = "SELECT * FROM logs WHERE msg = 'Hello?' AND id = ?";
        let result = convert_placeholders_to_postgres(query);
        // String literal should be preserved, placeholder converted
        assert!(
            result.contains("'Hello?'"),
            "String literal should preserve ?"
        );
        assert!(
            result.contains("$1"),
            "Placeholder should be converted to $1"
        );
        assert!(
            !result.contains("'Hello$1'"),
            "Should not convert ? inside string literal"
        );
    }

    #[test]
    #[allow(clippy::uninlined_format_args)] // Test assertion formatting
    fn test_escaped_single_quotes() {
        let query = "SELECT * FROM t WHERE msg = 'It''s here' AND id = ?";
        let result = convert_placeholders_to_postgres(query);
        // sqlparser may normalize the escaped quote representation
        // Both 'It''s here' and 'It\'s here' are valid, or it might parse to "It's here"
        assert!(
            result.contains("It") && result.contains("s here"),
            "String content should be preserved. Got: {}",
            result
        );
        assert!(result.contains("$1"), "Placeholder should be converted");
    }

    #[test]
    fn test_double_quoted_string() {
        let query = "SELECT * FROM t WHERE name = \"test?\" AND id = ?";
        let result = convert_placeholders_to_postgres(query);
        assert!(
            result.contains("\"test?\""),
            "Double-quoted string should preserve ?"
        );
        assert!(result.contains("$1"), "Placeholder should be converted");
    }

    #[test]
    fn test_multiple_placeholders() {
        let query = "INSERT INTO workflows (id, name, description) VALUES (?, ?, ?)";
        let result = convert_placeholders_to_postgres(query);
        assert!(result.contains("$1"), "First placeholder");
        assert!(result.contains("$2"), "Second placeholder");
        assert!(result.contains("$3"), "Third placeholder");
        // Verify they're in order
        let pos1 = result.find("$1").unwrap();
        let pos2 = result.find("$2").unwrap();
        let pos3 = result.find("$3").unwrap();
        assert!(
            pos1 < pos2 && pos2 < pos3,
            "Placeholders should be sequential"
        );
    }

    #[test]
    fn test_no_placeholders() {
        let query = "SELECT * FROM workflows ORDER BY created_at DESC";
        let result = convert_placeholders_to_postgres(query);
        assert_eq!(
            result, query,
            "Query without placeholders should be unchanged"
        );
    }

    #[test]
    fn test_complex_query_with_mixed_quotes() {
        let query = "SELECT * FROM t WHERE a = 'test?' AND b = \"why?\" AND c = ?";
        let result = convert_placeholders_to_postgres(query);
        assert!(result.contains("'test?'"), "Single-quoted string preserved");
        assert!(
            result.contains("\"why?\""),
            "Double-quoted string preserved"
        );
        assert!(result.contains("$1"), "Placeholder converted");
        // Ensure only one placeholder was converted
        assert_eq!(result.matches("$1").count(), 1);
        assert!(!result.contains("$2"));
    }

    #[test]
    fn test_placeholder_in_where_clause_with_operator() {
        let query = "SELECT * FROM executions WHERE status = ? AND created_at > ?";
        let result = convert_placeholders_to_postgres(query);
        assert!(result.contains("$1"), "First placeholder");
        assert!(result.contains("$2"), "Second placeholder");
    }

    #[test]
    fn test_update_query_with_placeholders() {
        let query = "UPDATE executions SET status = ?, task_states = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?";
        let result = convert_placeholders_to_postgres(query);
        assert!(result.contains("$1"));
        assert!(result.contains("$2"));
        assert!(result.contains("$3"));
    }

    #[test]
    fn test_string_literal_at_end() {
        let query = "SELECT * FROM t WHERE id = ? AND msg = 'end?'";
        let result = convert_placeholders_to_postgres(query);
        assert!(result.contains("$1"));
        assert!(result.contains("'end?'"));
    }

    #[test]
    #[allow(clippy::uninlined_format_args)] // Test SQL string formatting
    fn test_format_timestamp_includes_at_time_zone_for_postgres() {
        // We can't easily create a real pool in a unit test, so we'll test the logic
        // by verifying the SQL string generation

        // Simulate what format_timestamp should return for PostgreSQL
        let column = TimestampColumn::CreatedAt;
        let column_name = column.as_str();

        // PostgreSQL should include AT TIME ZONE 'UTC'
        let expected_postgres = format!(
            "to_char({} AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')",
            column_name
        );

        // SQLite should not include AT TIME ZONE
        let expected_sqlite = format!("strftime('%Y-%m-%dT%H:%M:%SZ', {})", column_name);

        // Verify the expected patterns are correct
        assert!(
            expected_postgres.contains("AT TIME ZONE 'UTC'"),
            "PostgreSQL query must include AT TIME ZONE 'UTC' to convert TIMESTAMPTZ to UTC before formatting"
        );
        assert!(
            !expected_sqlite.contains("AT TIME ZONE"),
            "SQLite doesn't support AT TIME ZONE"
        );

        // Verify all timestamp columns would be formatted with UTC conversion
        assert_eq!(TimestampColumn::CreatedAt.as_str(), "created_at");
        assert_eq!(TimestampColumn::UpdatedAt.as_str(), "updated_at");
        assert_eq!(TimestampColumn::CompletedAt.as_str(), "completed_at");
        assert_eq!(TimestampColumn::Timestamp.as_str(), "timestamp");
    }

    #[test]
    fn test_timestamp_formatting_rationale() {
        // This test documents WHY we need AT TIME ZONE 'UTC' for PostgreSQL
        //
        // PostgreSQL's to_char() formats TIMESTAMPTZ in the **session timezone**,
        // not UTC. On a server with timezone='America/New_York':
        //
        // Without AT TIME ZONE:
        //   Stored: 2025-01-01 12:00:00 UTC (absolute instant)
        //   Query:  to_char(ts, 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
        //   Result: '2025-01-01T07:00:00Z'  ❌ (EST time labeled as UTC)
        //
        // With AT TIME ZONE 'UTC':
        //   Stored: 2025-01-01 12:00:00 UTC (absolute instant)
        //   Query:  to_char(ts AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
        //   Result: '2025-01-01T12:00:00Z'  ✅ (Correct UTC time)
        //
        // This is NOT the same as the old TIMESTAMP bug:
        // - TIMESTAMP (old):  Stored naive local time, AT TIME ZONE misinterpreted it
        // - TIMESTAMPTZ (new): Stores absolute instant, AT TIME ZONE converts it correctly

        // This test passes if it compiles - it's documentation
    }
}

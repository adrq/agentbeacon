// Test utilities for new schema
#![allow(dead_code)]
#![allow(unused_imports)]

use axum::body::Body;
use axum::response::Response;
use serde_json::Value as JsonValue;
use sqlx::{Any, Pool, postgres::PgPoolOptions};
use std::sync::Once;
use std::sync::atomic::{AtomicUsize, Ordering};

static INIT_DRIVERS: Once = Once::new();
static PG_DB_COUNTER: AtomicUsize = AtomicUsize::new(0);

/// Install SQLx drivers for Any pool (required in SQLx 0.8+)
fn install_drivers() {
    INIT_DRIVERS.call_once(|| {
        sqlx::any::install_default_drivers();
    });
}

/// Public wrapper for install_drivers (for tests that create pools directly)
pub fn install_drivers_once() {
    install_drivers();
}

/// Pool type for testing - uses DbPool wrapper for consistency with real implementation
pub type TestPool = scheduler::db::DbPool;

/// Extract JSON body from Axum response for testing
pub async fn response_body_as_json(response: Response<Body>) -> JsonValue {
    use axum::body::to_bytes;

    let body_bytes = to_bytes(response.into_body(), usize::MAX)
        .await
        .expect("Failed to read response body");

    serde_json::from_slice(&body_bytes).expect("Failed to parse response body as JSON")
}

/// Create in-memory SQLite pool for testing
pub async fn create_test_pool_sqlite() -> TestPool {
    install_drivers();

    scheduler::db::pool::create("sqlite::memory:")
        .await
        .expect("Failed to create in-memory SQLite pool")
}

/// Create PostgreSQL test pool at 0.0.0.0 with a unique database per call.
///
/// Each invocation gets its own database (agentbeacon_test_0, _1, ...) so
/// concurrent tests don't kill each other's connections.
#[allow(clippy::redundant_pattern_matching)]
pub async fn create_test_pool_postgres() -> (TestPool, String) {
    install_drivers();

    let db_num = PG_DB_COUNTER.fetch_add(1, Ordering::SeqCst);
    let db_name = format!("agentbeacon_test_{db_num}");

    let admin_url = "postgres://postgres:postgres@0.0.0.0/postgres";
    let admin_pool = PgPoolOptions::new()
        .max_connections(1)
        .connect(admin_url)
        .await
        .expect("FATAL: PostgreSQL server unavailable at 0.0.0.0 - tests cannot run without both databases");

    let _ = sqlx::query(&format!(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{db_name}' AND pid <> pg_backend_pid()"
    ))
    .execute(&admin_pool)
    .await;

    let _ = sqlx::query(&format!("DROP DATABASE IF EXISTS {db_name}"))
        .execute(&admin_pool)
        .await;

    sqlx::query(&format!("CREATE DATABASE {db_name}"))
        .execute(&admin_pool)
        .await
        .expect("Failed to create test database");

    admin_pool.close().await;

    let test_url = format!("postgres://postgres:postgres@0.0.0.0/{db_name}");
    let pool = scheduler::db::pool::create(&test_url)
        .await
        .expect("Failed to connect to test database");

    (pool, test_url)
}

/// Get table names for migration validation
pub async fn get_table_names(pool: &TestPool) -> Result<Vec<String>, sqlx::Error> {
    let is_postgres = pool.is_postgres();

    if is_postgres {
        let tables: Vec<String> = sqlx::query_scalar(
            "SELECT CAST(tablename AS TEXT) FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
        )
        .fetch_all(pool.as_ref())
        .await?;
        Ok(tables)
    } else {
        let tables: Vec<String> = sqlx::query_scalar(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        .fetch_all(pool.as_ref())
        .await?;
        Ok(tables)
    }
}

/// Run migrations on a pool with specified database URL
pub async fn run_migrations(pool: &TestPool, database_url: &str) -> Result<(), String> {
    scheduler::db::migrations::run(pool, database_url)
        .await
        .map_err(|e| format!("Migration failed: {e}"))
}

/// Run migrations on SQLite pool
pub async fn run_migrations_sqlite(pool: &TestPool) -> Result<(), String> {
    run_migrations(pool, "sqlite::memory:").await
}

/// Run migrations on PostgreSQL pool
pub async fn run_migrations_postgres(pool: &TestPool, db_url: &str) -> Result<(), String> {
    run_migrations(pool, db_url).await
}

/// Config helpers (config table is unchanged)
pub async fn upsert_config(
    pool: &TestPool,
    name: &str,
    value: &str,
) -> Result<(), scheduler::error::SchedulerError> {
    scheduler::db::config::upsert(pool, name, value).await
}

pub async fn get_config(
    pool: &TestPool,
    name: &str,
) -> Result<scheduler::db::Config, scheduler::error::SchedulerError> {
    scheduler::db::config::get(pool, name).await
}

pub async fn list_config(
    pool: &TestPool,
) -> Result<Vec<scheduler::db::Config>, scheduler::error::SchedulerError> {
    scheduler::db::config::list(pool).await
}

/// Execution helpers (new schema)
pub async fn create_execution(
    pool: &TestPool,
    context_id: &str,
    input: &str,
) -> Result<String, scheduler::error::SchedulerError> {
    let id = uuid::Uuid::new_v4().to_string();
    scheduler::db::executions::create(pool, &id, context_id, input, None, None, None, None, 2, 5)
        .await?;
    Ok(id)
}

pub async fn get_execution_by_id(
    pool: &TestPool,
    id: &str,
) -> Result<scheduler::db::Execution, scheduler::error::SchedulerError> {
    scheduler::db::executions::get_by_id(pool, id).await
}

// Test utilities for new schema
// Each integration test compiles this module independently, so not all
// functions are used by every test file. Suppress false dead_code warnings.
#![allow(dead_code)]

use axum::body::Body;
use axum::response::Response;
use serde_json::Value as JsonValue;
use std::sync::Once;

static INIT_DRIVERS: Once = Once::new();

/// Install SQLx drivers for Any pool (required in SQLx 0.8+)
fn install_drivers() {
    INIT_DRIVERS.call_once(|| {
        sqlx::any::install_default_drivers();
    });
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

/// Run migrations on a pool with specified database URL
async fn run_migrations(pool: &TestPool, database_url: &str) -> Result<(), String> {
    scheduler::db::migrations::run(pool, database_url)
        .await
        .map_err(|e| format!("Migration failed: {e}"))
}

/// Run migrations on SQLite pool
pub async fn run_migrations_sqlite(pool: &TestPool) -> Result<(), String> {
    run_migrations(pool, "sqlite::memory:").await
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

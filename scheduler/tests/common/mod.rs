// Test utilities - intentionally unused until test coverage expands
#![allow(dead_code)]
#![allow(unused_imports)]

use axum::body::Body;
use axum::response::Response;
use serde_json::Value as JsonValue;
use sqlx::{Any, Pool, postgres::PgPoolOptions};
use std::sync::{Mutex, Once};
use uuid::Uuid;

static INIT_DRIVERS: Once = Once::new();
static PG_DB_SETUP: Mutex<()> = Mutex::new(());

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

/// Create schema validator for testing
pub fn create_test_validator()
-> Result<scheduler::validation::SchemaValidator, scheduler::error::SchedulerError> {
    scheduler::validation::SchemaValidator::new()
}

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

    // Use pool::create() to ensure database type is set correctly
    scheduler::db::pool::create("sqlite::memory:")
        .await
        .expect("Failed to create in-memory SQLite pool")
}

/// Create PostgreSQL test pool at 0.0.0.0, drops and recreates agentmaestro_test database
#[allow(clippy::await_holding_lock)] // Mutex guards database setup across tests
#[allow(clippy::redundant_pattern_matching)] // Pattern match preserves error handling clarity
#[allow(clippy::let_and_return)] // Lock must be held until return
pub async fn create_test_pool_postgres() -> TestPool {
    install_drivers();

    // Synchronize database creation across parallel tests
    let _guard = PG_DB_SETUP.lock().unwrap();

    // Connect to postgres database to manage test database
    let admin_url = "postgres://postgres:postgres@0.0.0.0/postgres";
    let admin_pool = PgPoolOptions::new()
        .max_connections(1)
        .connect(admin_url)
        .await
        .expect("FATAL: PostgreSQL server unavailable at 0.0.0.0 - tests cannot run without both databases");

    // Terminate active connections to test database (retry up to 3 times)
    for attempt in 1..=3 {
        let _ = sqlx::query(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = 'agentmaestro_test' AND pid <> pg_backend_pid()"
        )
        .execute(&admin_pool)
        .await;

        // Wait for connections to terminate
        tokio::time::sleep(tokio::time::Duration::from_millis(200 * attempt)).await;

        // Try to drop database
        if let Ok(_) = sqlx::query("DROP DATABASE IF EXISTS agentmaestro_test")
            .execute(&admin_pool)
            .await
        {
            break;
        }
    }

    // Create test database
    sqlx::query("CREATE DATABASE agentmaestro_test")
        .execute(&admin_pool)
        .await
        .expect("Failed to create test database");

    admin_pool.close().await;

    // Connect to test database using pool::create() to set database type correctly
    let test_url = "postgres://postgres:postgres@0.0.0.0/agentmaestro_test";
    let pool = scheduler::db::pool::create(test_url)
        .await
        .expect("Failed to connect to test database");

    // Note: Lock is held until this function returns, ensuring sequential database setup
    pool
}

/// Create test workflow struct for testing
pub fn create_test_workflow_struct() -> TestWorkflow {
    use chrono::Utc;

    TestWorkflow {
        id: Uuid::new_v4(),
        name: "test-workflow".to_string(),
        description: Some("Test description".to_string()),
        yaml_content: "name: test\ntasks: []".to_string(),
        created_at: Utc::now(),
        updated_at: Utc::now(),
    }
}

/// Get table names for migration validation
pub async fn get_table_names(pool: &TestPool) -> Result<Vec<String>, sqlx::Error> {
    // Detect database type from pool
    let is_postgres = pool.is_postgres();

    if is_postgres {
        // PostgreSQL query - use CAST to TEXT to work with Any driver
        let tables: Vec<String> = sqlx::query_scalar(
            "SELECT CAST(tablename AS TEXT) FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
        )
        .fetch_all(pool.as_ref())
        .await?;
        Ok(tables)
    } else {
        // SQLite query
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
pub async fn run_migrations_postgres(pool: &TestPool) -> Result<(), String> {
    run_migrations(
        pool,
        "postgres://postgres:postgres@0.0.0.0/agentmaestro_test",
    )
    .await
}

/// Test workflow struct (placeholder - will be replaced with actual Workflow struct)
#[derive(Debug, Clone)]
pub struct TestWorkflow {
    pub id: Uuid,
    pub name: String,
    pub description: Option<String>,
    pub yaml_content: String,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

/// Database operation helpers - now using real implementations
pub async fn create_workflow(
    pool: &TestPool,
    workflow: &TestWorkflow,
) -> Result<(), scheduler::error::SchedulerError> {
    // Convert TestWorkflow to real Workflow and create
    let wf = scheduler::db::Workflow {
        id: workflow.id,
        name: workflow.name.clone(),
        description: workflow.description.clone(),
        yaml_content: workflow.yaml_content.clone(),
        created_at: workflow.created_at,
        updated_at: workflow.updated_at,
    };

    scheduler::db::workflows::create(pool, &wf).await
}

pub async fn get_workflow_by_id(
    pool: &TestPool,
    id: &Uuid,
) -> Result<TestWorkflow, scheduler::error::SchedulerError> {
    let wf = scheduler::db::workflows::get_by_id(pool, id).await?;

    Ok(TestWorkflow {
        id: wf.id,
        name: wf.name,
        description: wf.description,
        yaml_content: wf.yaml_content,
        created_at: wf.created_at,
        updated_at: wf.updated_at,
    })
}

pub async fn update_workflow(
    pool: &TestPool,
    id: &Uuid,
    yaml_content: &str,
) -> Result<(), scheduler::error::SchedulerError> {
    // Description is now parsed within workflows::update()
    scheduler::db::workflows::update(pool, id, yaml_content).await
}

pub async fn list_workflows(
    pool: &TestPool,
) -> Result<Vec<TestWorkflow>, scheduler::error::SchedulerError> {
    let workflows = scheduler::db::workflows::list(pool, None).await?;

    Ok(workflows
        .into_iter()
        .map(|wf| TestWorkflow {
            id: wf.id,
            name: wf.name,
            description: wf.description,
            yaml_content: wf.yaml_content,
            created_at: wf.created_at,
            updated_at: wf.updated_at,
        })
        .collect())
}

pub async fn upsert_workflow(
    pool: &TestPool,
    workflow: &TestWorkflow,
) -> Result<(), scheduler::error::SchedulerError> {
    let db_workflow = scheduler::db::Workflow {
        id: workflow.id,
        name: workflow.name.clone(),
        description: workflow.description.clone(),
        yaml_content: workflow.yaml_content.clone(),
        created_at: workflow.created_at,
        updated_at: workflow.updated_at,
    };
    scheduler::db::workflows::upsert(pool, &db_workflow).await
}

pub async fn get_workflow_by_name(
    pool: &TestPool,
    name: &str,
) -> Result<TestWorkflow, scheduler::error::SchedulerError> {
    let wf = scheduler::db::workflows::get_by_name(pool, name).await?;

    Ok(TestWorkflow {
        id: wf.id,
        name: wf.name,
        description: wf.description,
        yaml_content: wf.yaml_content,
        created_at: wf.created_at,
        updated_at: wf.updated_at,
    })
}

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
) -> Result<TestConfig, scheduler::error::SchedulerError> {
    let cfg = scheduler::db::config::get(pool, name).await?;

    Ok(TestConfig {
        name: cfg.name,
        value: cfg.value,
        created_at: cfg.created_at,
        updated_at: cfg.updated_at,
    })
}

pub async fn list_config(
    pool: &TestPool,
) -> Result<Vec<TestConfig>, scheduler::error::SchedulerError> {
    let configs = scheduler::db::config::list(pool).await?;

    Ok(configs
        .into_iter()
        .map(|cfg| TestConfig {
            name: cfg.name,
            value: cfg.value,
            created_at: cfg.created_at,
            updated_at: cfg.updated_at,
        })
        .collect())
}

pub async fn create_execution(
    pool: &TestPool,
    workflow_id: &Uuid,
    task_states: serde_json::Value,
) -> Result<Uuid, scheduler::error::SchedulerError> {
    scheduler::db::executions::create(pool, workflow_id, task_states, None, None).await
}

pub async fn get_execution_by_id(
    pool: &TestPool,
    id: &Uuid,
) -> Result<TestExecution, scheduler::error::SchedulerError> {
    let exec = scheduler::db::executions::get_by_id(pool, id).await?;

    Ok(TestExecution {
        id: exec.id,
        workflow_id: exec.workflow_id,
        status: exec.status,
        task_states: exec.task_states,
        created_at: exec.created_at,
        updated_at: exec.updated_at,
        completed_at: exec.completed_at,
    })
}

pub async fn create_test_workflow(pool: &TestPool) -> Uuid {
    let workflow = create_test_workflow_struct();
    create_workflow(pool, &workflow).await.unwrap();
    workflow.id
}

/// Test config struct (placeholder)
#[derive(Debug, Clone)]
pub struct TestConfig {
    pub name: String,
    pub value: String,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
}

/// Test execution struct (placeholder)
#[derive(Debug, Clone)]
pub struct TestExecution {
    pub id: Uuid,
    pub workflow_id: Uuid,
    pub status: String,
    pub task_states: serde_json::Value,
    pub created_at: chrono::DateTime<chrono::Utc>,
    pub updated_at: chrono::DateTime<chrono::Utc>,
    pub completed_at: Option<chrono::DateTime<chrono::Utc>>,
}

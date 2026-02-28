mod common;

use common::*;

/// Test: Config UPSERT Logic (SQLite - INSERT OR REPLACE)
#[tokio::test]
async fn test_config_upsert_sqlite() {
    let pool = create_test_pool_sqlite().await;
    run_migrations_sqlite(&pool)
        .await
        .expect("Failed to run migrations");

    upsert_config(&pool, "test_key", "initial_value")
        .await
        .expect("Failed to create config entry");

    let config = get_config(&pool, "test_key")
        .await
        .expect("Failed to retrieve config entry");
    assert_eq!(config.value, "initial_value");

    upsert_config(&pool, "test_key", "updated_value")
        .await
        .expect("Failed to update config entry");

    let config_updated = get_config(&pool, "test_key")
        .await
        .expect("Failed to retrieve updated config entry");
    assert_eq!(config_updated.value, "updated_value");

    let all_configs = list_config(&pool)
        .await
        .expect("Failed to list config entries");
    // Migration seeds max_depth + max_width, plus 1 test_key = 3 total
    let test_entries: Vec<_> = all_configs
        .iter()
        .filter(|c| c.name == "test_key")
        .collect();
    assert_eq!(
        test_entries.len(),
        1,
        "SQLite: Should have exactly 1 test_key config entry, found {}",
        test_entries.len()
    );

    pool.close().await;
}

/// Test: Config UPSERT Logic (PostgreSQL - ON CONFLICT)
#[tokio::test]
async fn test_config_upsert_postgres() {
    let (pool, db_url) = create_test_pool_postgres().await;
    run_migrations_postgres(&pool, &db_url)
        .await
        .expect("Failed to run migrations");

    upsert_config(&pool, "test_key_pg", "initial_value")
        .await
        .expect("Failed to create config entry");

    let config = get_config(&pool, "test_key_pg")
        .await
        .expect("Failed to retrieve config entry");
    assert_eq!(config.value, "initial_value");

    upsert_config(&pool, "test_key_pg", "updated_value")
        .await
        .expect("Failed to update config entry");

    let config_updated = get_config(&pool, "test_key_pg")
        .await
        .expect("Failed to retrieve updated config entry");
    assert_eq!(config_updated.value, "updated_value");

    let all_configs = list_config(&pool)
        .await
        .expect("Failed to list config entries");
    // Migration seeds max_depth + max_width, plus 1 test_key_pg = 3 total
    let test_entries: Vec<_> = all_configs
        .iter()
        .filter(|c| c.name == "test_key_pg")
        .collect();
    assert_eq!(
        test_entries.len(),
        1,
        "PostgreSQL: Should have exactly 1 test_key_pg config entry, found {}",
        test_entries.len()
    );

    pool.close().await;
}

/// Test: Execution CRUD with new schema (SQLite)
#[tokio::test]
async fn test_execution_crud_sqlite() {
    let pool = create_test_pool_sqlite().await;
    run_migrations_sqlite(&pool)
        .await
        .expect("Failed to run migrations on SQLite");

    let exec_id = create_execution(&pool, "ctx-001", r#"{"prompt":"hello"}"#)
        .await
        .expect("Failed to create execution on SQLite");

    let exec = get_execution_by_id(&pool, &exec_id)
        .await
        .expect("Failed to retrieve execution on SQLite");
    assert_eq!(exec.context_id, "ctx-001");
    assert_eq!(exec.status, "submitted");

    pool.close().await;
}

/// Test: Execution CRUD with new schema (PostgreSQL)
#[tokio::test]
async fn test_execution_crud_postgres() {
    let (pool, db_url) = create_test_pool_postgres().await;
    run_migrations_postgres(&pool, &db_url)
        .await
        .expect("Failed to run migrations on PostgreSQL");

    let exec_id = create_execution(&pool, "ctx-002", r#"{"prompt":"hello"}"#)
        .await
        .expect("Failed to create execution on PostgreSQL");

    let exec = get_execution_by_id(&pool, &exec_id)
        .await
        .expect("Failed to retrieve execution on PostgreSQL");
    assert_eq!(exec.context_id, "ctx-002");
    assert_eq!(exec.status, "submitted");

    pool.close().await;
}

/// Test: Migration Validation (SQLite)
#[tokio::test]
async fn test_migrations_sqlite() {
    let sqlite_pool = create_test_pool_sqlite().await;
    run_migrations_sqlite(&sqlite_pool)
        .await
        .expect("SQLite migrations failed");

    let tables = get_table_names(&sqlite_pool)
        .await
        .expect("Failed to get SQLite table names");

    // Verify key tables exist
    for expected in &[
        "executions",
        "sessions",
        "events",
        "agents",
        "projects",
        "artifacts",
        "task_queue",
        "config",
        "schema_migrations",
    ] {
        assert!(
            tables.contains(&expected.to_string()),
            "SQLite missing table: {expected}"
        );
    }

    // Test idempotence
    run_migrations_sqlite(&sqlite_pool)
        .await
        .expect("SQLite migrations not idempotent");

    sqlite_pool.close().await;
}

/// Test: Migration Validation (PostgreSQL)
#[tokio::test]
async fn test_migrations_postgres() {
    let (postgres_pool, db_url) = create_test_pool_postgres().await;
    run_migrations_postgres(&postgres_pool, &db_url)
        .await
        .expect("PostgreSQL migrations failed");

    let tables = get_table_names(&postgres_pool)
        .await
        .expect("Failed to get PostgreSQL table names");

    for expected in &[
        "executions",
        "sessions",
        "events",
        "agents",
        "projects",
        "artifacts",
        "task_queue",
        "config",
        "schema_migrations",
    ] {
        assert!(
            tables.contains(&expected.to_string()),
            "PostgreSQL missing table: {expected}"
        );
    }

    // Test idempotence
    run_migrations_postgres(&postgres_pool, &db_url)
        .await
        .expect("PostgreSQL migrations not idempotent");

    postgres_pool.close().await;
}

/// Test: PostgreSQL Parameter Placeholder Conversion
#[tokio::test]
async fn test_parameter_conversion_sqlite() {
    let pool = create_test_pool_sqlite().await;

    let query = "SELECT * FROM config WHERE name = ? AND value = ?";
    let converted = pool.prepare_query(query);

    assert_eq!(
        converted, query,
        "SQLite should keep ? placeholders unchanged"
    );
    assert!(!pool.is_postgres(), "Pool should be identified as SQLite");

    pool.close().await;
}

#[tokio::test]
async fn test_parameter_conversion_postgres() {
    let (pool, _db_url) = create_test_pool_postgres().await;

    let query0 = "SELECT * FROM config";
    let converted0 = pool.prepare_query(query0);
    assert_eq!(
        converted0, query0,
        "Query with 0 parameters should remain unchanged"
    );

    let query1 = "SELECT * FROM config WHERE name = ?";
    let converted1 = pool.prepare_query(query1);
    assert_eq!(
        converted1, "SELECT * FROM config WHERE name = $1",
        "Single ? should convert to $1 for PostgreSQL"
    );

    let query2 = "SELECT * FROM config WHERE name = ? AND value = ?";
    let converted2 = pool.prepare_query(query2);
    assert_eq!(
        converted2, "SELECT * FROM config WHERE name = $1 AND value = $2",
        "Multiple ? should convert to $1, $2 for PostgreSQL"
    );

    assert!(
        pool.is_postgres(),
        "Pool should be identified as PostgreSQL"
    );

    pool.close().await;
}

/// Test: PostgreSQL URL Scheme Recognition
#[tokio::test]
async fn test_url_scheme_postgres() {
    use scheduler::db::pool;

    common::install_drivers_once();

    // Short-form postgres:// scheme
    {
        let url = "postgres://postgres:postgres@0.0.0.0/agentbeacon_test";
        let pool = pool::create(url)
            .await
            .expect("postgres:// scheme should be recognized");

        assert!(
            pool.is_postgres(),
            "postgres:// scheme should be identified as PostgreSQL"
        );

        pool.close().await;
    }

    // Official postgresql:// scheme (RFC 3986)
    {
        let url = "postgresql://postgres:postgres@0.0.0.0/agentbeacon_test";
        let pool = pool::create(url)
            .await
            .expect("postgresql:// scheme should be recognized");

        assert!(
            pool.is_postgres(),
            "postgresql:// scheme should be identified as PostgreSQL"
        );

        pool.close().await;
    }
}

/// Test: URL Scheme Recognition (SQLite + invalid schemes)
#[tokio::test]
async fn test_url_scheme_sqlite_and_invalid() {
    use scheduler::db::pool;

    common::install_drivers_once();

    // SQLite still recognized
    {
        let pool = pool::create("sqlite::memory:")
            .await
            .expect("sqlite: scheme should be recognized");

        assert!(
            !pool.is_postgres(),
            "sqlite: scheme should NOT be identified as PostgreSQL"
        );

        pool.close().await;
    }

    // Invalid schemes rejected
    {
        let result = pool::create("mysql://localhost/test").await;

        assert!(
            result.is_err(),
            "Invalid database URL schemes should be rejected"
        );

        if let Err(e) = result {
            let err_msg = e.to_string();
            assert!(
                err_msg.contains("postgres://") || err_msg.contains("postgresql://"),
                "Error message should mention both PostgreSQL schemes, got: {err_msg}"
            );
            assert!(
                err_msg.contains("sqlite://"),
                "Error message should mention SQLite scheme, got: {err_msg}"
            );
        }
    }
}

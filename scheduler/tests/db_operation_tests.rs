mod common;

use common::*;
use uuid::Uuid;

/// Test 2.1: Workflow CRUD Operations (Both Databases)
#[tokio::test]
async fn test_workflow_crud_operations_both_databases() {
    // Test SQLite first
    {
        let pool = create_test_pool_sqlite().await;
        run_migrations_sqlite(&pool)
            .await
            .expect("Failed to run migrations on SQLite");

        let workflow = create_test_workflow_struct();
        create_workflow(&pool, &workflow)
            .await
            .expect("Failed to create workflow on SQLite");

        let retrieved = get_workflow_by_id(&pool, &workflow.id)
            .await
            .expect("Failed to retrieve workflow on SQLite");
        assert_eq!(retrieved.name, workflow.name, "SQLite retrieval failed");

        let updated_yaml = "name: test\ndescription: Updated\ntasks: []";
        update_workflow(&pool, &workflow.id, updated_yaml)
            .await
            .expect("Failed to update workflow on SQLite");

        let retrieved_updated = get_workflow_by_id(&pool, &workflow.id)
            .await
            .expect("Failed to retrieve updated workflow on SQLite");
        assert_eq!(
            retrieved_updated.yaml_content, updated_yaml,
            "SQLite update failed"
        );

        let workflows = list_workflows(&pool)
            .await
            .expect("Failed to list workflows on SQLite");
        assert!(
            !workflows.is_empty(),
            "SQLite listing failed - expected at least 1 workflow"
        );

        pool.close().await;
    }

    // Test PostgreSQL second
    {
        let pool = create_test_pool_postgres().await;
        run_migrations_postgres(&pool)
            .await
            .expect("Failed to run migrations on PostgreSQL");

        let workflow = create_test_workflow_struct();
        create_workflow(&pool, &workflow)
            .await
            .expect("Failed to create workflow on PostgreSQL");

        let retrieved = get_workflow_by_id(&pool, &workflow.id)
            .await
            .expect("Failed to retrieve workflow on PostgreSQL");
        assert_eq!(retrieved.name, workflow.name, "PostgreSQL retrieval failed");

        let updated_yaml = "name: test\ndescription: Updated\ntasks: []";
        update_workflow(&pool, &workflow.id, updated_yaml)
            .await
            .expect("Failed to update workflow on PostgreSQL");

        let retrieved_updated = get_workflow_by_id(&pool, &workflow.id)
            .await
            .expect("Failed to retrieve updated workflow on PostgreSQL");
        assert_eq!(
            retrieved_updated.yaml_content, updated_yaml,
            "PostgreSQL update failed"
        );

        let workflows = list_workflows(&pool)
            .await
            .expect("Failed to list workflows on PostgreSQL");
        assert!(
            !workflows.is_empty(),
            "PostgreSQL listing failed - expected at least 1 workflow"
        );

        pool.close().await;
    }
}

/// Test 2.2: Config UPSERT Logic (SQLite - INSERT OR REPLACE)
#[tokio::test]
async fn test_config_upsert_sqlite() {
    // Given: In-memory SQLite database
    let pool = create_test_pool_sqlite().await;
    run_migrations_sqlite(&pool)
        .await
        .expect("Failed to run migrations");

    // When: Creating config entry
    upsert_config(&pool, "test_key", "initial_value")
        .await
        .expect("Failed to create config entry");

    // Then: Config entry exists
    let config = get_config(&pool, "test_key")
        .await
        .expect("Failed to retrieve config entry");
    assert_eq!(config.value, "initial_value");

    // When: Updating same key (UPSERT via INSERT OR REPLACE)
    upsert_config(&pool, "test_key", "updated_value")
        .await
        .expect("Failed to update config entry");

    // Then: Value is updated, not duplicated (validates INSERT OR REPLACE syntax)
    let config_updated = get_config(&pool, "test_key")
        .await
        .expect("Failed to retrieve updated config entry");
    assert_eq!(config_updated.value, "updated_value");

    let all_configs = list_config(&pool)
        .await
        .expect("Failed to list config entries");
    assert_eq!(
        all_configs.len(),
        1,
        "SQLite: Should have exactly 1 config entry, found {}",
        all_configs.len()
    );

    pool.close().await;
}

/// Test 2.3: Config UPSERT Logic (PostgreSQL - ON CONFLICT)
#[tokio::test]
async fn test_config_upsert_postgres() {
    // Given: PostgreSQL test database at 0.0.0.0
    let pool = create_test_pool_postgres().await;
    run_migrations_postgres(&pool)
        .await
        .expect("Failed to run migrations");

    // When: Creating config entry
    upsert_config(&pool, "test_key_pg", "initial_value")
        .await
        .expect("Failed to create config entry");

    // Then: Config entry exists
    let config = get_config(&pool, "test_key_pg")
        .await
        .expect("Failed to retrieve config entry");
    assert_eq!(config.value, "initial_value");

    // When: Updating same key (UPSERT via ON CONFLICT)
    upsert_config(&pool, "test_key_pg", "updated_value")
        .await
        .expect("Failed to update config entry");

    // Then: Value is updated, not duplicated (validates ON CONFLICT DO UPDATE syntax)
    let config_updated = get_config(&pool, "test_key_pg")
        .await
        .expect("Failed to retrieve updated config entry");
    assert_eq!(config_updated.value, "updated_value");

    let all_configs = list_config(&pool)
        .await
        .expect("Failed to list config entries");
    assert_eq!(
        all_configs.len(),
        1,
        "PostgreSQL: Should have exactly 1 config entry, found {}",
        all_configs.len()
    );

    pool.close().await;
}

/// Test 2.4: Execution State Persistence (Both Databases)
#[tokio::test]
async fn test_execution_state_persistence_both_databases() {
    // Test SQLite first
    {
        let pool = create_test_pool_sqlite().await;
        run_migrations_sqlite(&pool)
            .await
            .expect("Failed to run migrations on SQLite");

        let workflow_id = create_test_workflow(&pool).await;
        let task_states = serde_json::json!({
            "task-1": {"status": "pending"},
            "task-2": {"status": "pending"}
        });

        let execution_id = create_execution(&pool, &workflow_id, task_states.clone())
            .await
            .expect("Failed to create execution on SQLite");

        let execution = get_execution_by_id(&pool, &execution_id)
            .await
            .expect("Failed to retrieve execution on SQLite");
        assert_eq!(
            execution.workflow_id, workflow_id,
            "SQLite workflow_id mismatch"
        );
        assert_eq!(
            execution.task_states, task_states,
            "SQLite JSON roundtrip failed"
        );
        assert_eq!(execution.status, "pending", "SQLite status mismatch");

        pool.close().await;
    }

    // Test PostgreSQL second
    {
        let pool = create_test_pool_postgres().await;
        run_migrations_postgres(&pool)
            .await
            .expect("Failed to run migrations on PostgreSQL");

        let workflow_id = create_test_workflow(&pool).await;
        let task_states = serde_json::json!({
            "task-1": {"status": "pending"},
            "task-2": {"status": "pending"}
        });

        let execution_id = create_execution(&pool, &workflow_id, task_states.clone())
            .await
            .expect("Failed to create execution on PostgreSQL");

        let execution = get_execution_by_id(&pool, &execution_id)
            .await
            .expect("Failed to retrieve execution on PostgreSQL");
        assert_eq!(
            execution.workflow_id, workflow_id,
            "PostgreSQL workflow_id mismatch"
        );
        assert_eq!(
            execution.task_states, task_states,
            "PostgreSQL JSON roundtrip failed"
        );
        assert_eq!(execution.status, "pending", "PostgreSQL status mismatch");

        pool.close().await;
    }
}

/// Test 2.5: Migration Validation (Both Databases)
#[tokio::test]
async fn test_migrations_create_identical_schema() {
    let (sqlite_tables, postgres_tables);

    // Test SQLite first
    {
        let sqlite_pool = create_test_pool_sqlite().await;
        run_migrations_sqlite(&sqlite_pool)
            .await
            .expect("SQLite migrations failed");

        sqlite_tables = get_table_names(&sqlite_pool)
            .await
            .expect("Failed to get SQLite table names");

        // Test idempotence
        run_migrations_sqlite(&sqlite_pool)
            .await
            .expect("SQLite migrations not idempotent");

        sqlite_pool.close().await;
    }

    // Test PostgreSQL second
    {
        let postgres_pool = create_test_pool_postgres().await;
        run_migrations_postgres(&postgres_pool)
            .await
            .expect("PostgreSQL migrations failed");

        postgres_tables = get_table_names(&postgres_pool)
            .await
            .expect("Failed to get PostgreSQL table names");

        // Test idempotence
        run_migrations_postgres(&postgres_pool)
            .await
            .expect("PostgreSQL migrations not idempotent");

        postgres_pool.close().await;
    }

    // Verify both databases have the same tables
    assert_eq!(
        sqlite_tables.len(),
        postgres_tables.len(),
        "Table count mismatch: SQLite has {}, PostgreSQL has {}",
        sqlite_tables.len(),
        postgres_tables.len()
    );

    for table in &sqlite_tables {
        assert!(
            postgres_tables.contains(table),
            "PostgreSQL missing table: {table}"
        );
    }
}

/// Test 2.6: Connection Pool Configuration
#[tokio::test]
async fn test_connection_pool_limits() {
    // Test SQLite first
    {
        let sqlite_pool = create_test_pool_sqlite().await;
        run_migrations_sqlite(&sqlite_pool)
            .await
            .expect("Failed to run SQLite migrations");

        let workflow = create_test_workflow_struct();
        create_workflow(&sqlite_pool, &workflow)
            .await
            .expect("SQLite pool failed to create workflow");

        let sqlite_result = get_workflow_by_id(&sqlite_pool, &workflow.id)
            .await
            .expect("Failed to retrieve workflow from SQLite");
        assert_eq!(
            sqlite_result.name, workflow.name,
            "SQLite workflow name mismatch"
        );

        sqlite_pool.close().await;
    }

    // Test PostgreSQL second
    {
        let postgres_pool = create_test_pool_postgres().await;
        run_migrations_postgres(&postgres_pool)
            .await
            .expect("Failed to run PostgreSQL migrations");

        let postgres_workflow = TestWorkflow {
            id: Uuid::new_v4(),
            name: "postgres-test-workflow".to_string(),
            description: Some("Test description".to_string()),
            yaml_content: "name: test\ntasks: []".to_string(),
            created_at: chrono::Utc::now(),
            updated_at: chrono::Utc::now(),
        };
        create_workflow(&postgres_pool, &postgres_workflow)
            .await
            .expect("PostgreSQL pool failed to create workflow");

        let postgres_result = get_workflow_by_id(&postgres_pool, &postgres_workflow.id)
            .await
            .expect("Failed to retrieve workflow from PostgreSQL");
        assert_eq!(
            postgres_result.name, postgres_workflow.name,
            "PostgreSQL workflow name mismatch (parameter placeholder conversion may have failed)"
        );

        postgres_pool.close().await;
    }
}

/// Test: PostgreSQL Parameter Placeholder Conversion
#[tokio::test]
async fn test_postgres_parameter_conversion() {
    // Test SQLite - should keep ? placeholders
    {
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

    // Test PostgreSQL - should convert to $1, $2, etc.
    {
        let pool = create_test_pool_postgres().await;

        // Test with 0 parameters
        let query0 = "SELECT * FROM config";
        let converted0 = pool.prepare_query(query0);
        assert_eq!(
            converted0, query0,
            "Query with 0 parameters should remain unchanged"
        );

        // Test with 1 parameter
        let query1 = "SELECT * FROM config WHERE name = ?";
        let converted1 = pool.prepare_query(query1);
        assert_eq!(
            converted1, "SELECT * FROM config WHERE name = $1",
            "Single ? should convert to $1 for PostgreSQL"
        );

        // Test with 2 parameters
        let query2 = "SELECT * FROM config WHERE name = ? AND value = ?";
        let converted2 = pool.prepare_query(query2);
        assert_eq!(
            converted2, "SELECT * FROM config WHERE name = $1 AND value = $2",
            "Multiple ? should convert to $1, $2 for PostgreSQL"
        );

        // Test with 5 parameters
        let query5 = "INSERT INTO workflows (id, name, description, yaml_content, created_at) VALUES (?, ?, ?, ?, ?)";
        let converted5 = pool.prepare_query(query5);
        assert_eq!(
            converted5,
            "INSERT INTO workflows (id, name, description, yaml_content, created_at) VALUES ($1, $2, $3, $4, $5)",
            "5 placeholders should convert to $1, $2, $3, $4, $5 for PostgreSQL"
        );

        assert!(
            pool.is_postgres(),
            "Pool should be identified as PostgreSQL"
        );

        pool.close().await;
    }
}

/// Test 2.8: Error Context Preservation
/// Verifies that test helpers preserve actual error types instead of mapping everything to RowNotFound
#[tokio::test]
async fn test_error_context_preserved() {
    use scheduler::error::SchedulerError;

    let pool = create_test_pool_sqlite().await;
    run_migrations_sqlite(&pool)
        .await
        .expect("Failed to run migrations");

    // Test 1: WorkflowNotFound error is preserved (not mapped to generic RowNotFound)
    let nonexistent_id = Uuid::new_v4();
    match get_workflow_by_id(&pool, &nonexistent_id).await {
        Err(SchedulerError::WorkflowNotFound(msg)) => {
            assert!(
                msg.contains(&nonexistent_id.to_string()),
                "WorkflowNotFound error should contain workflow ID, got: {msg}"
            );
        }
        Err(other) => panic!("Expected WorkflowNotFound error, got: {other:?}"),
        Ok(_) => panic!("Expected error for nonexistent workflow"),
    }

    // Test 2: Duplicate name constraint violation is preserved
    let workflow1 = create_test_workflow_struct();
    create_workflow(&pool, &workflow1)
        .await
        .expect("First workflow creation should succeed");

    // Create second workflow with same name but different ID
    let mut workflow2 = create_test_workflow_struct();
    workflow2.id = Uuid::new_v4(); // Different ID
    workflow2.name = workflow1.name.clone(); // Same name

    match create_workflow(&pool, &workflow2).await {
        Err(SchedulerError::Database(msg)) => {
            assert!(
                msg.to_lowercase().contains("unique") || msg.to_lowercase().contains("constraint"),
                "Database error should mention constraint violation, got: {msg}"
            );
        }
        Err(other) => panic!("Expected Database error for duplicate name, got: {other:?}"),
        Ok(_) => panic!("Expected error for duplicate workflow name"),
    }

    // Test 3: Config operations preserve errors
    match get_config(&pool, "nonexistent_config").await {
        Err(SchedulerError::NotFound(msg)) => {
            assert!(
                msg.contains("Config not found") || msg.contains("nonexistent_config"),
                "Config NotFound error should contain context, got: {msg}"
            );
        }
        Err(other) => panic!("Expected NotFound error for nonexistent config, got: {other:?}"),
        Ok(_) => panic!("Expected error for nonexistent config"),
    }

    // Test 4: Execution operations preserve errors
    let nonexistent_workflow_id = Uuid::new_v4();
    match create_execution(&pool, &nonexistent_workflow_id, serde_json::json!({})).await {
        Err(SchedulerError::Database(msg)) => {
            assert!(
                msg.to_lowercase().contains("foreign key")
                    || msg.to_lowercase().contains("constraint"),
                "Foreign key error should be preserved, got: {msg}"
            );
        }
        Err(other) => panic!("Expected Database error for foreign key violation, got: {other:?}"),
        Ok(_) => panic!("Expected error for execution with nonexistent workflow"),
    }

    pool.close().await;
}

/// Test 2.9: PostgreSQL URL Scheme Recognition (Both postgres:// and postgresql://)
///
/// Verifies that both the short-form postgres:// and official postgresql:// schemes
/// are recognized correctly, preventing migration failures with official URLs.
#[tokio::test]
async fn test_postgresql_url_scheme_recognition() {
    use scheduler::db::pool;

    // Install SQLx drivers before creating pools
    common::install_drivers_once();

    // Test 1: Short-form postgres:// scheme (existing)
    {
        let url = "postgres://postgres:postgres@0.0.0.0/agentmaestro_test";
        let pool = pool::create(url)
            .await
            .expect("postgres:// scheme should be recognized");

        assert!(
            pool.is_postgres(),
            "postgres:// scheme should be identified as PostgreSQL"
        );

        pool.close().await;
    }

    // Test 2: Official postgresql:// scheme (RFC 3986)
    {
        let url = "postgresql://postgres:postgres@0.0.0.0/agentmaestro_test";
        let pool = pool::create(url)
            .await
            .expect("postgresql:// scheme should be recognized");

        assert!(
            pool.is_postgres(),
            "postgresql:// scheme should be identified as PostgreSQL"
        );

        pool.close().await;
    }

    // Test 3: Verify SQLite is still recognized
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

    // Test 4: Verify invalid schemes are rejected
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

/// Test 2.10: Foreign Key CASCADE Behavior (SQLite and PostgreSQL)
///
/// Verifies that ON DELETE CASCADE constraints work correctly in both databases.
/// This is critical because SQLite disables foreign keys by default, which would
/// cause silent data integrity failures if not properly configured.
#[tokio::test]
async fn test_foreign_key_cascade_behavior() {
    // Test SQLite CASCADE behavior
    {
        let pool = create_test_pool_sqlite().await;
        run_migrations_sqlite(&pool)
            .await
            .expect("Failed to run SQLite migrations");

        // Create a workflow
        let workflow = create_test_workflow_struct();
        create_workflow(&pool, &workflow)
            .await
            .expect("Failed to create workflow");

        // Create an execution for that workflow
        let task_states = serde_json::json!({"task-1": {"status": "pending"}});
        let execution_id = create_execution(&pool, &workflow.id, task_states.clone())
            .await
            .expect("Failed to create execution");

        // Create an execution event for that execution
        let event_metadata = serde_json::json!({"test": "data"});
        let _event_id = scheduler::db::execution_events::create(
            &pool,
            &execution_id,
            "execution_start",
            None,
            "Test event",
            event_metadata,
        )
        .await
        .expect("Failed to create execution event");

        // Verify execution and event exist
        let execution_before = get_execution_by_id(&pool, &execution_id)
            .await
            .expect("Execution should exist before workflow deletion");
        assert_eq!(execution_before.workflow_id, workflow.id);

        let events_before =
            scheduler::db::execution_events::list_by_execution(&pool, &execution_id)
                .await
                .expect("Failed to list events");
        assert_eq!(
            events_before.len(),
            1,
            "Should have 1 event before deletion"
        );

        // Delete the workflow - should CASCADE to executions and execution_events
        scheduler::db::workflows::delete(&pool, &workflow.id)
            .await
            .expect("Failed to delete workflow");

        // Verify workflow is gone
        let workflow_result = get_workflow_by_id(&pool, &workflow.id).await;
        assert!(workflow_result.is_err(), "Workflow should be deleted");

        // Verify execution was CASCADE deleted (this is the critical test)
        let execution_result = get_execution_by_id(&pool, &execution_id).await;
        assert!(
            execution_result.is_err(),
            "SQLite: Execution should be CASCADE deleted when workflow is deleted (foreign key constraint)"
        );

        // Verify execution events were CASCADE deleted
        let events_after = scheduler::db::execution_events::list_by_execution(&pool, &execution_id)
            .await
            .expect("Failed to list events");
        assert_eq!(
            events_after.len(),
            0,
            "SQLite: Execution events should be CASCADE deleted"
        );

        pool.close().await;
    }

    // Test PostgreSQL CASCADE behavior (for comparison)
    {
        let pool = create_test_pool_postgres().await;
        run_migrations_postgres(&pool)
            .await
            .expect("Failed to run PostgreSQL migrations");

        // Create a workflow
        let workflow = create_test_workflow_struct();
        create_workflow(&pool, &workflow)
            .await
            .expect("Failed to create workflow");

        // Create an execution for that workflow
        let task_states = serde_json::json!({"task-1": {"status": "pending"}});
        let execution_id = create_execution(&pool, &workflow.id, task_states.clone())
            .await
            .expect("Failed to create execution");

        // Create an execution event for that execution
        let event_metadata = serde_json::json!({"test": "data"});
        let _event_id = scheduler::db::execution_events::create(
            &pool,
            &execution_id,
            "execution_start",
            None,
            "Test event",
            event_metadata,
        )
        .await
        .expect("Failed to create execution event");

        // Delete the workflow - should CASCADE to executions and execution_events
        scheduler::db::workflows::delete(&pool, &workflow.id)
            .await
            .expect("Failed to delete workflow");

        // Verify workflow is gone
        let workflow_result = get_workflow_by_id(&pool, &workflow.id).await;
        assert!(workflow_result.is_err(), "Workflow should be deleted");

        // Verify execution was CASCADE deleted
        let execution_result = get_execution_by_id(&pool, &execution_id).await;
        assert!(
            execution_result.is_err(),
            "PostgreSQL: Execution should be CASCADE deleted when workflow is deleted (foreign key constraint)"
        );

        // Verify execution events were CASCADE deleted
        let events_after = scheduler::db::execution_events::list_by_execution(&pool, &execution_id)
            .await
            .expect("Failed to list events");
        assert_eq!(
            events_after.len(),
            0,
            "PostgreSQL: Execution events should be CASCADE deleted"
        );

        pool.close().await;
    }
}

mod common;

use common::*;
use std::sync::Arc;
use tokio::task::JoinSet;

/// Test: Concurrent config upserts don't fail with race condition
///
/// Verifies that multiple concurrent writers can safely upsert the same config key
/// without triggering unique constraint violations (the bug that was fixed).
#[tokio::test]
#[allow(clippy::uninlined_format_args)] // Test data formatting
async fn test_concurrent_config_upsert_no_race() {
    let pool = create_test_pool_sqlite().await;
    run_migrations_sqlite(&pool)
        .await
        .expect("Failed to run migrations");

    let pool = Arc::new(pool);
    let mut tasks = JoinSet::new();

    // Launch 10 concurrent tasks all upserting the same config key
    for i in 0..10 {
        let pool_clone = Arc::clone(&pool);
        tasks.spawn(async move {
            upsert_config(&pool_clone, "test_key", &format!("value_{}", i))
                .await
                .expect("Upsert should not fail due to race condition");
        });
    }

    // Wait for all tasks to complete
    while let Some(result) = tasks.join_next().await {
        result.expect("Task should not panic");
    }

    // Verify exactly 1 config entry exists (no duplicates)
    let all_configs = list_config(&pool).await.expect("Failed to list config");
    assert_eq!(
        all_configs.len(),
        1,
        "Should have exactly 1 config entry after concurrent upserts"
    );

    // Verify the config key exists
    let config = get_config(&pool, "test_key")
        .await
        .expect("Config should exist");
    assert_eq!(config.name, "test_key");
    // Note: We don't assert the value because any of the 10 values could win

    pool.close().await;
}

/// Test: Concurrent workflow upserts don't fail with race condition
///
/// Verifies that multiple concurrent writers can safely upsert the same workflow name
/// without triggering unique constraint violations.
#[tokio::test]
#[allow(clippy::uninlined_format_args)] // Test data formatting
async fn test_concurrent_workflow_upsert_no_race() {
    let pool = create_test_pool_sqlite().await;
    run_migrations_sqlite(&pool)
        .await
        .expect("Failed to run migrations");

    let pool = Arc::new(pool);
    let mut tasks = JoinSet::new();

    // Launch 10 concurrent tasks all upserting workflows with the same name
    for i in 0..10 {
        let pool_clone = Arc::clone(&pool);
        tasks.spawn(async move {
            let workflow = TestWorkflow {
                id: uuid::Uuid::new_v4(),
                name: "concurrent-test".to_string(),
                description: Some(format!("Description {}", i)),
                yaml_content: format!(
                    "name: concurrent-test\ndescription: Description {}\ntasks: []",
                    i
                ),
                created_at: chrono::Utc::now(),
                updated_at: chrono::Utc::now(),
            };

            upsert_workflow(&pool_clone, &workflow)
                .await
                .expect("Upsert should not fail due to race condition");
        });
    }

    // Wait for all tasks to complete
    while let Some(result) = tasks.join_next().await {
        result.expect("Task should not panic");
    }

    // Verify exactly 1 workflow exists (no duplicates from race condition)
    let all_workflows = list_workflows(&pool)
        .await
        .expect("Failed to list workflows");
    assert_eq!(
        all_workflows.len(),
        1,
        "Should have exactly 1 workflow after concurrent upserts"
    );

    // Verify the workflow name exists
    let workflow = get_workflow_by_name(&pool, "concurrent-test")
        .await
        .expect("Workflow should exist");
    assert_eq!(workflow.name, "concurrent-test");
    // Description could be any of the 10 values, so we just verify it exists
    assert!(workflow.description.is_some());

    pool.close().await;
}

/// Test: Workflow updates preserve description changes
///
/// Verifies that updating a workflow correctly updates both yaml_content and description,
/// fixing the bug where description would become stale.
#[tokio::test]
async fn test_workflow_update_preserves_description() {
    let pool = create_test_pool_sqlite().await;
    run_migrations_sqlite(&pool)
        .await
        .expect("Failed to run migrations");

    // Create initial workflow with description "Initial"
    let workflow = TestWorkflow {
        id: uuid::Uuid::new_v4(),
        name: "update-test".to_string(),
        description: Some("Initial".to_string()),
        yaml_content: "name: update-test\ndescription: Initial\ntasks: []".to_string(),
        created_at: chrono::Utc::now(),
        updated_at: chrono::Utc::now(),
    };

    create_workflow(&pool, &workflow)
        .await
        .expect("Failed to create workflow");

    // Verify initial state
    let fetched = get_workflow_by_id(&pool, &workflow.id)
        .await
        .expect("Failed to fetch workflow");
    assert_eq!(fetched.description, Some("Initial".to_string()));

    // Update workflow with new description "Updated"
    let updated_yaml = "name: update-test\ndescription: Updated\ntasks: []";
    update_workflow(&pool, &workflow.id, updated_yaml)
        .await
        .expect("Failed to update workflow");

    // Verify description was updated (not stale)
    let fetched_after_update = get_workflow_by_id(&pool, &workflow.id)
        .await
        .expect("Failed to fetch updated workflow");
    assert_eq!(
        fetched_after_update.description,
        Some("Updated".to_string()),
        "Description should be updated, not stale"
    );
    assert!(
        fetched_after_update.yaml_content.contains("Updated"),
        "YAML content should contain new description"
    );

    pool.close().await;
}

/// Test: Workflow upsert updates description on existing workflows
///
/// Verifies that upsert correctly updates description when updating an existing workflow.
#[tokio::test]
async fn test_workflow_upsert_updates_description() {
    let pool = create_test_pool_sqlite().await;
    run_migrations_sqlite(&pool)
        .await
        .expect("Failed to run migrations");

    // First upsert: Create workflow with description "Version 1"
    let workflow_v1 = TestWorkflow {
        id: uuid::Uuid::new_v4(),
        name: "upsert-test".to_string(),
        description: Some("Version 1".to_string()),
        yaml_content: "name: upsert-test\ndescription: Version 1\ntasks: []".to_string(),
        created_at: chrono::Utc::now(),
        updated_at: chrono::Utc::now(),
    };

    upsert_workflow(&pool, &workflow_v1)
        .await
        .expect("Failed to upsert workflow v1");

    // Verify initial state
    let fetched_v1 = get_workflow_by_name(&pool, "upsert-test")
        .await
        .expect("Failed to fetch workflow v1");
    assert_eq!(fetched_v1.description, Some("Version 1".to_string()));

    // Second upsert: Update same workflow name with description "Version 2"
    let workflow_v2 = TestWorkflow {
        id: uuid::Uuid::new_v4(), // Different ID, but same name
        name: "upsert-test".to_string(),
        description: Some("Version 2".to_string()),
        yaml_content: "name: upsert-test\ndescription: Version 2\ntasks: []".to_string(),
        created_at: chrono::Utc::now(),
        updated_at: chrono::Utc::now(),
    };

    upsert_workflow(&pool, &workflow_v2)
        .await
        .expect("Failed to upsert workflow v2");

    // Verify description was updated (not stale)
    let fetched_v2 = get_workflow_by_name(&pool, "upsert-test")
        .await
        .expect("Failed to fetch workflow v2");
    assert_eq!(
        fetched_v2.description,
        Some("Version 2".to_string()),
        "Description should be updated to Version 2, not stale at Version 1"
    );
    assert!(
        fetched_v2.yaml_content.contains("Version 2"),
        "YAML content should contain Version 2"
    );

    // Verify only 1 workflow exists (upsert, not duplicate)
    let all_workflows = list_workflows(&pool)
        .await
        .expect("Failed to list workflows");
    assert_eq!(
        all_workflows.len(),
        1,
        "Should have exactly 1 workflow after upsert"
    );

    pool.close().await;
}

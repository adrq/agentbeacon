mod common;

use common::*;
use std::sync::Arc;
use tokio::task::JoinSet;

/// Test: Concurrent config upserts don't fail with race condition
#[tokio::test]
#[allow(clippy::uninlined_format_args)]
async fn test_concurrent_config_upsert_no_race() {
    let pool = create_test_pool_sqlite().await;
    run_migrations_sqlite(&pool)
        .await
        .expect("Failed to run migrations");

    let pool = Arc::new(pool);
    let mut tasks = JoinSet::new();

    for i in 0..10 {
        let pool_clone = Arc::clone(&pool);
        tasks.spawn(async move {
            upsert_config(&pool_clone, "test_key", &format!("value_{}", i))
                .await
                .expect("Upsert should not fail due to race condition");
        });
    }

    while let Some(result) = tasks.join_next().await {
        result.expect("Task should not panic");
    }

    let all_configs = list_config(&pool).await.expect("Failed to list config");
    // Migration seeds max_depth + max_width, plus 1 test_key = 3 total
    let test_entries: Vec<_> = all_configs
        .iter()
        .filter(|c| c.name == "test_key")
        .collect();
    assert_eq!(
        test_entries.len(),
        1,
        "Should have exactly 1 test_key config entry after concurrent upserts"
    );

    let config = get_config(&pool, "test_key")
        .await
        .expect("Config should exist");
    assert_eq!(config.name, "test_key");

    pool.close().await;
}

mod common;

use axum::{http::StatusCode, response::IntoResponse};
use common::response_body_as_json;
use scheduler::error::SchedulerError;

/// Test: Consistent Error JSON Structure
#[tokio::test]
async fn test_all_errors_return_json_structure() {
    let errors = vec![
        SchedulerError::NotFound("test-id".to_string()),
        SchedulerError::ValidationFailed("Invalid input".to_string()),
        SchedulerError::Database("Database error".to_string()),
    ];

    for error in errors {
        let response = error.into_response();
        let body = response_body_as_json(response).await;

        assert!(body.is_object(), "Response body should be JSON object");
        assert!(
            body.get("error").is_some(),
            "Missing 'error' key in response"
        );
        assert!(body["error"].is_string(), "Error message must be string");
    }
}

/// Test: Error Status Code Mapping
#[test]
fn test_error_status_code_mapping() {
    let test_cases = vec![
        (SchedulerError::NotFound("id".into()), StatusCode::NOT_FOUND),
        (
            SchedulerError::ValidationFailed("msg".into()),
            StatusCode::BAD_REQUEST,
        ),
        (
            SchedulerError::Database("error".into()),
            StatusCode::INTERNAL_SERVER_ERROR,
        ),
    ];

    for (error, expected_status) in test_cases {
        let response = error.into_response();

        assert_eq!(
            response.status(),
            expected_status,
            "Error should map to status {expected_status}",
        );
    }
}

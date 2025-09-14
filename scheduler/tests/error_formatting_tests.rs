// Error formatting unit tests
mod common;

use axum::{http::StatusCode, response::IntoResponse};
use common::response_body_as_json;
use scheduler::error::SchedulerError;

/// Test 3.1: Consistent Error JSON Structure
#[tokio::test]
async fn test_all_errors_return_json_structure() {
    // Given: Different error types
    let errors = vec![
        SchedulerError::WorkflowNotFound("test-id".to_string()),
        SchedulerError::ValidationFailed("Invalid YAML".to_string()),
        SchedulerError::Database("Database error".to_string()),
    ];

    // When: Converting to HTTP responses
    for error in errors {
        let response = error.into_response();
        let body = response_body_as_json(response).await;

        // Then: All have {"error": "..."} structure
        assert!(body.is_object(), "Response body should be JSON object");
        assert!(
            body.get("error").is_some(),
            "Missing 'error' key in response"
        );
        assert!(body["error"].is_string(), "Error message must be string");
    }
}

/// Test 3.2: Error Status Code Mapping
#[test]
#[allow(clippy::uninlined_format_args)] // Test assertion formatting
fn test_error_status_code_mapping() {
    // Given: Different error types
    let test_cases = vec![
        (
            SchedulerError::WorkflowNotFound("id".into()),
            StatusCode::NOT_FOUND,
        ),
        (
            SchedulerError::ValidationFailed("msg".into()),
            StatusCode::BAD_REQUEST,
        ),
        (
            SchedulerError::Database("error".into()),
            StatusCode::INTERNAL_SERVER_ERROR,
        ),
    ];

    // When: Converting to HTTP responses
    for (error, expected_status) in test_cases {
        let response = error.into_response();

        // Then: Status code matches expected mapping
        assert_eq!(
            response.status(),
            expected_status,
            "Error should map to status {}",
            expected_status
        );
    }
}

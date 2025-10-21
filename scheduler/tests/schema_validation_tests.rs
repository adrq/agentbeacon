mod common;

use common::create_test_validator;

/// Test 1.1: Invalid A2A Message Role
#[test]
#[allow(clippy::uninlined_format_args)] // Test assertion formatting
fn test_invalid_a2a_message_role_fails() {
    // Given: Workflow YAML with invalid message role
    let yaml = r#"
name: invalid-role
description: "Invalid role test"
tasks:
  - id: task-1
    agent: mock-agent
    task:
      message:
        messageId: msg-1
        kind: message
        role: invalid_role
        parts:
          - kind: text
            text: "Test"
"#;

    // When: Validating against workflow-schema.json
    let validator = create_test_validator().expect("Validator should be created");
    let result = validator.validate_workflow_yaml(yaml);
    assert!(result.is_err());
    let error = result.unwrap_err().to_string();
    assert!(
        error.contains("role") || error.contains("invalid_role"),
        "Error should mention invalid role: {}",
        error
    );
}

/// Test 1.3: Missing Required Fields
#[test]
#[allow(clippy::uninlined_format_args)] // Test assertion formatting
fn test_missing_required_tasks_field_fails() {
    // Given: Workflow YAML missing required 'tasks' field
    let yaml = r#"
name: missing-tasks
description: "Missing required tasks array"
"#;

    // When: Validating against workflow-schema.json
    let validator = create_test_validator().expect("Validator should be created");
    let result = validator.validate_workflow_yaml(yaml);
    assert!(result.is_err());
    let error = result.unwrap_err().to_string();
    assert!(
        error.contains("tasks") || error.contains("required"),
        "Error should mention missing tasks field: {}",
        error
    );
}

/// Test 1.4: Invalid Task ID Format
#[test]
#[allow(clippy::uninlined_format_args)] // Test assertion formatting
fn test_invalid_task_id_format_fails() {
    // Given: Workflow YAML with invalid task ID (must match ^[a-zA-Z0-9_-]+$)
    let yaml = r#"
name: invalid-task-id
description: "Invalid task ID test"
tasks:
  - id: task@invalid
    agent: mock-agent
    task:
      message:
        messageId: msg-1
        kind: message
        role: user
        parts:
          - kind: text
            text: "Test"
"#;

    // When: Validating against workflow-schema.json
    let validator = create_test_validator().expect("Validator should be created");
    let result = validator.validate_workflow_yaml(yaml);
    assert!(result.is_err());
    let error = result.unwrap_err().to_string();
    assert!(
        error.contains("id") || error.contains("pattern"),
        "Error should mention invalid task ID pattern: {}",
        error
    );
}

/// Test 1.6: Empty Workflow Name
#[test]
#[allow(clippy::uninlined_format_args)] // Test assertion formatting
fn test_empty_workflow_name_fails() {
    // Given: Workflow YAML with empty name (must be 1-128 chars)
    let yaml = r#"
name: ""
description: "Empty name test"
tasks:
  - id: task-1
    agent: mock-agent
    task:
      message:
        messageId: msg-1
        kind: message
        role: user
        parts:
          - kind: text
            text: "Test"
"#;

    // When: Validating against workflow-schema.json
    let validator = create_test_validator().expect("Validator should be created");
    let result = validator.validate_workflow_yaml(yaml);
    assert!(result.is_err());
    let error = result.unwrap_err().to_string();
    assert!(
        error.contains("shorter") || error.contains("minLength") || error.contains("name"),
        "Error should mention string length constraint: {}",
        error
    );
}

/// Test 1.7: Invalid A2A Part Kind
#[test]
#[allow(clippy::uninlined_format_args)] // Test assertion formatting
fn test_invalid_a2a_part_kind_fails() {
    // Given: Workflow YAML with invalid Part kind (must be text/data/resource)
    let yaml = r#"
name: invalid-part-kind
description: "Invalid part kind test"
tasks:
  - id: task-1
    agent: mock-agent
    task:
      message:
        messageId: msg-1
        kind: message
        role: user
        parts:
          - kind: invalid_kind
            text: "Test"
"#;

    // When: Validating against workflow-schema.json
    let validator = create_test_validator().expect("Validator should be created");
    let result = validator.validate_workflow_yaml(yaml);
    assert!(result.is_err());
    let error = result.unwrap_err().to_string();
    assert!(
        error.contains("kind") || error.contains("invalid_kind"),
        "Error should mention invalid part kind: {}",
        error
    );
}

// =============================================================================
// Tests for MessageSendParams Migration (T001)
// =============================================================================

/// Schema compilation with MessageSendParams $ref from A2A spec
#[test]
fn test_schema_compiles_with_messagesendparams_ref() {
    let validator = create_test_validator();

    assert!(
        validator.is_ok(),
        "Schema with MessageSendParams $ref should compile successfully"
    );
}

/// Validates new MessageSendParams format with task.message
#[test]
fn test_valid_new_format_validates() {
    let yaml = r#"
name: test-messagesendparams
tasks:
  - id: task-1
    agent: test-agent
    task:
      message:
        messageId: msg-1
        kind: message
        role: user
        parts:
          - kind: text
            text: "Test message"
"#;

    let validator = create_test_validator().expect("Validator should be created");
    let result = validator.validate_workflow_yaml(yaml);

    assert!(
        result.is_ok(),
        "Valid MessageSendParams format should validate: {:?}",
        result.err()
    );
}

/// Test that mixed-case task identifiers are valid
#[test]
fn test_mixed_case_identifiers_validate() {
    let yaml = r#"
name: test-mixed-case-ids
tasks:
  - id: AnalyzeCode
    agent: test-agent
    task:
      message:
        messageId: msg-1
        kind: message
        role: user
        parts:
          - kind: text
            text: "Analyze the codebase"
  - id: GenerateReport
    agent: test-agent
    depends_on:
      - AnalyzeCode
    task:
      message:
        messageId: msg-2
        kind: message
        role: user
        parts:
          - kind: text
            text: "Generate a report"
"#;

    let validator = create_test_validator().expect("Validator should be created");
    let result = validator.validate_workflow_yaml(yaml);

    assert!(
        result.is_ok(),
        "Mixed-case identifiers should be valid: {:?}",
        result.err()
    );
}

/// Rejects legacy task.history array format
#[test]
fn test_old_format_history_rejected() {
    let yaml = r#"
name: test-old-format
tasks:
  - id: task-1
    agent: test-agent
    task:
      history:
        - messageId: msg-1
          kind: message
          role: user
          parts:
            - kind: text
              text: "Old format"
"#;

    let validator = create_test_validator().expect("Validator should be created");
    let result = validator.validate_workflow_yaml(yaml);

    assert!(
        result.is_err(),
        "Old format with 'history' field should be rejected"
    );
}

/// Message without messageId validates (auto-generated at runtime)
#[test]
fn test_message_without_messageid_validates() {
    let yaml = r#"
name: test-no-messageid
tasks:
  - id: task-1
    agent: test-agent
    task:
      message:
        kind: message
        role: user
        parts:
          - kind: text
            text: "Test message without messageId"
"#;

    let validator = create_test_validator().expect("Validator should be created");
    let result = validator.validate_workflow_yaml(yaml);

    assert!(
        result.is_ok(),
        "Message without messageId should validate (injected at validation): {:?}",
        result.err()
    );
}

/// Message without kind validates (auto-injected at runtime)
#[test]
fn test_message_without_kind_validates() {
    let yaml = r#"
name: test-no-kind
tasks:
  - id: task-1
    agent: test-agent
    task:
      message:
        messageId: msg-1
        role: user
        parts:
          - kind: text
            text: "Test message without kind"
"#;

    let validator = create_test_validator().expect("Validator should be created");
    let result = validator.validate_workflow_yaml(yaml);

    assert!(
        result.is_ok(),
        "Message without kind should validate (injected at validation): {:?}",
        result.err()
    );
}

/// Message without messageId and kind validates (both auto-injected)
#[test]
fn test_message_without_messageid_and_kind_validates() {
    let yaml = r#"
name: test-minimal-message
tasks:
  - id: task-1
    agent: test-agent
    task:
      message:
        role: user
        parts:
          - kind: text
            text: "Minimal message structure"
"#;

    let validator = create_test_validator().expect("Validator should be created");
    let result = validator.validate_workflow_yaml(yaml);

    assert!(
        result.is_ok(),
        "Message without messageId and kind should validate (both injected): {:?}",
        result.err()
    );
}

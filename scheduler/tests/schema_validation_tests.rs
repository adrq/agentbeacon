mod common;

use common::create_test_validator;

/// Test 1.1: Valid A2A Message Structure
#[test]
fn test_valid_a2a_message_validates() {
    // Given: Valid workflow YAML with A2A Message structure
    let yaml = r#"
name: valid-workflow
description: "Valid A2A workflow"
tasks:
  - id: task-1
    agent: mock-agent
    task:
      history:
        - messageId: msg-1
          kind: message
          role: user
          parts:
            - kind: text
              text: "Hello A2A"
"#;

    // When: Validating against workflow-schema.json
    let validator = create_test_validator().expect("Validator should be created");
    let result = validator.validate_workflow_yaml(yaml);
    assert!(
        result.is_ok(),
        "Valid A2A Message structure should validate"
    );
}

/// Test 1.2: Invalid A2A Message Role
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
      history:
        - messageId: msg-1
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

/// Test 1.4: Cross-Schema $ref Resolution (A2A Artifact)
#[test]
fn test_a2a_artifact_ref_resolves() {
    // Given: Workflow YAML with A2A Artifact structure
    let yaml = r#"
name: artifacts-test
description: "Test A2A Artifact validation"
tasks:
  - id: task-1
    agent: mock-agent
    task:
      history:
        - messageId: msg-1
          kind: message
          role: user
          parts:
            - kind: text
              text: "Test"
      artifacts:
        - artifactId: output-1
          name: output.txt
          mimeType: text/plain
          parts:
            - kind: text
              text: "Result data"
"#;

    // When: Validating (requires InMemoryRetriever for $ref resolution)
    let validator = create_test_validator().expect("Validator should be created");
    let result = validator.validate_workflow_yaml(yaml);
    assert!(
        result.is_ok(),
        "A2A Artifact structure should validate via $ref resolution"
    );
}

/// Test 1.5: Invalid Task ID Format
#[test]
#[allow(clippy::uninlined_format_args)] // Test assertion formatting
fn test_invalid_task_id_format_fails() {
    // Given: Workflow YAML with invalid task ID (must match ^[a-z0-9_-]+$)
    let yaml = r#"
name: invalid-task-id
description: "Invalid task ID test"
tasks:
  - id: Task-1-Invalid
    agent: mock-agent
    task:
      history:
        - messageId: msg-1
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
      history:
        - messageId: msg-1
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
      history:
        - messageId: msg-1
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

/// Test 1.8: Circular $ref Detection
#[test]
fn test_circular_ref_detection_at_startup() {
    // When: Creating schema validator (should fail-fast at startup if circular $ref)
    let validator = create_test_validator();

    // Then: Validator creation should succeed (no circular refs in official schemas)
    assert!(
        validator.is_ok(),
        "Schema compilation should succeed (no circular refs in docs/)"
    );
}

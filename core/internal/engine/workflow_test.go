package engine

import (
	"encoding/json"
	"strings"
	"testing"
	"time"

	"gopkg.in/yaml.v3"
)

// Test that our custom error types implement the error interface correctly
func TestValidationError(t *testing.T) {
	err := ValidationError{
		Field:   "name",
		Message: "field is required",
	}

	// Test that ValidationError implements error interface
	var _ error = err

	expected := "validation error: field 'name' - field is required"
	if err.Error() != expected {
		t.Errorf("Error message mismatch: got %q, want %q", err.Error(), expected)
	}
}

func TestDAGError(t *testing.T) {
	err := DAGError{
		Type:    "cycle_detected",
		Message: "circular dependency found between nodes",
	}

	// Test that DAGError implements error interface
	var _ error = err

	expected := "DAG error (cycle_detected): circular dependency found between nodes"
	if err.Error() != expected {
		t.Errorf("Error message mismatch: got %q, want %q", err.Error(), expected)
	}
}

// Test that YAML exclusion tags work correctly
func TestWorkflowYAMLExclusion(t *testing.T) {
	workflow := Workflow{
		ID:          "wf-123", // Should be excluded with yaml:"-"
		Name:        "Test Workflow",
		Description: "Test description",
		CreatedAt:   time.Now(), // Should be excluded with yaml:"-"
		UpdatedAt:   time.Now(), // Should be excluded with yaml:"-"
	}

	yamlData, err := yaml.Marshal(workflow)
	if err != nil {
		t.Fatalf("Failed to marshal to YAML: %v", err)
	}

	yamlStr := string(yamlData)

	// ID, CreatedAt, UpdatedAt should be excluded
	if strings.Contains(yamlStr, "id:") {
		t.Error("ID should be excluded from YAML with yaml:\"-\"")
	}
	if strings.Contains(yamlStr, "created_at:") {
		t.Error("CreatedAt should be excluded from YAML")
	}
	if strings.Contains(yamlStr, "updated_at:") {
		t.Error("UpdatedAt should be excluded from YAML")
	}

	// Name and Description should be included
	if !strings.Contains(yamlStr, "name:") {
		t.Error("Name should be included in YAML")
	}
	if !strings.Contains(yamlStr, "description:") {
		t.Error("Description should be included in YAML")
	}
}

// Test that omitempty tags work correctly for optional fields
func TestJSONOmitemptyBehavior(t *testing.T) {
	// Node with minimal required fields only
	node := Node{
		ID:     "node-1",
		Agent:  "test-agent",
		Prompt: "Generate code",
		// DependsOn, Timeout, Retry should be omitted when empty/nil
	}

	jsonData, err := json.Marshal(node)
	if err != nil {
		t.Fatalf("Failed to marshal node: %v", err)
	}

	jsonStr := string(jsonData)

	// Optional fields should be omitted when empty
	if strings.Contains(jsonStr, "depends_on") {
		t.Error("depends_on should be omitted when empty slice")
	}
	if strings.Contains(jsonStr, "timeout") {
		t.Error("timeout should be omitted when zero value")
	}
	if strings.Contains(jsonStr, "retry") {
		t.Error("retry should be omitted when nil")
	}

	// Required fields should be present
	if !strings.Contains(jsonStr, "id") {
		t.Error("id should be present")
	}
	if !strings.Contains(jsonStr, "agent") {
		t.Error("agent should be present")
	}
}

// Test that nil pointer fields serialize/deserialize correctly
func TestNilPointerHandling(t *testing.T) {
	// Test with nil retry config
	node := Node{
		ID:     "test-node",
		Agent:  "test-agent",
		Prompt: "test",
		Retry:  nil, // Important: nil pointer
	}

	// Serialize and deserialize
	jsonData, err := json.Marshal(node)
	if err != nil {
		t.Fatalf("Failed to marshal: %v", err)
	}

	var unmarshaled Node
	err = json.Unmarshal(jsonData, &unmarshaled)
	if err != nil {
		t.Fatalf("Failed to unmarshal: %v", err)
	}

	// Nil should remain nil
	if unmarshaled.Retry != nil {
		t.Error("Retry should remain nil after round-trip")
	}

	// Test with non-nil retry config
	retryConfig := &RetryConfig{Attempts: 3, Backoff: "exponential"}
	node.Retry = retryConfig

	jsonData, err = json.Marshal(node)
	if err != nil {
		t.Fatalf("Failed to marshal with retry: %v", err)
	}

	err = json.Unmarshal(jsonData, &unmarshaled)
	if err != nil {
		t.Fatalf("Failed to unmarshal with retry: %v", err)
	}

	// Non-nil should be preserved
	if unmarshaled.Retry == nil {
		t.Error("Retry should not be nil after round-trip")
	} else {
		if unmarshaled.Retry.Attempts != 3 {
			t.Errorf("Retry attempts mismatch: got %d, want 3", unmarshaled.Retry.Attempts)
		}
		if unmarshaled.Retry.Backoff != "exponential" {
			t.Errorf("Retry backoff mismatch: got %q, want %q", unmarshaled.Retry.Backoff, "exponential")
		}
	}
}

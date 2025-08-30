package engine

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/agentmaestro/agentmaestro/core/internal/storage"
)

func TestParserStorageIntegration(t *testing.T) {
	// Create test database
	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "test.db")

	db, err := storage.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Failed to open database: %v", err)
	}
	defer db.Close()

	// Parse our simple example workflow
	examplePath, err := filepath.Abs("../../../examples/simple.yaml")
	if err != nil {
		t.Fatalf("Failed to get absolute path: %v", err)
	}

	workflow, err := ParseWorkflowFromFile(examplePath)
	if err != nil {
		t.Fatalf("Failed to parse workflow: %v", err)
	}

	// Register the workflow with storage (this uses the existing file-based approach)
	err = db.RegisterWorkflow(examplePath)
	if err != nil {
		t.Fatalf("Failed to register workflow: %v", err)
	}

	// Verify we can load the workflow metadata
	meta, err := db.GetWorkflowMetadata(workflow.Name)
	if err != nil {
		t.Fatalf("Failed to get workflow metadata: %v", err)
	}

	if meta.Name != workflow.Name {
		t.Errorf("Expected metadata name %q, got %q", workflow.Name, meta.Name)
	}

	if meta.Description != workflow.Description {
		t.Errorf("Expected metadata description %q, got %q", workflow.Description, meta.Description)
	}

	// Verify we can load the YAML content back
	yamlContent, err := db.LoadWorkflowYAML(workflow.Name)
	if err != nil {
		t.Fatalf("Failed to load workflow YAML: %v", err)
	}

	// Parse the loaded YAML and verify it matches
	reloadedWorkflow, err := ParseWorkflow(yamlContent)
	if err != nil {
		t.Fatalf("Failed to parse reloaded workflow: %v", err)
	}

	if reloadedWorkflow.Name != workflow.Name {
		t.Errorf("Reloaded workflow name mismatch: expected %q, got %q", workflow.Name, reloadedWorkflow.Name)
	}

	if len(reloadedWorkflow.Nodes) != len(workflow.Nodes) {
		t.Errorf("Reloaded workflow node count mismatch: expected %d, got %d", len(workflow.Nodes), len(reloadedWorkflow.Nodes))
	}
}

func TestWorkflowUpdateIntegration(t *testing.T) {
	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "test.db")

	db, err := storage.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Failed to open database: %v", err)
	}
	defer db.Close()

	// Create a temporary workflow file
	workflowFile := filepath.Join(tempDir, "update-test.yaml")
	originalYAML := `name: "Update Test"
description: "Original description"
config:
  api_keys: "development"
nodes:
  - id: original_node
    agent: demo-agent
    prompt: "Original prompt"
    timeout: 120`

	// Write and register the original workflow
	err = os.WriteFile(workflowFile, []byte(originalYAML), 0644)
	if err != nil {
		t.Fatalf("Failed to write workflow file: %v", err)
	}

	err = db.RegisterWorkflow(workflowFile)
	if err != nil {
		t.Fatalf("Failed to register workflow: %v", err)
	}

	// Update the workflow with new content
	updatedYAML := `name: "Update Test"
description: "Updated description with more details"
config:
  api_keys: "development"
nodes:
  - id: updated_node
    agent: test-agent-2
    prompt: "Updated prompt with new functionality"
    timeout: 240
    retry:
      attempts: 2
      backoff: linear`

	// Validate the updated workflow before saving
	updatedWorkflow, err := ParseWorkflow([]byte(updatedYAML))
	if err != nil {
		t.Fatalf("Updated workflow failed validation: %v", err)
	}

	// Update through storage layer
	err = db.UpdateWorkflowFile("Update Test", []byte(updatedYAML))
	if err != nil {
		t.Fatalf("Failed to update workflow: %v", err)
	}

	// Verify the updated metadata
	meta, err := db.GetWorkflowMetadata("Update Test")
	if err != nil {
		t.Fatalf("Failed to get updated metadata: %v", err)
	}

	if meta.Description != updatedWorkflow.Description {
		t.Errorf("Expected updated description %q, got %q", updatedWorkflow.Description, meta.Description)
	}

	if meta.Version != 2 {
		t.Errorf("Expected version 2 after update, got %d", meta.Version)
	}

	// Load and parse the updated content
	loadedYAML, err := db.LoadWorkflowYAML("Update Test")
	if err != nil {
		t.Fatalf("Failed to load updated YAML: %v", err)
	}

	finalWorkflow, err := ParseWorkflow(loadedYAML)
	if err != nil {
		t.Fatalf("Failed to parse final workflow: %v", err)
	}

	// Verify the updates took effect
	if finalWorkflow.Description != "Updated description with more details" {
		t.Errorf("Expected updated description, got %q", finalWorkflow.Description)
	}

	// Note: OnError field removed - now uses hardcoded stop-all behavior

	if len(finalWorkflow.Nodes) != 1 {
		t.Errorf("Expected 1 node, got %d", len(finalWorkflow.Nodes))
	}

	node := finalWorkflow.Nodes[0]
	if node.ID != "updated_node" {
		t.Errorf("Expected updated node ID, got %q", node.ID)
	}

	if node.Agent != "test-agent-2" {
		t.Errorf("Expected updated agent, got %q", node.Agent)
	}

	if node.Retry == nil {
		t.Error("Expected retry config to be present")
	} else {
		if node.Retry.Attempts != 2 {
			t.Errorf("Expected 2 retry attempts, got %d", node.Retry.Attempts)
		}
		if node.Retry.Backoff != "linear" {
			t.Errorf("Expected linear backoff, got %q", node.Retry.Backoff)
		}
	}
}

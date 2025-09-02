package engine

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestParseWorkflow_ValidMinimal(t *testing.T) {
	yamlContent := `
name: "Simple Workflow"
description: "Basic workflow for testing"
config:
  api_keys: "development"
nodes:
  - id: analyze
    agent: demo-agent
    prompt: "Analyze the codebase"
    timeout: 300
`

	workflow, err := ParseWorkflow([]byte(yamlContent))
	if err != nil {
		t.Fatalf("Expected no error for valid YAML, got: %v", err)
	}

	if workflow.Name != "Simple Workflow" {
		t.Errorf("Expected name 'Simple Workflow', got %q", workflow.Name)
	}

	if len(workflow.Nodes) != 1 {
		t.Errorf("Expected 1 node, got %d", len(workflow.Nodes))
	}

	node := workflow.Nodes[0]
	if node.ID != "analyze" {
		t.Errorf("Expected node ID 'analyze', got %q", node.ID)
	}
	if node.Agent != "demo-agent" {
		t.Errorf("Expected agent 'demo-agent', got %q", node.Agent)
	}
	if node.Timeout != 300 {
		t.Errorf("Expected timeout 300, got %d", node.Timeout)
	}
}

func TestParseWorkflow_WithDependencies(t *testing.T) {
	yamlContent := `
name: "Parallel Processing"
description: "Workflow with parallel branches"
config:
  api_keys: "development"
nodes:
  - id: fetch_data
    agent: demo-agent
    prompt: "Fetch project data"
    timeout: 60
  - id: analyze_code
    agent: demo-agent
    depends_on: [fetch_data]
    prompt: "Analyze code quality"
    timeout: 120
  - id: analyze_docs
    agent: test-agent-2
    depends_on: [fetch_data]
    prompt: "Analyze documentation"
    retry:
      attempts: 3
      backoff: exponential
  - id: generate_report
    agent: demo-agent
    depends_on: [analyze_code, analyze_docs]
    prompt: "Generate final report"
`

	workflow, err := ParseWorkflow([]byte(yamlContent))
	if err != nil {
		t.Fatalf("Expected no error for valid complex YAML, got: %v", err)
	}

	if len(workflow.Nodes) != 4 {
		t.Errorf("Expected 4 nodes, got %d", len(workflow.Nodes))
	}

	// Check dependencies
	analyzeCode := findNode(workflow.Nodes, "analyze_code")
	if analyzeCode == nil {
		t.Fatal("Expected to find analyze_code node")
	}
	if len(analyzeCode.DependsOn) != 1 || analyzeCode.DependsOn[0] != "fetch_data" {
		t.Errorf("Expected analyze_code to depend on [fetch_data], got %v", analyzeCode.DependsOn)
	}

	// Check retry config
	analyzeDocs := findNode(workflow.Nodes, "analyze_docs")
	if analyzeDocs == nil {
		t.Fatal("Expected to find analyze_docs node")
	}
	if analyzeDocs.Retry == nil {
		t.Error("Expected analyze_docs to have retry config")
	} else {
		if analyzeDocs.Retry.Attempts != 3 {
			t.Errorf("Expected 3 retry attempts, got %d", analyzeDocs.Retry.Attempts)
		}
		if analyzeDocs.Retry.Backoff != "exponential" {
			t.Errorf("Expected exponential backoff, got %q", analyzeDocs.Retry.Backoff)
		}
	}
}

func TestValidateWorkflow_RequiredFields(t *testing.T) {
	tests := []struct {
		name     string
		yaml     string
		expected string // part of error message
	}{
		{
			name: "missing name",
			yaml: `
description: "Test"
nodes:
  - id: test
    agent: demo-agent
    prompt: "test"
`,
			expected: "name is required",
		},
		{
			name: "empty nodes",
			yaml: `
name: "Test"
nodes: []
`,
			expected: "at least one node is required",
		},
		{
			name: "node missing ID",
			yaml: `
name: "Test"
nodes:
  - agent: demo-agent
    prompt: "test"
`,
			expected: "node ID is required",
		},
		{
			name: "node missing agent",
			yaml: `
name: "Test"
nodes:
  - id: test
    prompt: "test"
`,
			expected: "agent is required",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			_, err := ParseWorkflow([]byte(tt.yaml))
			if err == nil {
				t.Errorf("Expected validation error for %s", tt.name)
				return
			}
			if !strings.Contains(err.Error(), tt.expected) {
				t.Errorf("Expected error containing %q, got: %v", tt.expected, err)
			}
		})
	}
}

func TestValidateWorkflow_InvalidValues(t *testing.T) {
	tests := []struct {
		name     string
		yaml     string
		expected string
	}{
		{
			name: "duplicate node IDs",
			yaml: `
name: "Test"
nodes:
  - id: duplicate
    agent: demo-agent
    prompt: "test1"
  - id: duplicate
    agent: test-agent-2
    prompt: "test2"
`,
			expected: "duplicate node ID",
		},
		{
			name: "timeout too large",
			yaml: `
name: "Test"
nodes:
  - id: test
    agent: demo-agent
    prompt: "test"
    timeout: 5000
`,
			expected: "timeout must be",
		},
		{
			name: "invalid retry attempts",
			yaml: `
name: "Test"
nodes:
  - id: test
    agent: demo-agent
    prompt: "test"
    retry:
      attempts: 20
      backoff: linear
`,
			expected: "retry attempts must be",
		},
		{
			name: "invalid retry backoff",
			yaml: `
name: "Test"
nodes:
  - id: test
    agent: demo-agent
    prompt: "test"
    retry:
      attempts: 3
      backoff: invalid
`,
			expected: "retry backoff must be",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			_, err := ParseWorkflow([]byte(tt.yaml))
			if err == nil {
				t.Errorf("Expected validation error for %s", tt.name)
				return
			}
			if !strings.Contains(err.Error(), tt.expected) {
				t.Errorf("Expected error containing %q, got: %v", tt.expected, err)
			}
		})
	}
}

func TestParseWorkflowFromFile(t *testing.T) {
	tempDir := t.TempDir()
	testFile := filepath.Join(tempDir, "test-workflow.yaml")

	yamlContent := `
name: "File-based Workflow"
description: "Testing file parsing"
config:
  api_keys: "development"
nodes:
  - id: process
    agent: demo-agent
    prompt: "Process the file"
    timeout: 180
`

	err := os.WriteFile(testFile, []byte(yamlContent), 0644)
	if err != nil {
		t.Fatalf("Failed to write test file: %v", err)
	}

	workflow, err := ParseWorkflowFromFile(testFile)
	if err != nil {
		t.Fatalf("Expected no error parsing from file, got: %v", err)
	}

	if workflow.Name != "File-based Workflow" {
		t.Errorf("Expected name 'File-based Workflow', got %q", workflow.Name)
	}

	if len(workflow.Nodes) != 1 {
		t.Errorf("Expected 1 node, got %d", len(workflow.Nodes))
	}
}

func TestParseWorkflow_InvalidYAML(t *testing.T) {
	invalidYAML := `
name: "Test"
nodes:
  - id: test
    agent: demo-agent
    prompt: "test
    # Missing closing quote
`

	_, err := ParseWorkflow([]byte(invalidYAML))
	if err == nil {
		t.Error("Expected error for invalid YAML syntax")
	}
	if !strings.Contains(err.Error(), "invalid YAML") {
		t.Errorf("Expected 'invalid YAML' in error message, got: %v", err)
	}
}

func TestParseWorkflowFromFile_NonExistent(t *testing.T) {
	_, err := ParseWorkflowFromFile("/non/existent/file.yaml")
	if err == nil {
		t.Error("Expected error for non-existent file")
	}
	if !strings.Contains(err.Error(), "failed to read file") {
		t.Errorf("Expected 'failed to read file' in error message, got: %v", err)
	}
}

func findNode(nodes []Node, id string) *Node {
	for i := range nodes {
		if nodes[i].ID == id {
			return &nodes[i]
		}
	}
	return nil
}

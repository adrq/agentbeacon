package engine

import (
	"fmt"
	"os"
	"regexp"
	"strings"

	"gopkg.in/yaml.v3"
)

func ParseWorkflow(yamlContent []byte) (*Workflow, error) {
	var w Workflow
	if err := yaml.Unmarshal(yamlContent, &w); err != nil {
		return nil, fmt.Errorf("invalid YAML: %w", err)
	}

	if err := ValidateWorkflow(&w); err != nil {
		return nil, err
	}

	return &w, nil
}

func ParseWorkflowFromFile(filePath string) (*Workflow, error) {
	content, err := os.ReadFile(filePath)
	if err != nil {
		return nil, fmt.Errorf("failed to read file: %w", err)
	}
	return ParseWorkflow(content)
}

func ValidateWorkflow(w *Workflow) error {
	// Check required workflow fields
	if strings.TrimSpace(w.Name) == "" {
		return ValidationError{Field: "name", Message: "name is required"}
	}

	if len(w.Nodes) == 0 {
		return ValidationError{Field: "nodes", Message: "at least one node is required"}
	}

	// Note: on_error field removed in favor of hardcoded stop-all behavior

	// Track node IDs for uniqueness check
	nodeIDs := make(map[string]bool)

	// Validate each node
	for i, node := range w.Nodes {
		if err := ValidateNode(&node); err != nil {
			return fmt.Errorf("node %d: %w", i, err)
		}

		// Check for duplicate node IDs
		if nodeIDs[node.ID] {
			return ValidationError{Field: "nodes", Message: fmt.Sprintf("duplicate node ID: %s", node.ID)}
		}
		nodeIDs[node.ID] = true
	}

	// Validate dependencies reference existing nodes
	for _, node := range w.Nodes {
		for _, depID := range node.DependsOn {
			if !nodeIDs[depID] {
				return ValidationError{Field: "nodes", Message: fmt.Sprintf("node %s depends on non-existent node: %s", node.ID, depID)}
			}
		}
	}

	// Validate DAG structure (no cycles)
	if err := ValidateDAG(w.Nodes); err != nil {
		return err
	}

	return nil
}

func ValidateNode(node *Node) error {
	// Required fields
	if strings.TrimSpace(node.ID) == "" {
		return ValidationError{Field: "id", Message: "node ID is required"}
	}

	// Validate node ID format (alphanumeric + underscore only)
	validIDRegex := regexp.MustCompile(`^[a-zA-Z0-9_]+$`)
	if !validIDRegex.MatchString(node.ID) {
		return ValidationError{Field: "id", Message: "node ID must contain only letters, numbers, and underscores"}
	}

	if strings.TrimSpace(node.Agent) == "" {
		return ValidationError{Field: "agent", Message: "agent is required"}
	}

	// Validate timeout range (0-3600 seconds)
	if node.Timeout < 0 || node.Timeout > 3600 {
		return ValidationError{Field: "timeout", Message: "timeout must be between 0 and 3600 seconds"}
	}

	// Validate retry configuration if present
	if node.Retry != nil {
		if node.Retry.Attempts < 1 || node.Retry.Attempts > 3 {
			return ValidationError{Field: "retry.attempts", Message: "retry attempts must be between 1 and 3"}
		}
		if node.Retry.Backoff != "" && node.Retry.Backoff != "linear" && node.Retry.Backoff != "exponential" {
			return ValidationError{Field: "retry.backoff", Message: "retry backoff must be 'linear' or 'exponential'"}
		}
	}

	return nil
}

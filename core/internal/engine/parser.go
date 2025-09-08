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

	if len(w.Tasks) == 0 {
		return ValidationError{Field: "tasks", Message: "at least one task is required"}
	}

	// Note: on_error field removed in favor of hardcoded stop-all behavior

	// Track node IDs for uniqueness check
	nodeIDs := make(map[string]bool)

	// Validate each task
	for i, task := range w.Tasks {
		if err := ValidateNode(&task); err != nil {
			return fmt.Errorf("task %d: %w", i, err)
		}

		// Check for duplicate task IDs
		if nodeIDs[task.ID] {
			return ValidationError{Field: "tasks", Message: fmt.Sprintf("duplicate task ID: %s", task.ID)}
		}
		nodeIDs[task.ID] = true
	}

	// Validate dependencies reference existing tasks
	for _, task := range w.Tasks {
		for _, depID := range task.DependsOn {
			if !nodeIDs[depID] {
				return ValidationError{Field: "tasks", Message: fmt.Sprintf("task %s depends on non-existent task: %s", task.ID, depID)}
			}
		}
	}

	// Validate DAG structure (no cycles)
	if err := ValidateDAG(w.Tasks); err != nil {
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

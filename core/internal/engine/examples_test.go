package engine

import (
	"path/filepath"
	"testing"
)

func TestExampleWorkflows(t *testing.T) {
	examples := []struct {
		file          string
		expectedName  string
		expectedNodes int
	}{
		{
			file:          "../../../examples/simple.yaml",
			expectedName:  "Simple Pipeline",
			expectedNodes: 2,
		},
		{
			file:          "../../../examples/parallel.yaml",
			expectedName:  "Parallel Processing",
			expectedNodes: 4,
		},
	}

	for _, example := range examples {
		t.Run(example.file, func(t *testing.T) {
			absPath, err := filepath.Abs(example.file)
			if err != nil {
				t.Fatalf("Failed to get absolute path: %v", err)
			}

			workflow, err := ParseWorkflowFromFile(absPath)
			if err != nil {
				t.Fatalf("Failed to parse example workflow %s: %v", example.file, err)
			}

			if workflow.Name != example.expectedName {
				t.Errorf("Expected name %q, got %q", example.expectedName, workflow.Name)
			}

			if len(workflow.Tasks) != example.expectedNodes {
				t.Errorf("Expected %d tasks, got %d", example.expectedNodes, len(workflow.Tasks))
			}

			// Verify the workflow passes validation
			if err := ValidateWorkflow(workflow); err != nil {
				t.Errorf("Example workflow failed validation: %v", err)
			}
		})
	}
}

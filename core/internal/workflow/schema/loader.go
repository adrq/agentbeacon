package schema

import (
	"bytes"
	"fmt"
	"sync"

	docschemas "github.com/agentmaestro/agentmaestro/docs"
	jsonschema "github.com/santhosh-tekuri/jsonschema/v6"
)

const (
	workflowSchemaURI = "https://schemas.agentmaestro.dev/workflow-schema.json"
	a2aSchemaURI      = "https://schemas.agentmaestro.dev/a2a/task-schema.json"
)

var (
	compileOnce sync.Once
	compileErr  error
	compiled    *jsonschema.Schema
)

// Schema returns the compiled JSON Schema used to validate workflow documents.
func Schema() (*jsonschema.Schema, error) {
	compileOnce.Do(func() {
		compiled, compileErr = compileWorkflowSchema()
	})

	return compiled, compileErr
}

// ValidateDocument validates an arbitrary workflow document against the compiled schema.
func ValidateDocument(document interface{}) error {
	schema, err := Schema()
	if err != nil {
		return err
	}

	if err := schema.Validate(document); err != nil {
		return fmt.Errorf("workflow document failed schema validation: %w", err)
	}

	if err := ensureArtifactContract(document); err != nil {
		return fmt.Errorf("workflow document failed artifact integrity checks: %w", err)
	}

	return nil
}

func compileWorkflowSchema() (*jsonschema.Schema, error) {
	compiler := jsonschema.NewCompiler()

	if err := addResource(compiler, workflowSchemaURI, docschemas.WorkflowSchema); err != nil {
		return nil, err
	}

	if err := addResource(compiler, a2aSchemaURI, docschemas.A2ATaskSchema); err != nil {
		return nil, err
	}

	schema, err := compiler.Compile(workflowSchemaURI)
	if err != nil {
		return nil, fmt.Errorf("compile workflow schema: %w", err)
	}

	return schema, nil
}

func addResource(compiler *jsonschema.Compiler, uri string, data []byte) error {
	if err := compiler.AddResource(uri, bytes.NewReader(data)); err != nil {
		return fmt.Errorf("register JSON schema %s: %w", uri, err)
	}

	return nil
}

func ensureArtifactContract(document interface{}) error {
	root, ok := document.(map[string]interface{})
	if !ok {
		return fmt.Errorf("expected workflow document to be an object")
	}

	rawTasks, ok := root["tasks"].([]interface{})
	if !ok {
		return fmt.Errorf("workflow document must define a tasks array")
	}

	taskIDs := make(map[string]struct{}, len(rawTasks))
	artifactProducers := make(map[string]string)
	taskDependencies := make(map[string]map[string]struct{})

	for idx, rawTask := range rawTasks {
		task, ok := rawTask.(map[string]interface{})
		if !ok {
			return fmt.Errorf("tasks[%d] must be an object", idx)
		}

		rawID, ok := task["id"].(string)
		if !ok || rawID == "" {
			return fmt.Errorf("tasks[%d] missing string id", idx)
		}

		if _, exists := taskIDs[rawID]; exists {
			return fmt.Errorf("duplicate task id %q", rawID)
		}
		taskIDs[rawID] = struct{}{}

		dependencies := make(map[string]struct{})
		if rawDepends, ok := task["depends_on"]; ok && rawDepends != nil {
			slice, ok := rawDepends.([]interface{})
			if !ok {
				return fmt.Errorf("tasks[%d].depends_on must be an array", idx)
			}
			for depIdx, depVal := range slice {
				depID, ok := depVal.(string)
				if !ok || depID == "" {
					return fmt.Errorf("tasks[%d].depends_on[%d] must be a non-empty string", idx, depIdx)
				}
				dependencies[depID] = struct{}{}
			}
		}
		taskDependencies[rawID] = dependencies

		taskBlock, _ := task["task"].(map[string]interface{})
		if taskBlock != nil {
			rawArtifacts, hasArtifacts := taskBlock["artifacts"]
			if hasArtifacts && rawArtifacts != nil {
				slice, ok := rawArtifacts.([]interface{})
				if !ok {
					return fmt.Errorf("tasks[%d].task.artifacts must be an array", idx)
				}
				for artIdx, rawArtifact := range slice {
					artifact, ok := rawArtifact.(map[string]interface{})
					if !ok {
						return fmt.Errorf("tasks[%d].task.artifacts[%d] must be an object", idx, artIdx)
					}

					rawArtifactID, ok := artifact["artifactId"].(string)
					if !ok || rawArtifactID == "" {
						return fmt.Errorf("tasks[%d].task.artifacts[%d] missing string artifactId", idx, artIdx)
					}

					if producer, exists := artifactProducers[rawArtifactID]; exists {
						return fmt.Errorf("artifact id %q declared by both %s and %s", rawArtifactID, producer, rawID)
					}
					artifactProducers[rawArtifactID] = rawID
				}
			}
		}
	}

	for idx, rawTask := range rawTasks {
		task := rawTask.(map[string]interface{})
		taskID := task["id"].(string)

		inputs, ok := task["inputs"]
		if !ok || inputs == nil {
			continue
		}

		inputsObj, ok := inputs.(map[string]interface{})
		if !ok {
			return fmt.Errorf("tasks[%d].inputs must be an object", idx)
		}

		rawArtifacts, ok := inputsObj["artifacts"]
		if !ok || rawArtifacts == nil {
			continue
		}

		artifactSlice, ok := rawArtifacts.([]interface{})
		if !ok {
			return fmt.Errorf("tasks[%d].inputs.artifacts must be an array", idx)
		}

		for refIdx, rawRef := range artifactSlice {
			ref, ok := rawRef.(map[string]interface{})
			if !ok {
				return fmt.Errorf("tasks[%d].inputs.artifacts[%d] must be an object", idx, refIdx)
			}

			fromTask, ok := ref["from"].(string)
			if !ok || fromTask == "" {
				return fmt.Errorf("tasks[%d].inputs.artifacts[%d] missing from", idx, refIdx)
			}

			if _, exists := taskIDs[fromTask]; !exists {
				return fmt.Errorf("tasks[%d].inputs.artifacts[%d] references unknown task %q", idx, refIdx, fromTask)
			}

			artifactID, ok := ref["artifactId"].(string)
			if !ok || artifactID == "" {
				return fmt.Errorf("tasks[%d].inputs.artifacts[%d] missing artifactId", idx, refIdx)
			}

			producer, exists := artifactProducers[artifactID]
			if !exists {
				return fmt.Errorf("tasks[%d] expects artifact %q but no task declares it", idx, artifactID)
			}

			if producer != fromTask {
				return fmt.Errorf("tasks[%d] expects artifact %q from task %q but it is produced by %q", idx, artifactID, fromTask, producer)
			}

			if fromTask == taskID {
				return fmt.Errorf("tasks[%d] lists its own artifact %q as an input", idx, artifactID)
			}

			if _, ok := taskDependencies[taskID][fromTask]; !ok {
				return fmt.Errorf("tasks[%d] must declare depends_on: %s when consuming artifact %q", idx, fromTask, artifactID)
			}
		}
	}

	return nil
}

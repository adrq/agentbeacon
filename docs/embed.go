package docschemas

import _ "embed"

var (
	//go:embed workflow-schema.json
	WorkflowSchema []byte

	//go:embed a2a-task.schema.json
	A2ATaskSchema []byte
)

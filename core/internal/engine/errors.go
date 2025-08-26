package engine

import "fmt"

// ValidationError represents a validation error with field context
type ValidationError struct {
	Field   string
	Message string
}

// Error implements the error interface for ValidationError
func (e ValidationError) Error() string {
	return fmt.Sprintf("validation error: field '%s' - %s", e.Field, e.Message)
}

// DAGError represents an error in DAG processing
type DAGError struct {
	Type    string
	Message string
}

// Error implements the error interface for DAGError
func (e DAGError) Error() string {
	return fmt.Sprintf("DAG error (%s): %s", e.Type, e.Message)
}

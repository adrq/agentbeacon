package constants

// A2A Task State Constants
// These constants match the A2A Protocol v0.3.0 specification
const (
	TaskStateSubmitted     = "submitted"
	TaskStateWorking       = "working"
	TaskStateInputRequired = "input-required"
	TaskStateCompleted     = "completed"
	TaskStateCanceled      = "canceled"
	TaskStateFailed        = "failed"
	TaskStateRejected      = "rejected"
	TaskStateAuthRequired  = "auth-required"
	TaskStateUnknown       = "unknown"
)

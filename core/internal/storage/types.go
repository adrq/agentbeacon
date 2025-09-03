package storage

import (
	"time"

	"gorm.io/datatypes"
)

type Config struct {
	ID            string         `gorm:"primaryKey;type:varchar(255)" db:"id"`
	Name          string         `gorm:"uniqueIndex:idx_config_name;type:varchar(255);not null" db:"name"`
	APIKeys       datatypes.JSON `gorm:"type:jsonb;not null;serializer:json" db:"api_keys"`
	AgentSettings datatypes.JSON `gorm:"type:jsonb;not null;serializer:json" db:"agent_settings"`
	CreatedAt     time.Time      `gorm:"autoCreateTime" db:"created_at"`
	UpdatedAt     time.Time      `gorm:"autoUpdateTime" db:"updated_at"`
}

type WorkflowMeta struct {
	Name        string    `gorm:"primaryKey;type:varchar(255)" db:"name"`
	FilePath    string    `gorm:"type:varchar(500);not null" db:"file_path"`
	Description string    `gorm:"type:text" db:"description"`
	Version     int       `gorm:"type:integer;default:1" db:"version"`
	CreatedAt   time.Time `gorm:"autoCreateTime" db:"created_at"`
	UpdatedAt   time.Time `gorm:"autoUpdateTime" db:"updated_at"`
}

func (WorkflowMeta) TableName() string {
	return "workflow"
}

type Execution struct {
	ID           string         `gorm:"primaryKey;type:varchar(255)" db:"id"`
	WorkflowName string         `gorm:"type:varchar(255);not null;constraint:OnUpdate:CASCADE,OnDelete:CASCADE" db:"workflow_name"`
	Status       string         `gorm:"type:varchar(100);not null" db:"status"`
	NodeStates   datatypes.JSON `gorm:"type:jsonb;not null;serializer:json" db:"node_states"`
	A2ATasks     datatypes.JSON `gorm:"type:jsonb;serializer:json" db:"a2_a_tasks"`
	ACPSessions  datatypes.JSON `gorm:"type:jsonb;serializer:json" db:"acp_sessions"`
	Logs         string         `gorm:"type:text" db:"logs"`
	StartedAt    time.Time      `gorm:"autoCreateTime" db:"started_at"`
	CompletedAt  *time.Time     `gorm:"" db:"completed_at"`
	// New versioned workflow linkage (MVP registry). Nullable until old workflow path removed.
	WorkflowNamespace *string `gorm:"type:varchar(64)" db:"workflow_namespace"`
	WorkflowVersion   *string `gorm:"type:varchar(64)" db:"workflow_version"`
}

// WorkflowVersion represents an immutable workflow definition version.
// Kept alongside legacy WorkflowMeta during incremental migration.
type WorkflowVersion struct {
	Namespace    string    `gorm:"primaryKey;type:varchar(64)" db:"namespace"`
	Name         string    `gorm:"primaryKey;type:varchar(64)" db:"name"`
	Version      string    `gorm:"primaryKey;type:varchar(64)" db:"version"`
	IsLatest     bool      `gorm:"type:boolean;not null;default:false" db:"is_latest"`
	Description  string    `gorm:"type:text" db:"description"`
	ContentHash  string    `gorm:"type:char(64);not null" db:"content_hash"`
	YAMLSnapshot string    `gorm:"type:text;not null" db:"yaml_snapshot"`
	GitRepo      *string   `gorm:"type:text" db:"git_repo"`
	GitPath      *string   `gorm:"type:text" db:"git_path"`
	GitCommit    *string   `gorm:"type:char(40)" db:"git_commit"`
	GitBranch    *string   `gorm:"type:varchar(64)" db:"git_branch"`
	CreatedAt    time.Time `gorm:"autoCreateTime" db:"created_at"`
}

func (WorkflowVersion) TableName() string { return "workflow_version" }

// Event types - Using A2A states as canonical model
const (
	// State transitions (maps directly to A2A task states)
	EventTypeStateChange   = "state_change"
	EventTypeSubmitted     = "submitted"
	EventTypeWorking       = "working"
	EventTypeInputRequired = "input_required"
	EventTypeCompleted     = "completed"
	EventTypeFailed        = "failed"
	EventTypeCanceled      = "canceled"

	// Progress events
	EventTypeProgress   = "progress"
	EventTypeOutput     = "output"
	EventTypePlanUpdate = "plan_update" // ACP plans
	EventTypeArtifact   = "artifact"

	// System events
	EventTypeRetry   = "retry"
	EventTypeTimeout = "timeout"
	EventTypeError   = "error"
)

// Event sources
const (
	EventSourceA2A    = "a2a"
	EventSourceACP    = "acp"
	EventSourceSystem = "system"
)

// ExecutionEvent represents a single event in workflow execution
// Auto-increment ID provides ordering - no sequence field needed
type ExecutionEvent struct {
	// Primary key provides natural ordering
	ID int64 `gorm:"primaryKey;autoIncrement" db:"id"`

	// Event identification
	ExecutionID string    `gorm:"type:varchar(255);not null;index:idx_exec_id" db:"execution_id"`
	NodeID      string    `gorm:"type:varchar(255);not null" db:"node_id"`
	Timestamp   time.Time `gorm:"not null" db:"timestamp"`

	// Event classification
	Type   string `gorm:"type:varchar(50);not null;index" db:"type"`
	Source string `gorm:"type:varchar(20);not null" db:"source"` // "a2a", "acp", "system"

	// State tracking (nullable - not all events are state changes)
	State     *string `gorm:"type:varchar(50)" db:"state"`      // Current A2A state
	PrevState *string `gorm:"type:varchar(50)" db:"prev_state"` // Previous state

	// Event content
	Message string         `gorm:"type:text" db:"message"` // Human-readable
	Data    datatypes.JSON `gorm:"type:jsonb" db:"data"`   // Structured data
	Raw     datatypes.JSON `gorm:"type:jsonb" db:"raw"`    // Original protocol payload
}

func (ExecutionEvent) TableName() string {
	return "execution_events"
}

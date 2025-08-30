package engine

import "time"

// Workflow represents a complete workflow definition
type Workflow struct {
	ID          string         `json:"id" yaml:"-"`
	Name        string         `json:"name" yaml:"name"`
	Description string         `json:"description" yaml:"description"`
	Config      WorkflowConfig `json:"config" yaml:"config"`
	Nodes       []Node         `json:"nodes" yaml:"nodes"`
	CreatedAt   time.Time      `json:"created_at" yaml:"-"`
	UpdatedAt   time.Time      `json:"updated_at" yaml:"-"`
}

// Node represents a single node in a workflow
type Node struct {
	ID        string       `json:"id" yaml:"id"`
	Agent     string       `json:"agent" yaml:"agent"`
	AgentURL  string       `json:"agent_url,omitempty" yaml:"agent_url,omitempty"`
	Prompt    string       `json:"prompt" yaml:"prompt"`
	DependsOn []string     `json:"depends_on,omitempty" yaml:"depends_on,omitempty"`
	Timeout   int          `json:"timeout,omitempty" yaml:"timeout,omitempty"`
	Retry     *RetryConfig `json:"retry,omitempty" yaml:"retry,omitempty"`
}

// WorkflowConfig contains configuration references for workflows
type WorkflowConfig struct {
	APIKeys string `json:"api_keys" yaml:"api_keys"`
}

// RetryConfig defines retry behavior for nodes
type RetryConfig struct {
	Attempts int    `json:"attempts" yaml:"attempts"`
	Backoff  string `json:"backoff,omitempty" yaml:"backoff,omitempty"`
}

// Execution represents a workflow execution instance
type Execution struct {
	ID          string               `json:"id"`
	WorkflowID  string               `json:"workflow_id"`
	Status      string               `json:"status"`
	NodeStates  map[string]NodeState `json:"node_states"`
	StartedAt   time.Time            `json:"started_at"`
	CompletedAt *time.Time           `json:"completed_at"`
}

// NodeState represents the execution state of a single node
type NodeState struct {
	Status       string     `json:"status"`
	Output       string     `json:"output"`
	Error        string     `json:"error"`
	StartedAt    time.Time  `json:"started_at"`
	EndedAt      *time.Time `json:"ended_at"`
	AttemptCount int        `json:"attempt_count"`
	ErrorHistory []string   `json:"error_history,omitempty"`
	LastRetryAt  *time.Time `json:"last_retry_at,omitempty"`
}

// Config represents system-wide configuration
type Config struct {
	ID            string                 `json:"id"`
	Name          string                 `json:"name"`
	APIKeys       map[string]string      `json:"api_keys"`
	AgentSettings map[string]interface{} `json:"agent_settings"`
	CreatedAt     time.Time              `json:"created_at"`
	UpdatedAt     time.Time              `json:"updated_at"`
}

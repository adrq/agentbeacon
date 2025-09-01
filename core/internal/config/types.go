package config

// AgentType represents the type of agent protocol
type AgentType string

const (
	AgentTypeStdio AgentType = "stdio" // Simple stdin/stdout communication
	AgentTypeA2A   AgentType = "a2a"   // HTTP-based A2A protocol agents
	AgentTypeACP   AgentType = "acp"   // JSON-RPC over stdio (future implementation)
)

// AgentConfig defines configuration for a named agent instance
type AgentConfig struct {
	Type   string                 `yaml:"type"`   // Agent type: "stdio", "a2a", "acp"
	Config map[string]interface{} `yaml:"config"` // Type-specific configuration
}

// AgentsFile represents the structure of agents.yaml configuration file
type AgentsFile struct {
	Agents map[string]AgentConfig `yaml:"agents"` // agent name -> config
}

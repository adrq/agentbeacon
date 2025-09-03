package config

import (
	"fmt"
	"os"
	"sort"
	"strings"
	"sync"

	"gopkg.in/yaml.v3"
)

// ConfigLoader handles loading and caching of agent configurations
type ConfigLoader struct {
	filePath string
	cache    *AgentsFile
	mutex    sync.RWMutex
}

// NewConfigLoader creates a new ConfigLoader with the specified file path
// If filePath is empty, uses the default "examples/agents.yaml"
func NewConfigLoader(filePath string) *ConfigLoader {
	if filePath == "" {
		filePath = "examples/agents.yaml"
	}
	return &ConfigLoader{
		filePath: filePath,
	}
}

// LoadAgents loads the agents configuration from the file system
// Uses os.ExpandEnv to resolve environment variables in the YAML content
func (cl *ConfigLoader) LoadAgents() (*AgentsFile, error) {
	cl.mutex.Lock()
	defer cl.mutex.Unlock()

	// Return cached version if already loaded
	if cl.cache != nil {
		return cl.cache, nil
	}

	// Read the file
	content, err := os.ReadFile(cl.filePath)
	if err != nil {
		return nil, fmt.Errorf("failed to read agents configuration from %s: %w", cl.filePath, err)
	}

	// Expand environment variables using os.ExpandEnv
	expanded := os.ExpandEnv(string(content))

	// Parse YAML
	var agents AgentsFile
	if err := yaml.Unmarshal([]byte(expanded), &agents); err != nil {
		return nil, fmt.Errorf("failed to parse agents.yaml: %w", err)
	}

	// Cache the loaded configuration
	cl.cache = &agents
	return cl.cache, nil
}

// GetAgentConfig retrieves configuration for a specific agent by name
// Returns an error if the agent is not found, including suggestions of available agents
func (cl *ConfigLoader) GetAgentConfig(name string) (*AgentConfig, error) {
	agents, err := cl.LoadAgents()
	if err != nil {
		return nil, fmt.Errorf("failed to load agents configuration: %w", err)
	}

	config, exists := agents.Agents[name]
	if !exists {
		// Build list of available agents for error message
		var availableAgents []string
		for agentName := range agents.Agents {
			availableAgents = append(availableAgents, agentName)
		}
		// Ensure deterministic ordering for error message
		sort.Strings(availableAgents)

		if len(availableAgents) == 0 {
			return nil, fmt.Errorf("agent '%s' not found and no agents are configured", name)
		}

		return nil, fmt.Errorf("agent '%s' not found. Available agents: %s", name, strings.Join(availableAgents, ", "))
	}

	return &config, nil
}

// GetAvailableAgents returns a list of all configured agent names
func (cl *ConfigLoader) GetAvailableAgents() ([]string, error) {
	agents, err := cl.LoadAgents()
	if err != nil {
		return nil, fmt.Errorf("failed to load agents configuration: %w", err)
	}

	var names []string
	for name := range agents.Agents {
		names = append(names, name)
	}
	return names, nil
}

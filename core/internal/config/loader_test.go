package config

import (
	"os"
	"path/filepath"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestConfigLoader_LoadAgents_Success(t *testing.T) {
	// Create a temporary agents.yaml file
	tempDir := t.TempDir()
	agentsFile := filepath.Join(tempDir, "agents.yaml")

	yamlContent := `agents:
  mock-agent:
    type: stdio
    config:
      command: "./bin/mock-agent"
  test-agent:
    type: a2a
    config:
      url: "http://localhost:8080"
      timeout: 300`

	err := os.WriteFile(agentsFile, []byte(yamlContent), 0644)
	require.NoError(t, err)

	// Test loading
	loader := NewConfigLoader(agentsFile)
	agents, err := loader.LoadAgents()

	require.NoError(t, err)
	require.NotNil(t, agents)
	assert.Len(t, agents.Agents, 2)

	// Check mock-agent config
	mockAgent, exists := agents.Agents["mock-agent"]
	assert.True(t, exists)
	assert.Equal(t, "stdio", mockAgent.Type)
	assert.Equal(t, "./bin/mock-agent", mockAgent.Config["command"])

	// Check test-agent config
	testAgent, exists := agents.Agents["test-agent"]
	assert.True(t, exists)
	assert.Equal(t, "a2a", testAgent.Type)
	assert.Equal(t, "http://localhost:8080", testAgent.Config["url"])
}

func TestConfigLoader_LoadAgents_WithEnvironmentVariables(t *testing.T) {
	// Set test environment variables
	originalAPIKey := os.Getenv("TEST_API_KEY")
	originalTimeout := os.Getenv("TEST_TIMEOUT")

	os.Setenv("TEST_API_KEY", "secret-key-123")
	os.Setenv("TEST_TIMEOUT", "600")

	// Clean up after test
	defer func() {
		if originalAPIKey == "" {
			os.Unsetenv("TEST_API_KEY")
		} else {
			os.Setenv("TEST_API_KEY", originalAPIKey)
		}
		if originalTimeout == "" {
			os.Unsetenv("TEST_TIMEOUT")
		} else {
			os.Setenv("TEST_TIMEOUT", originalTimeout)
		}
	}()

	// Create temporary agents.yaml with environment variables
	tempDir := t.TempDir()
	agentsFile := filepath.Join(tempDir, "agents.yaml")

	yamlContent := `agents:
  env-agent:
    type: a2a
    config:
      url: "http://localhost:8080"
      api_key: "${TEST_API_KEY}"
      timeout: ${TEST_TIMEOUT}`

	err := os.WriteFile(agentsFile, []byte(yamlContent), 0644)
	require.NoError(t, err)

	// Test loading with environment variable resolution
	loader := NewConfigLoader(agentsFile)
	agents, err := loader.LoadAgents()

	require.NoError(t, err)
	require.NotNil(t, agents)

	envAgent, exists := agents.Agents["env-agent"]
	assert.True(t, exists)
	assert.Equal(t, "secret-key-123", envAgent.Config["api_key"])
	assert.Equal(t, 600, envAgent.Config["timeout"]) // YAML parses numeric values as integers
}

func TestConfigLoader_LoadAgents_MissingEnvironmentVariable(t *testing.T) {
	// Ensure the test env var is not set
	os.Unsetenv("MISSING_VAR")

	// Create temporary agents.yaml with missing environment variable
	tempDir := t.TempDir()
	agentsFile := filepath.Join(tempDir, "agents.yaml")

	yamlContent := `agents:
  env-agent:
    type: a2a
    config:
      url: "http://localhost:8080"
      api_key: "${MISSING_VAR}"`

	err := os.WriteFile(agentsFile, []byte(yamlContent), 0644)
	require.NoError(t, err)

	// Test loading - os.ExpandEnv replaces with empty string
	loader := NewConfigLoader(agentsFile)
	agents, err := loader.LoadAgents()

	require.NoError(t, err)
	require.NotNil(t, agents)

	envAgent, exists := agents.Agents["env-agent"]
	assert.True(t, exists)
	assert.Equal(t, "", envAgent.Config["api_key"]) // Missing var becomes empty string
}

func TestConfigLoader_GetAgentConfig_Success(t *testing.T) {
	// Create temporary agents.yaml
	tempDir := t.TempDir()
	agentsFile := filepath.Join(tempDir, "agents.yaml")

	yamlContent := `agents:
  mock-agent:
    type: stdio
    config:
      command: "./bin/mock-agent"`

	err := os.WriteFile(agentsFile, []byte(yamlContent), 0644)
	require.NoError(t, err)

	// Test getting agent config
	loader := NewConfigLoader(agentsFile)
	config, err := loader.GetAgentConfig("mock-agent")

	require.NoError(t, err)
	require.NotNil(t, config)
	assert.Equal(t, "stdio", config.Type)
	assert.Equal(t, "./bin/mock-agent", config.Config["command"])
}

func TestConfigLoader_GetAgentConfig_NotFound(t *testing.T) {
	// Create temporary agents.yaml
	tempDir := t.TempDir()
	agentsFile := filepath.Join(tempDir, "agents.yaml")

	yamlContent := `agents:
  mock-agent:
    type: stdio
    config:
      command: "./bin/mock-agent"
  other-agent:
    type: a2a
    config:
      url: "http://localhost:8080"`

	err := os.WriteFile(agentsFile, []byte(yamlContent), 0644)
	require.NoError(t, err)

	// Test getting non-existent agent config
	loader := NewConfigLoader(agentsFile)
	config, err := loader.GetAgentConfig("nonexistent-agent")

	require.Error(t, err)
	assert.Nil(t, config)
	assert.Contains(t, err.Error(), "agent 'nonexistent-agent' not found")
	assert.Contains(t, err.Error(), "Available agents: mock-agent, other-agent")
}

func TestConfigLoader_LoadAgents_FileNotFound(t *testing.T) {
	loader := NewConfigLoader("/nonexistent/path/agents.yaml")
	agents, err := loader.LoadAgents()

	require.Error(t, err)
	assert.Nil(t, agents)
	assert.Contains(t, err.Error(), "failed to read agents configuration")
	assert.Contains(t, err.Error(), "/nonexistent/path/agents.yaml")
}

func TestConfigLoader_LoadAgents_InvalidYAML(t *testing.T) {
	// Create temporary file with invalid YAML
	tempDir := t.TempDir()
	agentsFile := filepath.Join(tempDir, "agents.yaml")

	invalidYaml := `agents:
  mock-agent:
    type: stdio
    config:
      command: "./bin/mock-agent"
    invalid_yaml: [unclosed bracket`

	err := os.WriteFile(agentsFile, []byte(invalidYaml), 0644)
	require.NoError(t, err)

	// Test loading invalid YAML
	loader := NewConfigLoader(agentsFile)
	agents, err := loader.LoadAgents()

	require.Error(t, err)
	assert.Nil(t, agents)
	assert.Contains(t, err.Error(), "failed to parse agents.yaml")
}

func TestConfigLoader_Caching(t *testing.T) {
	// Create temporary agents.yaml
	tempDir := t.TempDir()
	agentsFile := filepath.Join(tempDir, "agents.yaml")

	yamlContent := `agents:
  mock-agent:
    type: stdio
    config:
      command: "./bin/mock-agent"`

	err := os.WriteFile(agentsFile, []byte(yamlContent), 0644)
	require.NoError(t, err)

	loader := NewConfigLoader(agentsFile)

	// Load first time
	agents1, err := loader.LoadAgents()
	require.NoError(t, err)

	// Load second time - should return cached version
	agents2, err := loader.LoadAgents()
	require.NoError(t, err)

	// Should be the same instance (cached)
	assert.Same(t, agents1, agents2)
}

func TestConfigLoader_DefaultPath(t *testing.T) {
	loader := NewConfigLoader("")
	assert.Equal(t, "examples/agents.yaml", loader.filePath)
}

func TestConfigLoader_GetAvailableAgents(t *testing.T) {
	// Create temporary agents.yaml
	tempDir := t.TempDir()
	agentsFile := filepath.Join(tempDir, "agents.yaml")

	yamlContent := `agents:
  agent-a:
    type: stdio
    config:
      command: "./bin/agent-a"
  agent-b:
    type: a2a
    config:
      url: "http://localhost:8080"`

	err := os.WriteFile(agentsFile, []byte(yamlContent), 0644)
	require.NoError(t, err)

	// Test getting available agents
	loader := NewConfigLoader(agentsFile)
	agents, err := loader.GetAvailableAgents()

	require.NoError(t, err)
	assert.Len(t, agents, 2)
	assert.Contains(t, agents, "agent-a")
	assert.Contains(t, agents, "agent-b")
}

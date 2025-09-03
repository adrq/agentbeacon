package api

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/agentmaestro/agentmaestro/core/internal/protocol"
	"github.com/google/uuid"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// Simplified A2A tests that don't require full database setup
// These focus on JSON-RPC protocol compliance and message parsing

// TestA2AJSONRPCProtocol tests JSON-RPC protocol compliance
func TestA2AJSONRPCProtocol(t *testing.T) {
	// Create a mock handler that we can test protocol compliance with
	handler := &A2AHandler{
		taskMap:    make(map[string]string),
		contextMap: make(map[string]string),
	}
	server := httptest.NewServer(handler)
	defer server.Close()

	t.Run("ValidJSONRPCRequest", func(t *testing.T) {
		request := map[string]interface{}{
			"jsonrpc": "2.0",
			"method":  "tasks/get",
			"params": map[string]interface{}{
				"taskId": "nonexistent-task",
			},
			"id": "test-request-1",
		}

		jsonData, err := json.Marshal(request)
		require.NoError(t, err)

		resp, err := http.Post(server.URL, "application/json", bytes.NewBuffer(jsonData))
		require.NoError(t, err)
		defer resp.Body.Close()

		var response map[string]interface{}
		err = json.NewDecoder(resp.Body).Decode(&response)
		require.NoError(t, err)

		// Should have proper JSON-RPC structure
		assert.Equal(t, "2.0", response["jsonrpc"])
		assert.Equal(t, "test-request-1", response["id"])

		// Should have error since task doesn't exist (but protocol structure is correct)
		assert.NotNil(t, response["error"])
		assert.Nil(t, response["result"])

		errorData := response["error"].(map[string]interface{})
		assert.Equal(t, float64(-32603), errorData["code"])
		assert.Equal(t, "Internal error", errorData["message"])
	})

	t.Run("MalformedJSON", func(t *testing.T) {
		malformedJSON := `{"jsonrpc": "2.0", "method": "test", "id": 1, invalid}`

		resp, err := http.Post(server.URL, "application/json", bytes.NewBufferString(malformedJSON))
		require.NoError(t, err)
		defer resp.Body.Close()

		var response map[string]interface{}
		err = json.NewDecoder(resp.Body).Decode(&response)
		require.NoError(t, err)

		// Should return parse error
		assert.Equal(t, "2.0", response["jsonrpc"])
		assert.NotNil(t, response["error"])

		errorData := response["error"].(map[string]interface{})
		assert.Equal(t, float64(-32700), errorData["code"])
		assert.Equal(t, "Parse error", errorData["message"])
	})

	t.Run("UnsupportedHTTPMethod", func(t *testing.T) {
		req, err := http.NewRequest("GET", server.URL, nil)
		require.NoError(t, err)

		client := &http.Client{}
		resp, err := client.Do(req)
		require.NoError(t, err)
		defer resp.Body.Close()

		var response map[string]interface{}
		err = json.NewDecoder(resp.Body).Decode(&response)
		require.NoError(t, err)

		// Should return method not allowed
		errorData := response["error"].(map[string]interface{})
		assert.Equal(t, float64(-32601), errorData["code"])
		assert.Equal(t, "Method not allowed", errorData["message"])
	})

	t.Run("UnknownRPCMethod", func(t *testing.T) {
		request := map[string]interface{}{
			"jsonrpc": "2.0",
			"method":  "unknown/method",
			"params":  map[string]interface{}{},
			"id":      "test-request-2",
		}

		jsonData, err := json.Marshal(request)
		require.NoError(t, err)

		resp, err := http.Post(server.URL, "application/json", bytes.NewBuffer(jsonData))
		require.NoError(t, err)
		defer resp.Body.Close()

		var response map[string]interface{}
		err = json.NewDecoder(resp.Body).Decode(&response)
		require.NoError(t, err)

		// Should return method not found
		errorData := response["error"].(map[string]interface{})
		assert.Equal(t, float64(-32601), errorData["code"])
		assert.Equal(t, "Method not found", errorData["message"])
	})
}

// TestA2AMessageParsing tests message parsing logic
func TestA2AMessageParsing(t *testing.T) {
	handler := &A2AHandler{taskMap: map[string]string{}, contextMap: map[string]string{}}

	t.Run("RejectInlineTextYAML", func(t *testing.T) {
		messages := []protocol.Message{{
			Role:      "user",
			Parts:     []protocol.Part{{Kind: "text", Text: "name: wf\nnodes:\n - id: n1"}},
			MessageID: uuid.New().String(), Kind: "message",
		}}
		_, err := handler.extractWorkflowRef(messages)
		assert.Error(t, err)
		assert.Contains(t, err.Error(), "inline workflow YAML disabled")
	})

	t.Run("RejectInlineDataWorkflowYaml", func(t *testing.T) {
		messages := []protocol.Message{{
			Role:      "user",
			Parts:     []protocol.Part{{Kind: "data", Data: &protocol.DataPart{Data: map[string]interface{}{"workflowYaml": "name: wf\nnodes: []"}}}},
			MessageID: uuid.New().String(), Kind: "message",
		}}
		_, err := handler.extractWorkflowRef(messages)
		assert.Error(t, err)
		assert.Contains(t, err.Error(), "inline workflow YAML disabled")
	})

	t.Run("RequireWorkflowRef", func(t *testing.T) {
		messages := []protocol.Message{{
			Role:      "user",
			Parts:     []protocol.Part{{Kind: "text", Text: "Just a note"}},
			MessageID: uuid.New().String(), Kind: "message",
		}}
		_, err := handler.extractWorkflowRef(messages)
		assert.Error(t, err)
		assert.Contains(t, err.Error(), "workflowRef is required")
	})

	t.Run("AcceptWorkflowRef", func(t *testing.T) {
		messages := []protocol.Message{{
			Role:      "user",
			Parts:     []protocol.Part{{Kind: "data", Data: &protocol.DataPart{Data: map[string]interface{}{"workflowRef": "ns/demo:latest"}}}},
			MessageID: uuid.New().String(), Kind: "message",
		}}
		ref, err := handler.extractWorkflowRef(messages)
		assert.NoError(t, err)
		assert.Equal(t, "ns/demo:latest", ref)
	})

	t.Run("MultipleDifferingRefs", func(t *testing.T) {
		messages := []protocol.Message{
			{Role: "user", Parts: []protocol.Part{{Kind: "data", Data: &protocol.DataPart{Data: map[string]interface{}{"workflowRef": "ns/one:latest"}}}}, MessageID: uuid.New().String(), Kind: "message"},
			{Role: "user", Parts: []protocol.Part{{Kind: "data", Data: &protocol.DataPart{Data: map[string]interface{}{"workflowRef": "ns/two:latest"}}}}, MessageID: uuid.New().String(), Kind: "message"},
		}
		_, err := handler.extractWorkflowRef(messages)
		assert.Error(t, err)
		assert.Contains(t, err.Error(), "exactly one message required")
	})
}

// TestA2AAgentCardGeneration tests agent card generation
func TestA2AAgentCardGeneration(t *testing.T) {
	card := GetAgentCard()

	// Test protocol compliance
	assert.Equal(t, "0.3.0", card.ProtocolVersion)
	assert.Equal(t, "AgentMaestro Orchestrator", card.Name)
	assert.NotEmpty(t, card.Description)
	assert.NotEmpty(t, card.URL)
	assert.NotEmpty(t, card.Version)

	// Test capabilities
	assert.False(t, card.Capabilities.Streaming, "MVP doesn't support streaming")
	assert.False(t, card.Capabilities.PushNotifications, "MVP doesn't support push notifications")

	// Test input/output modes
	assert.Contains(t, card.DefaultInputModes, "application/json")
	assert.Contains(t, card.DefaultOutputModes, "application/json")

	// Test preferred transport
	assert.Equal(t, "JSONRPC", card.PreferredTransport)

	// Test skills
	require.Len(t, card.Skills, 1)
	skill := card.Skills[0]
	assert.Equal(t, "execute-workflow", skill.ID)
	assert.Equal(t, "Execute Workflow", skill.Name)
	assert.Contains(t, skill.Description, "workflow")
	assert.NotEmpty(t, skill.Examples)
	assert.Contains(t, skill.InputModes, "application/json")
	assert.Contains(t, skill.OutputModes, "application/json")
}

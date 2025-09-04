package api

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/agentmaestro/agentmaestro/core/internal/config"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// TestWorkerResultEndpointContract validates the worker result endpoint stub implementation.
func TestWorkerResultEndpointContract(t *testing.T) {
	t.Run("POST returns accepted response", func(t *testing.T) {
		// Setup test server
		db := createTestDB(t)
		configLoader := config.NewConfigLoader("examples/agents.yaml")
		handler := NewRestHandler(db, configLoader)

		// Prepare sample result payload
		resultPayload := map[string]interface{}{
			"taskId": "test-task-123",
			"status": "completed",
			"output": "Task completed successfully",
			"artifacts": []map[string]interface{}{
				{
					"name":    "result.txt",
					"content": "Sample output content",
					"type":    "text/plain",
				},
			},
		}

		jsonData, err := json.Marshal(resultPayload)
		require.NoError(t, err)

		// Submit task result
		req := httptest.NewRequest("POST", "/api/worker/result", bytes.NewBuffer(jsonData))
		req.Header.Set("Content-Type", "application/json")
		rec := httptest.NewRecorder()

		handler.ServeHTTP(rec, req)

		// Validate response format
		assert.Equal(t, http.StatusOK, rec.Code, "Result endpoint should return 200 OK")
		assert.Equal(t, "application/json", rec.Header().Get("Content-Type"), "Should return JSON content type")

		// Parse JSON response
		var response map[string]interface{}
		err = json.NewDecoder(rec.Body).Decode(&response)
		require.NoError(t, err, "Response should be valid JSON")

		// Validate stub contract: always accepts results
		assert.Contains(t, response, "accepted", "Response must contain 'accepted' field")
		assert.True(t, response["accepted"].(bool), "Accepted field should be true")
		assert.Len(t, response, 1, "Response should only contain accepted field")
	})

	t.Run("POST with empty body returns accepted", func(t *testing.T) {
		db := createTestDB(t)
		configLoader := config.NewConfigLoader("examples/agents.yaml")
		handler := NewRestHandler(db, configLoader)

		req := httptest.NewRequest("POST", "/api/worker/result", bytes.NewBufferString("{}"))
		req.Header.Set("Content-Type", "application/json")
		rec := httptest.NewRecorder()

		handler.ServeHTTP(rec, req)

		// Stub accepts empty payloads
		assert.Equal(t, http.StatusOK, rec.Code, "Empty JSON should be accepted")

		var response map[string]interface{}
		err := json.NewDecoder(rec.Body).Decode(&response)
		require.NoError(t, err)

		assert.True(t, response["accepted"].(bool), "Should still return accepted: true")
	})

	t.Run("POST with malformed JSON returns bad request", func(t *testing.T) {
		db := createTestDB(t)
		configLoader := config.NewConfigLoader("examples/agents.yaml")
		handler := NewRestHandler(db, configLoader)

		// Send malformed JSON
		req := httptest.NewRequest("POST", "/api/worker/result", strings.NewReader("{invalid json"))
		req.Header.Set("Content-Type", "application/json")
		rec := httptest.NewRecorder()

		handler.ServeHTTP(rec, req)

		// Invalid JSON returns error
		assert.Equal(t, http.StatusBadRequest, rec.Code, "Malformed JSON should return 400")

		var response map[string]interface{}
		err := json.NewDecoder(rec.Body).Decode(&response)
		require.NoError(t, err)

		assert.Contains(t, response, "error", "Error response should contain error field")
	})

	t.Run("GET method not allowed", func(t *testing.T) {
		db := createTestDB(t)
		configLoader := config.NewConfigLoader("examples/agents.yaml")
		handler := NewRestHandler(db, configLoader)

		req := httptest.NewRequest("GET", "/api/worker/result", nil)
		rec := httptest.NewRecorder()

		handler.ServeHTTP(rec, req)

		assert.Equal(t, http.StatusMethodNotAllowed, rec.Code, "GET should not be allowed")
	})

	t.Run("PUT method not allowed", func(t *testing.T) {
		db := createTestDB(t)
		configLoader := config.NewConfigLoader("examples/agents.yaml")
		handler := NewRestHandler(db, configLoader)

		req := httptest.NewRequest("PUT", "/api/worker/result", nil)
		rec := httptest.NewRecorder()

		handler.ServeHTTP(rec, req)

		assert.Equal(t, http.StatusMethodNotAllowed, rec.Code, "PUT should not be allowed")
	})

	t.Run("DELETE method not allowed", func(t *testing.T) {
		db := createTestDB(t)
		configLoader := config.NewConfigLoader("examples/agents.yaml")
		handler := NewRestHandler(db, configLoader)

		req := httptest.NewRequest("DELETE", "/api/worker/result", nil)
		rec := httptest.NewRecorder()

		handler.ServeHTTP(rec, req)

		assert.Equal(t, http.StatusMethodNotAllowed, rec.Code, "DELETE should not be allowed")
	})

	t.Run("consistent response across different payloads", func(t *testing.T) {
		db := createTestDB(t)
		configLoader := config.NewConfigLoader("examples/agents.yaml")
		handler := NewRestHandler(db, configLoader)

		// Test different payload types - all should be accepted by stub
		testPayloads := []map[string]interface{}{
			{"taskId": "task-1", "status": "completed"},
			{"taskId": "task-2", "status": "failed", "error": "Some error"},
			{"different": "format", "entirely": true},
		}

		for i, payload := range testPayloads {
			jsonData, err := json.Marshal(payload)
			require.NoError(t, err)

			req := httptest.NewRequest("POST", "/api/worker/result", bytes.NewBuffer(jsonData))
			req.Header.Set("Content-Type", "application/json")
			rec := httptest.NewRecorder()

			handler.ServeHTTP(rec, req)

			assert.Equal(t, http.StatusOK, rec.Code, "Payload %d should be accepted", i)

			var response map[string]interface{}
			err = json.NewDecoder(rec.Body).Decode(&response)
			require.NoError(t, err)

			assert.True(t, response["accepted"].(bool), "All payloads should be accepted by stub")
		}
	})
}

// TestWorkerResultEndpointRouting validates HTTP routing for the result endpoint.
func TestWorkerResultEndpointRouting(t *testing.T) {
	t.Run("exact path matches", func(t *testing.T) {
		db := createTestDB(t)
		configLoader := config.NewConfigLoader("examples/agents.yaml")
		handler := NewRestHandler(db, configLoader)

		req := httptest.NewRequest("POST", "/api/worker/result", bytes.NewBufferString("{}"))
		req.Header.Set("Content-Type", "application/json")
		rec := httptest.NewRecorder()

		handler.ServeHTTP(rec, req)

		// Route should be properly registered
		assert.NotEqual(t, http.StatusNotFound, rec.Code, "Result endpoint should be routed correctly")
	})

	t.Run("trailing slash handling", func(t *testing.T) {
		db := createTestDB(t)
		configLoader := config.NewConfigLoader("examples/agents.yaml")
		handler := NewRestHandler(db, configLoader)

		req := httptest.NewRequest("POST", "/api/worker/result/", bytes.NewBufferString("{}"))
		req.Header.Set("Content-Type", "application/json")
		rec := httptest.NewRecorder()

		handler.ServeHTTP(rec, req)

		// Exact path matching enforced
		assert.Equal(t, http.StatusNotFound, rec.Code, "Trailing slash should not match exact route")
	})
}

// TestWorkerResultContentTypeHandling validates content type processing.
func TestWorkerResultContentTypeHandling(t *testing.T) {
	t.Run("missing content type", func(t *testing.T) {
		db := createTestDB(t)
		configLoader := config.NewConfigLoader("examples/agents.yaml")
		handler := NewRestHandler(db, configLoader)

		req := httptest.NewRequest("POST", "/api/worker/result", bytes.NewBufferString("{}"))
		// Omit Content-Type header intentionally
		rec := httptest.NewRecorder()

		handler.ServeHTTP(rec, req)

		// Stub implementation is lenient with content types
		assert.Equal(t, http.StatusOK, rec.Code, "Missing content-type should still be accepted by stub")
	})

	t.Run("wrong content type", func(t *testing.T) {
		db := createTestDB(t)
		configLoader := config.NewConfigLoader("examples/agents.yaml")
		handler := NewRestHandler(db, configLoader)

		req := httptest.NewRequest("POST", "/api/worker/result", bytes.NewBufferString("{}"))
		req.Header.Set("Content-Type", "text/plain")
		rec := httptest.NewRecorder()

		handler.ServeHTTP(rec, req)

		// Lenient content-type handling in stub
		assert.Equal(t, http.StatusOK, rec.Code, "Wrong content-type should still be accepted by stub")
	})
}

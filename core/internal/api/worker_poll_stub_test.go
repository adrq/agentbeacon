package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/agentmaestro/agentmaestro/core/internal/config"
	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

// TestWorkerPollEndpointContract validates the worker poll endpoint stub implementation.
func TestWorkerPollEndpointContract(t *testing.T) {
	t.Run("GET returns no task available response", func(t *testing.T) {
		// Setup test server
		db := createTestDB(t)
		configLoader := config.NewConfigLoader("examples/agents.yaml")
		handler := NewRestHandler(db, configLoader)

		// Poll for available tasks
		req := httptest.NewRequest("GET", "/api/worker/poll", nil)
		rec := httptest.NewRecorder()

		handler.ServeHTTP(rec, req)

		// Validate response format
		assert.Equal(t, http.StatusOK, rec.Code, "Poll endpoint should return 200 OK")
		assert.Equal(t, "application/json", rec.Header().Get("Content-Type"), "Should return JSON content type")

		// Parse JSON response
		var response map[string]interface{}
		err := json.NewDecoder(rec.Body).Decode(&response)
		require.NoError(t, err, "Response should be valid JSON")

		// Validate stub contract: no tasks available
		assert.Contains(t, response, "task", "Response must contain 'task' field")
		assert.Nil(t, response["task"], "Task field should be null when no tasks available")
		assert.Len(t, response, 1, "Response should only contain task field")
	})

	t.Run("POST method not allowed", func(t *testing.T) {
		db := createTestDB(t)
		configLoader := config.NewConfigLoader("examples/agents.yaml")
		handler := NewRestHandler(db, configLoader)

		req := httptest.NewRequest("POST", "/api/worker/poll", nil)
		rec := httptest.NewRecorder()

		handler.ServeHTTP(rec, req)

		assert.Equal(t, http.StatusMethodNotAllowed, rec.Code, "POST should not be allowed")
	})

	t.Run("PUT method not allowed", func(t *testing.T) {
		db := createTestDB(t)
		configLoader := config.NewConfigLoader("examples/agents.yaml")
		handler := NewRestHandler(db, configLoader)

		req := httptest.NewRequest("PUT", "/api/worker/poll", nil)
		rec := httptest.NewRecorder()

		handler.ServeHTTP(rec, req)

		assert.Equal(t, http.StatusMethodNotAllowed, rec.Code, "PUT should not be allowed")
	})

	t.Run("DELETE method not allowed", func(t *testing.T) {
		db := createTestDB(t)
		configLoader := config.NewConfigLoader("examples/agents.yaml")
		handler := NewRestHandler(db, configLoader)

		req := httptest.NewRequest("DELETE", "/api/worker/poll", nil)
		rec := httptest.NewRecorder()

		handler.ServeHTTP(rec, req)

		assert.Equal(t, http.StatusMethodNotAllowed, rec.Code, "DELETE should not be allowed")
	})

	t.Run("consistent response format across multiple calls", func(t *testing.T) {
		db := createTestDB(t)
		configLoader := config.NewConfigLoader("examples/agents.yaml")
		handler := NewRestHandler(db, configLoader)

		// Make multiple requests to ensure consistent behavior
		for i := 0; i < 3; i++ {
			req := httptest.NewRequest("GET", "/api/worker/poll", nil)
			rec := httptest.NewRecorder()

			handler.ServeHTTP(rec, req)

			assert.Equal(t, http.StatusOK, rec.Code, "Each poll should return 200 OK")

			var response map[string]interface{}
			err := json.NewDecoder(rec.Body).Decode(&response)
			require.NoError(t, err, "Each response should be valid JSON")

			assert.Nil(t, response["task"], "Task should always be null in stub implementation")
		}
	})
}

// TestWorkerPollEndpointRouting validates HTTP routing for the poll endpoint.
func TestWorkerPollEndpointRouting(t *testing.T) {
	t.Run("exact path matches", func(t *testing.T) {
		db := createTestDB(t)
		configLoader := config.NewConfigLoader("examples/agents.yaml")
		handler := NewRestHandler(db, configLoader)

		req := httptest.NewRequest("GET", "/api/worker/poll", nil)
		rec := httptest.NewRecorder()

		handler.ServeHTTP(rec, req)

		// Route should be properly registered
		assert.NotEqual(t, http.StatusNotFound, rec.Code, "Poll endpoint should be routed correctly")
	})

	t.Run("trailing slash handling", func(t *testing.T) {
		db := createTestDB(t)
		configLoader := config.NewConfigLoader("examples/agents.yaml")
		handler := NewRestHandler(db, configLoader)

		req := httptest.NewRequest("GET", "/api/worker/poll/", nil)
		rec := httptest.NewRecorder()

		handler.ServeHTTP(rec, req)

		// Exact path matching enforced
		assert.Equal(t, http.StatusNotFound, rec.Code, "Trailing slash should not match exact route")
	})
}

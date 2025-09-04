package api

import (
	"encoding/json"
	"net/http"
)

// workerPollStubHandler returns no task available (stub implementation).
func (api *RestAPI) workerPollStubHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]interface{}{"task": nil})
}

// workerResultStubHandler accepts submitted results (stub implementation).
func (api *RestAPI) workerResultStubHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	// Accept any valid JSON payload
	var ignored interface{}
	if err := json.NewDecoder(r.Body).Decode(&ignored); err != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON body")
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]bool{"accepted": true})
}

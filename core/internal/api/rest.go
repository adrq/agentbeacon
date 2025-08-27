package api

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"github.com/agentmaestro/agentmaestro/core/internal/storage"
	"github.com/google/uuid"
)

func NewRestHandler(db *storage.DB) http.Handler {
	mux := http.NewServeMux()

	mux.HandleFunc("/api/health", healthHandler)
	mux.HandleFunc("/api/configs", func(w http.ResponseWriter, r *http.Request) {
		configsHandler(w, r, db)
	})
	mux.HandleFunc("/api/configs/", func(w http.ResponseWriter, r *http.Request) {
		configByNameHandler(w, r, db)
	})
	mux.HandleFunc("/api/workflows/register", func(w http.ResponseWriter, r *http.Request) {
		registerWorkflowHandler(w, r, db)
	})
	mux.HandleFunc("/api/workflows", func(w http.ResponseWriter, r *http.Request) {
		workflowsHandler(w, r, db)
	})
	mux.HandleFunc("/api/workflows/", func(w http.ResponseWriter, r *http.Request) {
		workflowByNameHandler(w, r, db)
	})

	return mux
}

func healthHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}

func configsHandler(w http.ResponseWriter, r *http.Request, db *storage.DB) {
	switch r.Method {
	case http.MethodPost:
		createConfigHandler(w, r, db)
	case http.MethodGet:
		listConfigsHandler(w, r, db)
	default:
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

func createConfigHandler(w http.ResponseWriter, r *http.Request, db *storage.DB) {
	var config storage.Config
	if err := json.NewDecoder(r.Body).Decode(&config); err != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON body")
		return
	}

	if config.ID == "" {
		config.ID = uuid.New().String()
	}

	if err := db.CreateConfig(&config); err != nil {
		writeError(w, http.StatusInternalServerError, fmt.Sprintf("failed to create config: %v", err))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(config)
}

func listConfigsHandler(w http.ResponseWriter, r *http.Request, db *storage.DB) {
	configs, err := db.ListConfigs()
	if err != nil {
		writeError(w, http.StatusInternalServerError, fmt.Sprintf("failed to list configs: %v", err))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(configs)
}

func configByNameHandler(w http.ResponseWriter, r *http.Request, db *storage.DB) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	name := extractPathParam(r.URL.Path, "/api/configs/")
	if name == "" {
		writeError(w, http.StatusBadRequest, "config name is required")
		return
	}

	config, err := db.GetConfig(name)
	if err != nil {
		if err == sql.ErrNoRows || strings.Contains(err.Error(), "not found") {
			writeError(w, http.StatusNotFound, "config not found")
			return
		}
		writeError(w, http.StatusInternalServerError, fmt.Sprintf("failed to get config: %v", err))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(config)
}

func registerWorkflowHandler(w http.ResponseWriter, r *http.Request, db *storage.DB) {
	if r.Method != http.MethodPost {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	var req struct {
		FilePath string `json:"file_path"`
	}

	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON body")
		return
	}

	if req.FilePath == "" {
		writeError(w, http.StatusBadRequest, "file_path is required")
		return
	}

	if err := db.RegisterWorkflow(req.FilePath); err != nil {
		writeError(w, http.StatusInternalServerError, fmt.Sprintf("failed to register workflow: %v", err))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(map[string]string{"message": "workflow registered successfully"})
}

func workflowsHandler(w http.ResponseWriter, r *http.Request, db *storage.DB) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	workflows, err := db.ListWorkflows()
	if err != nil {
		writeError(w, http.StatusInternalServerError, fmt.Sprintf("failed to list workflows: %v", err))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(workflows)
}

func workflowByNameHandler(w http.ResponseWriter, r *http.Request, db *storage.DB) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	name := extractPathParam(r.URL.Path, "/api/workflows/")
	if name == "" {
		writeError(w, http.StatusBadRequest, "workflow name is required")
		return
	}

	workflow, err := db.GetWorkflowMetadata(name)
	if err != nil {
		if err == sql.ErrNoRows || strings.Contains(err.Error(), "not found") {
			writeError(w, http.StatusNotFound, "workflow not found")
			return
		}
		writeError(w, http.StatusInternalServerError, fmt.Sprintf("failed to get workflow: %v", err))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(workflow)
}

func extractPathParam(path, prefix string) string {
	if !strings.HasPrefix(path, prefix) {
		return ""
	}
	return strings.TrimPrefix(path, prefix)
}

func writeError(w http.ResponseWriter, statusCode int, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(statusCode)
	json.NewEncoder(w).Encode(map[string]string{"error": message})
}

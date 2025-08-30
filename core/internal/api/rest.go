package api

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"github.com/agentmaestro/agentmaestro/core/internal/executor"
	"github.com/agentmaestro/agentmaestro/core/internal/storage"
	"github.com/google/uuid"
)

// RestAPI encapsulates the REST API with a shared executor instance
type RestAPI struct {
	db       *storage.DB
	executor *executor.Executor
}

func NewRestHandler(db *storage.DB) http.Handler {
	api := &RestAPI{
		db:       db,
		executor: executor.NewExecutor(db),
	}
	mux := http.NewServeMux()

	mux.HandleFunc("/api/health", api.healthHandler)
	mux.HandleFunc("/api/configs", api.configsHandler)
	mux.HandleFunc("/api/configs/", api.configByNameHandler)
	mux.HandleFunc("/api/workflows/register", api.registerWorkflowHandler)
	mux.HandleFunc("/api/workflows", api.workflowsHandler)
	mux.HandleFunc("/api/workflows/", api.workflowRelatedHandler)
	mux.HandleFunc("/api/executions/start", api.startExecutionHandler)
	mux.HandleFunc("/api/executions", api.listExecutionsHandler)
	mux.HandleFunc("/api/executions/", api.executionHandler)

	return mux
}

func (api *RestAPI) healthHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}

func (api *RestAPI) configsHandler(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodPost:
		api.createConfigHandler(w, r)
	case http.MethodGet:
		api.listConfigsHandler(w, r)
	default:
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

func (api *RestAPI) createConfigHandler(w http.ResponseWriter, r *http.Request) {
	var config storage.Config
	if err := json.NewDecoder(r.Body).Decode(&config); err != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON body")
		return
	}

	if config.ID == "" {
		config.ID = uuid.New().String()
	}

	if err := api.db.CreateConfig(&config); err != nil {
		writeError(w, http.StatusInternalServerError, fmt.Sprintf("failed to create config: %v", err))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(config)
}

func (api *RestAPI) listConfigsHandler(w http.ResponseWriter, r *http.Request) {
	configs, err := api.db.ListConfigs()
	if err != nil {
		writeError(w, http.StatusInternalServerError, fmt.Sprintf("failed to list configs: %v", err))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(configs)
}

func (api *RestAPI) configByNameHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	name := extractPathParam(r.URL.Path, "/api/configs/")
	if name == "" {
		writeError(w, http.StatusBadRequest, "config name is required")
		return
	}

	config, err := api.db.GetConfig(name)
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

func (api *RestAPI) registerWorkflowHandler(w http.ResponseWriter, r *http.Request) {
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

	if err := api.db.RegisterWorkflow(req.FilePath); err != nil {
		writeError(w, http.StatusInternalServerError, fmt.Sprintf("failed to register workflow: %v", err))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(map[string]string{"message": "workflow registered successfully"})
}

func (api *RestAPI) workflowsHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	workflows, err := api.db.ListWorkflows()
	if err != nil {
		writeError(w, http.StatusInternalServerError, fmt.Sprintf("failed to list workflows: %v", err))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(workflows)
}

func (api *RestAPI) workflowByNameHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	name := extractPathParam(r.URL.Path, "/api/workflows/")
	if name == "" {
		writeError(w, http.StatusBadRequest, "workflow name is required")
		return
	}

	workflow, err := api.db.GetWorkflowMetadata(name)
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
	// Extract the parameter and normalize it by trimming trailing slashes
	param := strings.TrimPrefix(path, prefix)
	return strings.TrimRight(param, "/")
}

func (api *RestAPI) startExecutionHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	var req struct {
		WorkflowName string `json:"workflow_name"`
	}

	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON body")
		return
	}

	if req.WorkflowName == "" {
		writeError(w, http.StatusBadRequest, "workflow_name is required")
		return
	}

	execution, err := api.executor.StartWorkflow(req.WorkflowName)
	if err != nil {
		writeError(w, http.StatusInternalServerError, fmt.Sprintf("failed to start workflow: %v", err))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(execution)
}

func writeError(w http.ResponseWriter, statusCode int, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(statusCode)
	json.NewEncoder(w).Encode(map[string]string{"error": message})
}

func (api *RestAPI) workflowRelatedHandler(w http.ResponseWriter, r *http.Request) {
	path := r.URL.Path

	// Check if this is a request for workflow executions (ends with /executions)
	if strings.HasSuffix(path, "/executions") {
		api.workflowExecutionsHandler(w, r)
		return
	}

	// Otherwise, handle as workflow by name
	api.workflowByNameHandler(w, r)
}

func (api *RestAPI) listExecutionsHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	statusFilter := r.URL.Query().Get("status")

	executions, err := api.executor.ListExecutions(statusFilter)
	if err != nil {
		writeError(w, http.StatusInternalServerError, fmt.Sprintf("failed to list executions: %v", err))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(executions)
}

func (api *RestAPI) executionHandler(w http.ResponseWriter, r *http.Request) {
	executionID := extractPathParam(r.URL.Path, "/api/executions/")
	if executionID == "" {
		writeError(w, http.StatusBadRequest, "execution ID is required")
		return
	}

	// Check if this is a request for execution stop or status
	if strings.HasSuffix(executionID, "/stop") {
		api.stopExecutionHandler(w, r, strings.TrimSuffix(executionID, "/stop"))
		return
	}

	if strings.HasSuffix(executionID, "/status") {
		api.executionStatusHandler(w, r, strings.TrimSuffix(executionID, "/status"))
		return
	}

	// Otherwise, handle as get full execution details
	api.getExecutionHandler(w, r, executionID)
}

func (api *RestAPI) stopExecutionHandler(w http.ResponseWriter, r *http.Request, executionID string) {
	if r.Method != http.MethodPost {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	if err := api.executor.StopExecution(executionID); err != nil {
		if strings.Contains(err.Error(), "not found") || strings.Contains(err.Error(), "not running") {
			writeError(w, http.StatusNotFound, err.Error())
			return
		}
		writeError(w, http.StatusInternalServerError, fmt.Sprintf("failed to stop execution: %v", err))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"message": "execution stopped successfully"})
}

func (api *RestAPI) executionStatusHandler(w http.ResponseWriter, r *http.Request, executionID string) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	status, err := api.executor.GetExecutionStatus(executionID)
	if err != nil {
		if strings.Contains(err.Error(), "not found") {
			writeError(w, http.StatusNotFound, "execution not found")
			return
		}
		writeError(w, http.StatusInternalServerError, fmt.Sprintf("failed to get execution status: %v", err))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(status)
}

func (api *RestAPI) getExecutionHandler(w http.ResponseWriter, r *http.Request, executionID string) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	execution, err := api.executor.GetExecution(executionID)
	if err != nil {
		if strings.Contains(err.Error(), "not found") {
			writeError(w, http.StatusNotFound, "execution not found")
			return
		}
		writeError(w, http.StatusInternalServerError, fmt.Sprintf("failed to get execution: %v", err))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(execution)
}

func (api *RestAPI) workflowExecutionsHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	// Extract workflow name from path like /api/workflows/{name}/executions
	path := r.URL.Path
	workflowName := extractPathParam(path, "/api/workflows/")
	workflowName = strings.TrimSuffix(workflowName, "/executions")

	if workflowName == "" {
		writeError(w, http.StatusBadRequest, "workflow name is required")
		return
	}

	executions, err := api.executor.GetWorkflowExecutions(workflowName)
	if err != nil {
		writeError(w, http.StatusInternalServerError, fmt.Sprintf("failed to get workflow executions: %v", err))
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(executions)
}

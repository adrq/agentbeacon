package api

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"

	"github.com/agentmaestro/agentmaestro/core/internal/config"
	"github.com/agentmaestro/agentmaestro/core/internal/executor"
	"github.com/agentmaestro/agentmaestro/core/internal/storage"
	"github.com/google/uuid"
	"gopkg.in/yaml.v3"
)

// RestAPI encapsulates the REST API with a shared executor instance
type RestAPI struct {
	db       *storage.DB
	executor *executor.Executor
}

func NewRestHandler(db *storage.DB, configLoader *config.ConfigLoader) http.Handler {
	api := &RestAPI{
		db:       db,
		executor: executor.NewExecutor(db, configLoader),
	}
	return setupRestAPIRoutes(api)
}

func NewRestHandlerWithExecutor(db *storage.DB, configLoader *config.ConfigLoader, exec *executor.Executor) http.Handler {
	api := &RestAPI{
		db:       db,
		executor: exec,
	}
	return setupRestAPIRoutes(api)
}

func setupRestAPIRoutes(api *RestAPI) http.Handler {
	mux := http.NewServeMux()

	// REST API endpoints
	mux.HandleFunc("/api/health", api.healthHandler)
	mux.HandleFunc("/api/ready", api.readyHandler)
	mux.HandleFunc("/api/configs", api.configsHandler)
	mux.HandleFunc("/api/configs/", api.configByNameHandler)
	mux.HandleFunc("/api/workflows/register", api.registerWorkflowHandler)
	mux.HandleFunc("/api/workflows", api.workflowsLatestHandler)
	mux.HandleFunc("/api/workflows/", api.workflowRegistryHandler)
	mux.HandleFunc("/api/git/sync", api.gitSyncHandler)
	// Removed legacy /api/executions/start (execution now via A2A & registry refs)
	mux.HandleFunc("/api/executions", api.listExecutionsHandler)
	mux.HandleFunc("/api/executions/", api.executionHandler)

	// Worker API endpoints
	mux.HandleFunc("/api/worker/poll", api.workerPollHandler)
	mux.HandleFunc("/api/worker/result", api.workerResultHandler)
	mux.HandleFunc("/api/worker/sync", api.workerSyncHandler)

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

func (api *RestAPI) readyHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	// For MVP, scheduler is ready when database connection is working
	// In future, this could check if all required services are available
	if err := api.db.Ping(); err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusServiceUnavailable)
		json.NewEncoder(w).Encode(map[string]string{"status": "not ready", "reason": "database unavailable"})
		return
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "ready"})
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

// registerWorkflowHandler supports inline YAML registration only for the immutable registry.
func (api *RestAPI) registerWorkflowHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}

	// Read body once so we can attempt YAML fallback if JSON decode into legacy struct fails.
	body, err := io.ReadAll(r.Body)
	if err != nil {
		writeError(w, http.StatusBadRequest, "failed to read body")
		return
	}

	// Attempt to decode into a generic map to inspect available keys.
	var generic map[string]json.RawMessage
	if err := json.Unmarshal(body, &generic); err != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON body")
		return
	}

	// Inline YAML path
	if _, ok := generic["workflow_yaml"]; ok {
		var yamlHolder struct {
			WorkflowYAML string `json:"workflow_yaml"`
		}
		if err := json.Unmarshal(body, &yamlHolder); err != nil || yamlHolder.WorkflowYAML == "" {
			writeError(w, http.StatusBadRequest, "workflow_yaml is required")
			return
		}
		// Simple validation to reject old format and require new format
		var workflowDoc map[string]interface{}
		if err := yaml.Unmarshal([]byte(yamlHolder.WorkflowYAML), &workflowDoc); err != nil {
			writeError(w, http.StatusBadRequest, "invalid workflow YAML")
			return
		}

		// Check for old "nodes" field and reject it
		if _, hasNodes := workflowDoc["nodes"]; hasNodes {
			writeError(w, http.StatusBadRequest, "workflow schema error: 'nodes' field not supported, use 'tasks' instead")
			return
		}

		// Require new "tasks" field
		if _, hasTasks := workflowDoc["tasks"]; !hasTasks {
			writeError(w, http.StatusBadRequest, "workflow schema error: 'tasks' field is required")
			return
		}

		// Parse YAML to extract required fields (after schema validation)
		var parsed struct {
			Name        string `yaml:"name"`
			Namespace   string `yaml:"namespace"`
			Description string `yaml:"description"`
		}
		if err := yaml.Unmarshal([]byte(yamlHolder.WorkflowYAML), &parsed); err != nil {
			writeError(w, http.StatusBadRequest, "invalid workflow YAML")
			return
		}
		if parsed.Name == "" {
			writeError(w, http.StatusBadRequest, "workflow YAML must include name")
			return
		}
		// Use empty namespace for new schema (backward compatibility with DB)
		if parsed.Namespace == "" {
			parsed.Namespace = "default"
		}
		wf, regErr := api.db.RegisterInlineWorkflow(parsed.Namespace, parsed.Name, parsed.Description, yamlHolder.WorkflowYAML)
		if regErr != nil {
			if regErr == storage.ErrDuplicateContent {
				// Need existing latest to construct ref in response per spec.
				latest, lerr := api.db.GetLatestWorkflowVersion(parsed.Namespace, parsed.Name)
				if lerr != nil { // fallback if somehow not accessible
					writeError(w, http.StatusConflict, regErr.Error())
					return
				}
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusConflict)
				json.NewEncoder(w).Encode(map[string]string{
					"error": regErr.Error(),
					"ref":   fmt.Sprintf("%s/%s:%s", latest.Namespace, latest.Name, latest.Version),
				})
				return
			}
			writeError(w, http.StatusBadRequest, regErr.Error())
			return
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"ref":       fmt.Sprintf("%s/%s:%s", wf.Namespace, wf.Name, wf.Version),
			"namespace": wf.Namespace,
			"name":      wf.Name,
			"version":   wf.Version,
			"latest":    wf.IsLatest,
		})
		return
	}

	writeError(w, http.StatusBadRequest, "workflow_yaml is required for registration")
}

// workflowsLatestHandler returns latest registry versions only (one per workflow) per MVP registry spec.
func (api *RestAPI) workflowsLatestHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	rows, err := api.db.ListLatestWorkflowVersions()
	if err != nil {
		writeError(w, http.StatusInternalServerError, fmt.Sprintf("failed to list workflows: %v", err))
		return
	}
	dtos := make([]map[string]interface{}, 0, len(rows))
	for _, wf := range rows {
		dtos = append(dtos, map[string]interface{}{
			"namespace":   wf.Namespace,
			"name":        wf.Name,
			"version":     wf.Version,
			"latest":      wf.IsLatest,
			"description": wf.Description,
		})
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(dtos)
}

// workflowRegistryHandler handles:
// GET /api/workflows/{ns}/{name} -> latest version
// GET /api/workflows/{ns}/{name}/versions -> all versions newest->oldest
func (api *RestAPI) workflowRegistryHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	suffix := strings.TrimPrefix(r.URL.Path, "/api/workflows/")
	suffix = strings.TrimSuffix(suffix, "/")
	if suffix == "" { // should have been caught by exact /api/workflows route
		writeError(w, http.StatusBadRequest, "missing workflow reference")
		return
	}

	parts := strings.Split(suffix, "/")
	if len(parts) < 2 {
		writeError(w, http.StatusBadRequest, "expected /api/workflows/{namespace}/{name}")
		return
	}
	ns := parts[0]
	name := parts[1]
	if len(parts) == 2 { // latest
		wf, err := api.db.GetLatestWorkflowVersion(ns, name)
		if err != nil {
			writeError(w, http.StatusNotFound, "workflow not found")
			return
		}
		dto := map[string]interface{}{
			"namespace":   wf.Namespace,
			"name":        wf.Name,
			"version":     wf.Version,
			"latest":      wf.IsLatest,
			"description": wf.Description,
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(dto)
		return
	}
	if len(parts) == 3 && parts[2] == "versions" { // versions list
		rows, err := api.db.ListWorkflowVersions(ns, name)
		if err != nil {
			writeError(w, http.StatusNotFound, "workflow not found")
			return
		}
		dtos := make([]map[string]interface{}, 0, len(rows))
		for _, wf := range rows {
			dtos = append(dtos, map[string]interface{}{
				"namespace":   wf.Namespace,
				"name":        wf.Name,
				"version":     wf.Version,
				"latest":      wf.IsLatest,
				"description": wf.Description,
			})
		}
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(dtos)
		return
	}
	writeError(w, http.StatusBadRequest, "unrecognized workflow path")
}

func extractPathParam(path, prefix string) string {
	if !strings.HasPrefix(path, prefix) {
		return ""
	}
	// Extract the parameter and normalize it by trimming trailing slashes
	param := strings.TrimPrefix(path, prefix)
	return strings.TrimRight(param, "/")
}

// gitSyncHandler triggers a manual git repository scan for workflow YAML files.
// Request body: {"repo_path": "/path/to/repo"}
// Response per spec section 9: {"scanned": n, "inserted": n, ...}
func (api *RestAPI) gitSyncHandler(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
		return
	}
	var req struct {
		RepoPath string `json:"repo_path"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	if req.RepoPath == "" {
		writeError(w, http.StatusBadRequest, "repo_path is required")
		return
	}
	result, err := api.db.GitSync(req.RepoPath)
	if err != nil {
		writeError(w, http.StatusInternalServerError, fmt.Sprintf("git sync failed: %v", err))
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(result)
}

func writeError(w http.ResponseWriter, statusCode int, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(statusCode)
	json.NewEncoder(w).Encode(map[string]string{"error": message})
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

	// Otherwise, fetch full execution details directly
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

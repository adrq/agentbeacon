package storage

import (
	"fmt"
	"os"
	"time"

	"gopkg.in/yaml.v3"
)

func (db *DB) CreateConfig(config *Config) error {
	config.CreatedAt = time.Now()
	config.UpdatedAt = time.Now()

	if db.driver == "postgres" {
		_, err := db.NamedExec(`
			INSERT INTO config (id, name, api_keys, agent_settings, created_at, updated_at)
			VALUES (:id, :name, :api_keys, :agent_settings, :created_at, :updated_at)
			ON CONFLICT (name) DO UPDATE SET
				api_keys = EXCLUDED.api_keys,
				agent_settings = EXCLUDED.agent_settings,
				updated_at = EXCLUDED.updated_at
		`, config)
		return err
	} else {
		_, err := db.NamedExec(`
			INSERT OR REPLACE INTO config (id, name, api_keys, agent_settings, created_at, updated_at)
			VALUES (:id, :name, :api_keys, :agent_settings, :created_at, :updated_at)
		`, config)
		return err
	}
}

func (db *DB) GetConfig(name string) (*Config, error) {
	var config Config
	query := db.placeholder("SELECT * FROM config WHERE name = ?")
	err := db.Get(&config, query, name)
	if err != nil {
		return nil, fmt.Errorf("config not found: %w", err)
	}
	return &config, nil
}

func (db *DB) ListConfigs() ([]Config, error) {
	var configs []Config
	err := db.Select(&configs, "SELECT * FROM config ORDER BY name")
	return configs, err
}

type WorkflowYAML struct {
	Name        string `yaml:"name"`
	Description string `yaml:"description"`
}

func (db *DB) RegisterWorkflow(filePath string) error {
	content, err := os.ReadFile(filePath)
	if err != nil {
		return fmt.Errorf("failed to read workflow file: %w", err)
	}

	var wf WorkflowYAML
	if err := yaml.Unmarshal(content, &wf); err != nil {
		return fmt.Errorf("failed to parse workflow YAML: %w", err)
	}

	if wf.Name == "" {
		return fmt.Errorf("workflow name is required in YAML")
	}

	meta := &WorkflowMeta{
		Name:        wf.Name,
		FilePath:    filePath,
		Description: wf.Description,
		Version:     1,
		CreatedAt:   time.Now(),
		UpdatedAt:   time.Now(),
	}

	_, err = db.NamedExec(`
		INSERT INTO workflow (name, file_path, description, version, created_at, updated_at)
		VALUES (:name, :file_path, :description, :version, :created_at, :updated_at)
	`, meta)
	return err
}

func (db *DB) GetWorkflowMetadata(name string) (*WorkflowMeta, error) {
	var meta WorkflowMeta
	query := db.placeholder("SELECT * FROM workflow WHERE name = ?")
	err := db.Get(&meta, query, name)
	if err != nil {
		return nil, fmt.Errorf("workflow not found: %w", err)
	}
	return &meta, nil
}

func (db *DB) LoadWorkflowYAML(name string) ([]byte, error) {
	meta, err := db.GetWorkflowMetadata(name)
	if err != nil {
		return nil, err
	}

	content, err := os.ReadFile(meta.FilePath)
	if err != nil {
		return nil, fmt.Errorf("failed to read workflow file: %w", err)
	}
	return content, nil
}

func (db *DB) UpdateWorkflowFile(name string, content []byte) error {
	meta, err := db.GetWorkflowMetadata(name)
	if err != nil {
		return err
	}

	var wf WorkflowYAML
	if err := yaml.Unmarshal(content, &wf); err != nil {
		return fmt.Errorf("failed to parse workflow YAML: %w", err)
	}

	if wf.Name != name {
		return fmt.Errorf("workflow name mismatch: expected %s, got %s", name, wf.Name)
	}

	if err := os.WriteFile(meta.FilePath, content, 0644); err != nil {
		return fmt.Errorf("failed to write workflow file: %w", err)
	}

	meta.Description = wf.Description
	meta.Version++
	meta.UpdatedAt = time.Now()

	query := db.placeholder("UPDATE workflow SET description = ?, version = ?, updated_at = ? WHERE name = ?")
	_, err = db.Exec(query, meta.Description, meta.Version, meta.UpdatedAt, name)
	return err
}

func (db *DB) ListWorkflows() ([]WorkflowMeta, error) {
	var workflows []WorkflowMeta
	err := db.Select(&workflows, "SELECT * FROM workflow ORDER BY name")
	return workflows, err
}

func (db *DB) DeleteWorkflow(name string) error {
	meta, err := db.GetWorkflowMetadata(name)
	if err != nil {
		return err
	}

	if err := os.Remove(meta.FilePath); err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("failed to delete workflow file: %w", err)
	}

	query := db.placeholder("DELETE FROM workflow WHERE name = ?")
	_, err = db.Exec(query, name)
	return err
}

func (db *DB) CreateExecution(execution *Execution) error {
	execution.StartedAt = time.Now()

	_, err := db.NamedExec(`
		INSERT INTO execution (id, workflow_name, status, node_states, logs, started_at, completed_at)
		VALUES (:id, :workflow_name, :status, :node_states, :logs, :started_at, :completed_at)
	`, execution)
	return err
}

func (db *DB) UpdateExecution(execution *Execution) error {
	_, err := db.NamedExec(`
		UPDATE execution
		SET status = :status, node_states = :node_states, logs = :logs, completed_at = :completed_at
		WHERE id = :id
	`, execution)
	return err
}

func (db *DB) GetExecution(id string) (*Execution, error) {
	var execution Execution
	query := db.placeholder("SELECT * FROM execution WHERE id = ?")
	err := db.Get(&execution, query, id)
	if err != nil {
		return nil, fmt.Errorf("execution not found: %w", err)
	}
	return &execution, nil
}

func (db *DB) ListExecutions(workflowName string) ([]Execution, error) {
	var executions []Execution
	query := db.placeholder("SELECT * FROM execution WHERE workflow_name = ? ORDER BY started_at DESC")
	err := db.Select(&executions, query, workflowName)
	return executions, err
}

func (db *DB) ListAllExecutions() ([]Execution, error) {
	var executions []Execution
	err := db.Select(&executions, "SELECT * FROM execution ORDER BY started_at DESC")
	return executions, err
}

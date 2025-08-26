package storage

import (
	"time"

	"gorm.io/datatypes"
)

type Config struct {
	ID            string         `gorm:"primaryKey;type:varchar(255)" db:"id"`
	Name          string         `gorm:"uniqueIndex:idx_config_name;type:varchar(255);not null" db:"name"`
	APIKeys       datatypes.JSON `gorm:"type:jsonb;not null;serializer:json" db:"api_keys"`
	AgentSettings datatypes.JSON `gorm:"type:jsonb;not null;serializer:json" db:"agent_settings"`
	CreatedAt     time.Time      `gorm:"autoCreateTime" db:"created_at"`
	UpdatedAt     time.Time      `gorm:"autoUpdateTime" db:"updated_at"`
}

type WorkflowMeta struct {
	Name        string    `gorm:"primaryKey;type:varchar(255)" db:"name"`
	FilePath    string    `gorm:"type:varchar(500);not null" db:"file_path"`
	Description string    `gorm:"type:text" db:"description"`
	Version     int       `gorm:"type:integer;default:1" db:"version"`
	CreatedAt   time.Time `gorm:"autoCreateTime" db:"created_at"`
	UpdatedAt   time.Time `gorm:"autoUpdateTime" db:"updated_at"`
}

func (WorkflowMeta) TableName() string {
	return "workflow"
}

type Execution struct {
	ID           string         `gorm:"primaryKey;type:varchar(255)" db:"id"`
	WorkflowName string         `gorm:"type:varchar(255);not null;constraint:OnUpdate:CASCADE,OnDelete:CASCADE" db:"workflow_name"`
	Status       string         `gorm:"type:varchar(100);not null" db:"status"`
	NodeStates   datatypes.JSON `gorm:"type:jsonb;not null;serializer:json" db:"node_states"`
	Logs         string         `gorm:"type:text" db:"logs"`
	StartedAt    time.Time      `gorm:"autoCreateTime" db:"started_at"`
	CompletedAt  *time.Time     `gorm:"" db:"completed_at"`
}

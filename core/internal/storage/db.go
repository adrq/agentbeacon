package storage

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/jmoiron/sqlx"
	_ "github.com/lib/pq"
	_ "github.com/mattn/go-sqlite3"
	"gorm.io/driver/postgres"
	"gorm.io/driver/sqlite"
	"gorm.io/gorm"
	"gorm.io/gorm/schema"
)

type DB struct {
	*sqlx.DB
	driver string
	dsn    string
}

func Open(driver, dsn string) (*DB, error) {
	if driver == "sqlite3" && dsn != ":memory:" {
		dir := filepath.Dir(dsn)
		if err := os.MkdirAll(dir, 0755); err != nil {
			return nil, fmt.Errorf("failed to create database directory: %w", err)
		}
	}

	db, err := sqlx.Connect(driver, dsn)
	if err != nil {
		return nil, fmt.Errorf("failed to open database: %w", err)
	}

	wrapper := &DB{
		DB:     db,
		driver: driver,
		dsn:    dsn,
	}

	if err := wrapper.setSettings(); err != nil {
		wrapper.Close()
		return nil, fmt.Errorf("failed to set database settings: %w", err)
	}

	if err := wrapper.migrate(); err != nil {
		wrapper.Close()
		return nil, fmt.Errorf("failed to run migrations: %w", err)
	}

	if err := wrapper.initWorkflowsDir(); err != nil {
		wrapper.Close()
		return nil, fmt.Errorf("failed to initialize workflows directory: %w", err)
	}

	return wrapper, nil
}

func (db *DB) setSettings() error {
	if db.driver == "sqlite3" {
		return db.setSQLitePragmas()
	}
	return nil
}

func (db *DB) setSQLitePragmas() error {
	pragmas := []string{
		"PRAGMA foreign_keys = ON",
		"PRAGMA journal_mode = WAL",
		"PRAGMA synchronous = NORMAL",
		"PRAGMA cache_size = 1000",
		"PRAGMA temp_store = memory",
	}

	for _, pragma := range pragmas {
		if _, err := db.Exec(pragma); err != nil {
			return fmt.Errorf("failed to set pragma %s: %w", pragma, err)
		}
	}

	return nil
}

func (db *DB) placeholder(query string) string {
	if db.driver == "postgres" {
		result := ""
		paramNum := 1
		for _, char := range query {
			if char == '?' {
				result += fmt.Sprintf("$%d", paramNum)
				paramNum++
			} else {
				result += string(char)
			}
		}
		return result
	}
	return query
}

// Placeholder is the exported version of placeholder for external packages
func (db *DB) Placeholder(query string) string {
	return db.placeholder(query)
}

func (db *DB) Close() error {
	if db.DB == nil {
		return nil
	}
	return db.DB.Close()
}

func DefaultDBPath() string {
	homeDir, err := os.UserHomeDir()
	if err != nil {
		homeDir = "."
	}
	return filepath.Join(homeDir, ".agentmaestro", "agentmaestro.db")
}

func (db *DB) getWorkflowsDir() string {
	if db.driver == "sqlite3" {
		return filepath.Join(filepath.Dir(db.dsn), "workflows")
	}
	homeDir, _ := os.UserHomeDir()
	if homeDir == "" {
		homeDir = "."
	}
	return filepath.Join(homeDir, ".agentmaestro", "workflows")
}

func (db *DB) initWorkflowsDir() error {
	if db.driver == "sqlite3" && db.dsn == ":memory:" {
		return nil
	}
	return os.MkdirAll(db.getWorkflowsDir(), 0755)
}

func (db *DB) migrate() error {
	var gormDB *gorm.DB
	var err error

	config := &gorm.Config{
		NamingStrategy: schema.NamingStrategy{
			SingularTable: true,
		},
	}

	// For in-memory databases, we must reuse the existing connection
	// because each new connection gets its own separate database instance
	if db.driver == "sqlite3" && db.dsn == ":memory:" {
		// Use the existing connection for GORM
		gormDB, err = gorm.Open(sqlite.Dialector{Conn: db.DB.DB}, config)
	} else if db.driver == "postgres" {
		gormDB, err = gorm.Open(postgres.Open(db.dsn), config)
	} else {
		gormDB, err = gorm.Open(sqlite.Open(db.dsn), config)
	}

	if err != nil {
		return fmt.Errorf("failed to create GORM instance: %w", err)
	}

	if err := gormDB.AutoMigrate(&Config{}, &WorkflowMeta{}, &Execution{}); err != nil {
		return fmt.Errorf("failed to auto-migrate: %w", err)
	}

	sqlDB, err := gormDB.DB()
	if err != nil {
		return fmt.Errorf("failed to get underlying sql.DB: %w", err)
	}

	_, err = sqlDB.Exec("CREATE UNIQUE INDEX IF NOT EXISTS idx_config_name ON config (name)")
	if err != nil {
		return fmt.Errorf("failed to create unique index: %w", err)
	}

	return nil
}

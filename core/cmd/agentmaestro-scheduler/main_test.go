package main

import (
	"os"
	"testing"
)

func TestParseFlags(t *testing.T) {
	tests := []struct {
		name     string
		args     []string
		wantPort string
		wantDrv  string
		wantDSN  string
	}{
		{
			name:     "defaults",
			args:     []string{},
			wantPort: ":9456",
			wantDrv:  "sqlite3",
			wantDSN:  "", // Will be set to default path
		},
		{
			name:     "custom port",
			args:     []string{"-port", "8080"},
			wantPort: ":8080",
			wantDrv:  "sqlite3",
			wantDSN:  "",
		},
		{
			name:     "postgres driver",
			args:     []string{"-driver", "postgres", "-db", "postgres://localhost/test"},
			wantPort: ":9456",
			wantDrv:  "postgres",
			wantDSN:  "postgres://localhost/test",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			gotPort, gotDrv, gotDSN := parseFlags(tt.args)

			if gotPort != tt.wantPort {
				t.Errorf("parseFlags() port = %v, want %v", gotPort, tt.wantPort)
			}
			if gotDrv != tt.wantDrv {
				t.Errorf("parseFlags() driver = %v, want %v", gotDrv, tt.wantDrv)
			}

			// For DSN, if expected is empty, just check that we got some default value
			if tt.wantDSN == "" {
				if gotDSN == "" {
					t.Errorf("parseFlags() dsn should have default value, got empty")
				}
			} else if gotDSN != tt.wantDSN {
				t.Errorf("parseFlags() dsn = %v, want %v", gotDSN, tt.wantDSN)
			}
		})
	}
}

func TestParseFlagsWithEnv(t *testing.T) {
	// Set environment variable for postgres DSN
	oldEnv := os.Getenv("DATABASE_URL")
	os.Setenv("DATABASE_URL", "postgres://env-test/db")
	defer os.Setenv("DATABASE_URL", oldEnv)

	args := []string{"-driver", "postgres"}
	_, _, gotDSN := parseFlags(args)

	wantDSN := "postgres://env-test/db"
	if gotDSN != wantDSN {
		t.Errorf("parseFlags() with env DSN = %v, want %v", gotDSN, wantDSN)
	}
}

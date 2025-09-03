package storage

import (
	"path/filepath"
	"testing"
)

// TestParseRawWorkflowRef covers syntactic parsing without DB lookup.
func TestParseRawWorkflowRef(t *testing.T) {
	cases := []struct {
		in          string
		wantNS      string
		wantName    string
		wantVersion string // version spec ("latest" or explicit or empty for implicit latest)
		ok          bool
	}{
		{"alice/demo", "alice", "demo", "", true},
		{"alice/demo:latest", "alice", "demo", "latest", true},
		{"alice/demo:v1", "alice", "demo", "v1", true},
		{"alice/demo:abc-123_hash", "alice", "demo", "abc-123_hash", true},
		// Invalid forms
		{"", "", "", "", false},
		{"no-slash", "", "", "", false},
		{"alice/", "", "", "", false},
		{"/name", "", "", "", false},
		{"alice/demo:", "", "", "", false},
		{"alice/bad name", "", "", "", false},
		{"bad*ns/demo", "", "", "", false},
	}
	for _, c := range cases {
		ns, name, ver, err := parseRawWorkflowRef(c.in)
		if c.ok && err != nil {
			t.Errorf("%q expected ok, got error %v", c.in, err)
			continue
		}
		if !c.ok && err == nil {
			t.Errorf("%q expected error, got none", c.in)
			continue
		}
		if c.ok {
			if ns != c.wantNS || name != c.wantName || ver != c.wantVersion {
				t.Errorf("%q parsed mismatch got (%s,%s,%s) want (%s,%s,%s)", c.in, ns, name, ver, c.wantNS, c.wantName, c.wantVersion)
			}
		}
	}
}

// TestResolveWorkflowRef exercises DB-backed resolution for latest and explicit versions.
func TestResolveWorkflowRef(t *testing.T) {
	db, err := Open("sqlite3", filepath.Join(t.TempDir(), "ref.db"))
	if err != nil {
		t.Fatalf("open db: %v", err)
	}
	defer db.Close()

	// Seed two versions
	v1, err := db.RegisterInlineWorkflow("alice", "demo", "first", "name: demo\nnamespace: alice\nnodes: []\n")
	if err != nil {
		t.Fatalf("register v1: %v", err)
	}
	v2, err := db.RegisterInlineWorkflow("alice", "demo", "second", "name: demo\nnamespace: alice\ndescription: second\nnodes: []\n")
	if err != nil {
		t.Fatalf("register v2: %v", err)
	}

	cases := []struct {
		in          string
		wantVersion string
	}{
		{"alice/demo", v2.Version},               // implicit latest
		{"alice/demo:latest", v2.Version},        // explicit latest
		{"alice/demo:" + v1.Version, v1.Version}, // explicit v1
		{"alice/demo:" + v2.Version, v2.Version}, // explicit v2
	}
	for _, c := range cases {
		ref, wf, err := db.ResolveWorkflowRef(c.in)
		if err != nil {
			t.Errorf("resolve %q: %v", c.in, err)
			continue
		}
		if wf.Version != c.wantVersion || ref.Version != c.wantVersion {
			t.Errorf("%q expected version %s got wf=%s ref=%s", c.in, c.wantVersion, wf.Version, ref.Version)
		}
		if ref.Canonical != ("alice/demo:" + c.wantVersion) {
			t.Errorf("canonical mismatch for %q got %s", c.in, ref.Canonical)
		}
	}

	// Missing latest
	if _, _, err := db.ResolveWorkflowRef("bob/absent"); err == nil {
		t.Errorf("expected error resolving missing latest")
	}
	// Missing explicit version
	if _, _, err := db.ResolveWorkflowRef("alice/demo:does-not-exist"); err == nil {
		t.Errorf("expected error resolving missing explicit version")
	}
	// Malformed
	if _, _, err := db.ResolveWorkflowRef("badref"); err == nil {
		t.Errorf("expected error for malformed ref")
	}
}

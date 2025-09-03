package storage

// Reference parsing & resolution per spec section 7.
// Accepted raw forms:
//   ns/name            -> implicit latest
//   ns/name:latest     -> latest
//   ns/name:<version>  -> explicit version
// Returned canonical form always: ns/name:<version>

import (
	"errors"
	"fmt"
	"regexp"
	"strings"
)

var (
	// ErrMalformedRef indicates the supplied reference string doesn't match the allowed grammar.
	ErrMalformedRef = errors.New("malformed workflow reference")
)

// versionPattern is intentionally loose: allow common filename / uuid / hash friendly chars.
// We keep it restrictive enough to avoid whitespace or path traversal. Hyphen is allowed for UUIDs.
var versionPattern = regexp.MustCompile(`^[a-zA-Z0-9._-]{1,128}$`)

// WorkflowRef represents a resolved workflow version reference in canonical form.
type WorkflowRef struct {
	Namespace string
	Name      string
	Version   string // concrete resolved version (uuid or commit)
	Canonical string // Namespace/Name:Version
}

// parseRawWorkflowRef parses the textual form but does NOT hit storage.
// It returns: namespace, name, versionSpecifier ("latest" or explicit version or empty if implicit latest), error.
func parseRawWorkflowRef(raw string) (string, string, string, error) {
	if raw == "" {
		return "", "", "", ErrMalformedRef
	}
	// Expect exactly one '/' separating namespace and name(+suffix)
	if strings.Count(raw, "/") != 1 {
		return "", "", "", ErrMalformedRef
	}
	parts := strings.SplitN(raw, "/", 2)
	namespace, right := parts[0], parts[1]
	if namespace == "" || right == "" {
		return "", "", "", ErrMalformedRef
	}
	// Validate namespace & name base via identPattern from registry.go
	var name string
	var versionSpec string
	if strings.Contains(right, ":") {
		sub := strings.SplitN(right, ":", 2)
		name, versionSpec = sub[0], sub[1]
		if versionSpec == "" { // colon with empty version
			return "", "", "", ErrMalformedRef
		}
	} else {
		name = right
		versionSpec = "" // implicit latest
	}

	if !identPattern.MatchString(namespace) || !identPattern.MatchString(name) {
		return "", "", "", ErrMalformedRef
	}

	if versionSpec != "" && versionSpec != "latest" && !versionPattern.MatchString(versionSpec) {
		return "", "", "", ErrMalformedRef
	}

	return namespace, name, versionSpec, nil
}

// ResolveWorkflowRef resolves a raw reference into a concrete stored workflow version.
// If the reference points to latest (implicit or explicit) it fetches the row with is_latest=TRUE.
// If explicit version is given it fetches that version directly.
// The returned WorkflowRef always has the canonical form ns/name:version.
func (db *DB) ResolveWorkflowRef(raw string) (*WorkflowRef, *WorkflowVersion, error) {
	ns, name, versionSpec, err := parseRawWorkflowRef(raw)
	if err != nil {
		return nil, nil, err
	}

	var wf *WorkflowVersion
	if versionSpec == "" || versionSpec == "latest" { // resolve latest
		wf, err = db.GetLatestWorkflowVersion(ns, name)
		if err != nil {
			return nil, nil, fmt.Errorf("latest workflow version not found: %w", err)
		}
	} else { // explicit version
		wf, err = db.GetWorkflowVersion(ns, name, versionSpec)
		if err != nil {
			return nil, nil, fmt.Errorf("workflow version not found: %w", err)
		}
	}

	ref := &WorkflowRef{
		Namespace: ns,
		Name:      name,
		Version:   wf.Version,
		Canonical: fmt.Sprintf("%s/%s:%s", ns, name, wf.Version),
	}
	return ref, wf, nil
}

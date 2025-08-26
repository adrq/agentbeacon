package engine

import (
	"fmt"
	"strings"
)

// ValidateDAG validates that the given nodes form a valid DAG (no cycles)
func ValidateDAG(nodes []Node) error {
	if len(nodes) == 0 {
		return nil
	}

	// Build node ID set for quick lookup
	nodeIDs := make(map[string]bool)
	for _, node := range nodes {
		if nodeIDs[node.ID] {
			return ValidationError{
				Field:   "nodes",
				Message: fmt.Sprintf("duplicate node ID: %s", node.ID),
			}
		}
		nodeIDs[node.ID] = true
	}

	// Check for self-dependencies and missing dependencies
	for _, node := range nodes {
		for _, dep := range node.DependsOn {
			if dep == node.ID {
				return ValidationError{
					Field:   "nodes",
					Message: fmt.Sprintf("node %s has self dependency", node.ID),
				}
			}
			if !nodeIDs[dep] {
				return ValidationError{
					Field:   "nodes",
					Message: fmt.Sprintf("node %s depends on non-existent node: %s", node.ID, dep),
				}
			}
		}
	}

	// Build adjacency list
	adj := make(map[string][]string)
	for _, node := range nodes {
		adj[node.ID] = node.DependsOn
	}

	// Use DFS to detect cycles
	visited := make(map[string]bool)
	recStack := make(map[string]bool)

	for _, node := range nodes {
		if !visited[node.ID] {
			if hasCycleDFS(node.ID, adj, visited, recStack) {
				return ValidationError{
					Field:   "nodes",
					Message: "cycle detected in workflow dependencies",
				}
			}
		}
	}

	return nil
}

// hasCycleDFS performs DFS to detect cycles using recursion stack
func hasCycleDFS(nodeID string, adj map[string][]string, visited, recStack map[string]bool) bool {
	visited[nodeID] = true
	recStack[nodeID] = true

	for _, dep := range adj[nodeID] {
		if !visited[dep] {
			if hasCycleDFS(dep, adj, visited, recStack) {
				return true
			}
		} else if recStack[dep] {
			return true
		}
	}

	recStack[nodeID] = false
	return false
}

// TopologicalSort returns the execution order as groups of nodes that can run in parallel
func TopologicalSort(nodes []Node) ([][]string, error) {
	if len(nodes) == 0 {
		return [][]string{}, nil
	}

	// First validate the DAG
	if err := ValidateDAG(nodes); err != nil {
		// Convert ValidationError to a simpler error for TopologicalSort
		if strings.Contains(err.Error(), "cycle") {
			return nil, fmt.Errorf("cannot sort cyclic graph: cycle detected")
		}
		return nil, err
	}

	// Build adjacency lists and in-degree count
	adj := make(map[string][]string)        // node -> its dependencies
	dependents := make(map[string][]string) // node -> nodes that depend on it
	inDegree := make(map[string]int)        // node -> number of dependencies

	// Initialize all nodes
	for _, node := range nodes {
		adj[node.ID] = node.DependsOn
		inDegree[node.ID] = len(node.DependsOn)
		if dependents[node.ID] == nil {
			dependents[node.ID] = []string{}
		}
	}

	// Build dependents list
	for _, node := range nodes {
		for _, dep := range node.DependsOn {
			dependents[dep] = append(dependents[dep], node.ID)
		}
	}

	var result [][]string
	remaining := make(map[string]bool)
	for _, node := range nodes {
		remaining[node.ID] = true
	}

	// Kahn's algorithm for topological sorting with levels
	for len(remaining) > 0 {
		var currentLevel []string

		// Find all nodes with in-degree 0 in remaining nodes
		for nodeID := range remaining {
			if inDegree[nodeID] == 0 {
				currentLevel = append(currentLevel, nodeID)
			}
		}

		if len(currentLevel) == 0 {
			// This should not happen since we validated the DAG, but just in case
			return nil, fmt.Errorf("cannot sort cyclic graph: cycle detected")
		}

		// Add current level to result
		result = append(result, currentLevel)

		// Remove current level nodes and update in-degrees
		for _, nodeID := range currentLevel {
			delete(remaining, nodeID)
			// Reduce in-degree for all dependents
			for _, dependent := range dependents[nodeID] {
				if remaining[dependent] {
					inDegree[dependent]--
				}
			}
		}
	}

	return result, nil
}

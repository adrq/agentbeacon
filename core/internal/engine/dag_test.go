package engine

import (
	"strings"
	"testing"
)

func TestValidateDAGLinearChain(t *testing.T) {
	nodes := []Node{
		{ID: "A", Agent: "test-agent", Prompt: "Task A", DependsOn: []string{}},
		{ID: "B", Agent: "test-agent", Prompt: "Task B", DependsOn: []string{"A"}},
		{ID: "C", Agent: "test-agent", Prompt: "Task C", DependsOn: []string{"B"}},
	}

	err := ValidateDAG(nodes)
	if err != nil {
		t.Errorf("Linear chain should be valid, got error: %v", err)
	}
}

func TestValidateDAGParallelBranches(t *testing.T) {
	nodes := []Node{
		{ID: "fetch_data", Agent: "demo-agent", Prompt: "Fetch data", DependsOn: []string{}},
		{ID: "analyze_code", Agent: "demo-agent", Prompt: "Analyze code", DependsOn: []string{"fetch_data"}},
		{ID: "analyze_docs", Agent: "test-agent-2", Prompt: "Analyze docs", DependsOn: []string{"fetch_data"}},
		{ID: "generate_report", Agent: "demo-agent", Prompt: "Generate report", DependsOn: []string{"analyze_code", "analyze_docs"}},
	}

	err := ValidateDAG(nodes)
	if err != nil {
		t.Errorf("Parallel branches DAG should be valid, got error: %v", err)
	}
}
func TestValidateDAGDiamondPattern(t *testing.T) {
	nodes := []Node{
		{ID: "start", Agent: "agent", Prompt: "Start", DependsOn: []string{}},
		{ID: "branch1", Agent: "agent", Prompt: "Branch 1", DependsOn: []string{"start"}},
		{ID: "branch2", Agent: "agent", Prompt: "Branch 2", DependsOn: []string{"start"}},
		{ID: "merge", Agent: "agent", Prompt: "Merge", DependsOn: []string{"branch1", "branch2"}},
	}

	err := ValidateDAG(nodes)
	if err != nil {
		t.Errorf("Diamond pattern DAG should be valid, got error: %v", err)
	}
}

func TestValidateDAGSingleNode(t *testing.T) {
	nodes := []Node{
		{ID: "solo", Agent: "agent", Prompt: "Solo task", DependsOn: []string{}},
	}

	err := ValidateDAG(nodes)
	if err != nil {
		t.Errorf("Single node DAG should be valid, got error: %v", err)
	}
}

func TestValidateDAGEmptyNodes(t *testing.T) {
	nodes := []Node{}

	err := ValidateDAG(nodes)
	if err != nil {
		t.Errorf("Empty node list should be valid, got error: %v", err)
	}
}

func TestValidateDAGMultipleDisconnectedComponents(t *testing.T) {
	nodes := []Node{
		{ID: "A", Agent: "agent", Prompt: "Task A", DependsOn: []string{}},
		{ID: "B", Agent: "agent", Prompt: "Task B", DependsOn: []string{"A"}},
		{ID: "X", Agent: "agent", Prompt: "Task X", DependsOn: []string{}},
		{ID: "Y", Agent: "agent", Prompt: "Task Y", DependsOn: []string{"X"}},
	}

	err := ValidateDAG(nodes)
	if err != nil {
		t.Errorf("Multiple disconnected components should be valid, got error: %v", err)
	}
}

func TestValidateDAGSimpleCycle(t *testing.T) {
	nodes := []Node{
		{ID: "A", Agent: "agent", Prompt: "Task A", DependsOn: []string{"B"}},
		{ID: "B", Agent: "agent", Prompt: "Task B", DependsOn: []string{"A"}},
	}

	err := ValidateDAG(nodes)
	if err == nil {
		t.Error("Simple cycle should be detected as invalid")
	}

	// Should be a ValidationError about cycles
	if !strings.Contains(err.Error(), "cycle") {
		t.Errorf("Error should mention cycle, got: %v", err)
	}
}

func TestValidateDAGComplexCycle(t *testing.T) {
	nodes := []Node{
		{ID: "A", Agent: "agent", Prompt: "Task A", DependsOn: []string{"B"}},
		{ID: "B", Agent: "agent", Prompt: "Task B", DependsOn: []string{"C"}},
		{ID: "C", Agent: "agent", Prompt: "Task C", DependsOn: []string{"D"}},
		{ID: "D", Agent: "agent", Prompt: "Task D", DependsOn: []string{"B"}}, // Creates cycle
	}

	err := ValidateDAG(nodes)
	if err == nil {
		t.Error("Complex cycle should be detected as invalid")
	}

	if !strings.Contains(err.Error(), "cycle") {
		t.Errorf("Error should mention cycle, got: %v", err)
	}
}

func TestValidateDAGSelfDependency(t *testing.T) {
	nodes := []Node{
		{ID: "A", Agent: "agent", Prompt: "Task A", DependsOn: []string{"A"}}, // Self-dependency
		{ID: "B", Agent: "agent", Prompt: "Task B", DependsOn: []string{}},
	}

	err := ValidateDAG(nodes)
	if err == nil {
		t.Error("Self-dependency should be detected as invalid")
	}

	if !strings.Contains(err.Error(), "A") || !strings.Contains(err.Error(), "self") {
		t.Errorf("Error should mention self-dependency on node A, got: %v", err)
	}
}

func TestValidateDAGMissingDependency(t *testing.T) {
	nodes := []Node{
		{ID: "A", Agent: "agent", Prompt: "Task A", DependsOn: []string{"nonexistent"}},
		{ID: "B", Agent: "agent", Prompt: "Task B", DependsOn: []string{}},
	}

	err := ValidateDAG(nodes)
	if err == nil {
		t.Error("Missing dependency should be detected as invalid")
	}

	if !strings.Contains(err.Error(), "nonexistent") {
		t.Errorf("Error should mention missing node 'nonexistent', got: %v", err)
	}
}

func TestValidateDAGDuplicateNodeIDs(t *testing.T) {
	nodes := []Node{
		{ID: "A", Agent: "agent", Prompt: "Task A", DependsOn: []string{}},
		{ID: "A", Agent: "agent", Prompt: "Task A duplicate", DependsOn: []string{}}, // Duplicate ID
		{ID: "B", Agent: "agent", Prompt: "Task B", DependsOn: []string{}},
	}

	err := ValidateDAG(nodes)
	if err == nil {
		t.Error("Duplicate node IDs should be detected as invalid")
	}

	if !strings.Contains(err.Error(), "duplicate") || !strings.Contains(err.Error(), "A") {
		t.Errorf("Error should mention duplicate node ID 'A', got: %v", err)
	}
}

func TestTopologicalSortLinearChain(t *testing.T) {
	nodes := []Node{
		{ID: "C", Agent: "agent", Prompt: "Task C", DependsOn: []string{"B"}}, // Order doesn't matter
		{ID: "A", Agent: "agent", Prompt: "Task A", DependsOn: []string{}},
		{ID: "B", Agent: "agent", Prompt: "Task B", DependsOn: []string{"A"}},
	}

	result, err := TopologicalSort(nodes)
	if err != nil {
		t.Fatalf("TopologicalSort failed: %v", err)
	}

	expected := [][]string{{"A"}, {"B"}, {"C"}}
	if !equalGroups(result, expected) {
		t.Errorf("Linear chain sort mismatch: got %v, want %v", result, expected)
	}
}

func TestTopologicalSortParallelBranches(t *testing.T) {
	nodes := []Node{
		{ID: "fetch_data", Agent: "demo-agent", Prompt: "Fetch data", DependsOn: []string{}},
		{ID: "analyze_code", Agent: "demo-agent", Prompt: "Analyze code", DependsOn: []string{"fetch_data"}},
		{ID: "analyze_docs", Agent: "test-agent-2", Prompt: "Analyze docs", DependsOn: []string{"fetch_data"}},
		{ID: "generate_report", Agent: "demo-agent", Prompt: "Generate report", DependsOn: []string{"analyze_code", "analyze_docs"}},
	}

	result, err := TopologicalSort(nodes)
	if err != nil {
		t.Fatalf("TopologicalSort failed: %v", err)
	}

	// Should have 3 groups: [fetch_data], [analyze_code, analyze_docs], [generate_report]
	if len(result) != 3 {
		t.Errorf("Expected 3 execution groups, got %d", len(result))
	}

	// First group should only have fetch_data
	if len(result[0]) != 1 || result[0][0] != "fetch_data" {
		t.Errorf("First group should be [fetch_data], got %v", result[0])
	}

	// Second group should have analyze_code and analyze_docs (order doesn't matter)
	if len(result[1]) != 2 {
		t.Errorf("Second group should have 2 items, got %d", len(result[1]))
	}
	secondGroup := make(map[string]bool)
	for _, id := range result[1] {
		secondGroup[id] = true
	}
	if !secondGroup["analyze_code"] || !secondGroup["analyze_docs"] {
		t.Errorf("Second group should contain analyze_code and analyze_docs, got %v", result[1])
	}

	// Third group should only have generate_report
	if len(result[2]) != 1 || result[2][0] != "generate_report" {
		t.Errorf("Third group should be [generate_report], got %v", result[2])
	}
}

func TestTopologicalSortDiamondPattern(t *testing.T) {
	nodes := []Node{
		{ID: "start", Agent: "agent", Prompt: "Start", DependsOn: []string{}},
		{ID: "branch1", Agent: "agent", Prompt: "Branch 1", DependsOn: []string{"start"}},
		{ID: "branch2", Agent: "agent", Prompt: "Branch 2", DependsOn: []string{"start"}},
		{ID: "merge", Agent: "agent", Prompt: "Merge", DependsOn: []string{"branch1", "branch2"}},
	}

	result, err := TopologicalSort(nodes)
	if err != nil {
		t.Fatalf("TopologicalSort failed: %v", err)
	}

	// Should have 3 groups: [start], [branch1, branch2], [merge]
	if len(result) != 3 {
		t.Errorf("Expected 3 execution groups, got %d", len(result))
	}

	// Check each group
	if len(result[0]) != 1 || result[0][0] != "start" {
		t.Errorf("First group should be [start], got %v", result[0])
	}

	if len(result[1]) != 2 {
		t.Errorf("Second group should have 2 items, got %d", len(result[1]))
	}

	if len(result[2]) != 1 || result[2][0] != "merge" {
		t.Errorf("Third group should be [merge], got %v", result[2])
	}
}

func TestTopologicalSortSingleNode(t *testing.T) {
	nodes := []Node{
		{ID: "solo", Agent: "agent", Prompt: "Solo task", DependsOn: []string{}},
	}

	result, err := TopologicalSort(nodes)
	if err != nil {
		t.Fatalf("TopologicalSort failed: %v", err)
	}

	expected := [][]string{{"solo"}}
	if !equalGroups(result, expected) {
		t.Errorf("Single node sort mismatch: got %v, want %v", result, expected)
	}
}

func TestTopologicalSortDeepDAGWithBranch(t *testing.T) {
	// Test deep chain with branch: A → B → C → D
	//                                    ↘
	//                                      E
	nodes := []Node{
		{ID: "A", Agent: "agent", Prompt: "Task A", DependsOn: []string{}},
		{ID: "B", Agent: "agent", Prompt: "Task B", DependsOn: []string{"A"}},
		{ID: "C", Agent: "agent", Prompt: "Task C", DependsOn: []string{"B"}},
		{ID: "D", Agent: "agent", Prompt: "Task D", DependsOn: []string{"C"}},
		{ID: "E", Agent: "agent", Prompt: "Task E", DependsOn: []string{"B"}},
	}

	result, err := TopologicalSort(nodes)
	if err != nil {
		t.Fatalf("TopologicalSort failed: %v", err)
	}

	// Expected: [A], [B], [C, E], [D]
	if len(result) != 4 {
		t.Errorf("Expected 4 execution groups, got %d: %v", len(result), result)
	}

	// Level 1: A
	if len(result[0]) != 1 || result[0][0] != "A" {
		t.Errorf("First group should be [A], got %v", result[0])
	}

	// Level 2: B
	if len(result[1]) != 1 || result[1][0] != "B" {
		t.Errorf("Second group should be [B], got %v", result[1])
	}

	// Level 3: C and E (parallel - both depend on B)
	if len(result[2]) != 2 {
		t.Errorf("Third group should have 2 items, got %d: %v", len(result[2]), result[2])
	}
	thirdGroup := make(map[string]bool)
	for _, id := range result[2] {
		thirdGroup[id] = true
	}
	if !thirdGroup["C"] || !thirdGroup["E"] {
		t.Errorf("Third group should contain C and E, got %v", result[2])
	}

	// Level 4: D (depends on C, so must wait until after level 3)
	if len(result[3]) != 1 || result[3][0] != "D" {
		t.Errorf("Fourth group should be [D], got %v", result[3])
	}
}

func TestTopologicalSortNoDependencies(t *testing.T) {
	nodes := []Node{
		{ID: "A", Agent: "agent", Prompt: "Task A", DependsOn: []string{}},
		{ID: "B", Agent: "agent", Prompt: "Task B", DependsOn: []string{}},
		{ID: "C", Agent: "agent", Prompt: "Task C", DependsOn: []string{}},
	}

	result, err := TopologicalSort(nodes)
	if err != nil {
		t.Fatalf("TopologicalSort failed: %v", err)
	}

	// All nodes should be in a single group since they have no dependencies
	if len(result) != 1 {
		t.Errorf("Expected 1 execution group, got %d", len(result))
	}

	if len(result[0]) != 3 {
		t.Errorf("First group should have 3 items, got %d", len(result[0]))
	}

	// Verify all nodes are present
	nodeSet := make(map[string]bool)
	for _, id := range result[0] {
		nodeSet[id] = true
	}
	if !nodeSet["A"] || !nodeSet["B"] || !nodeSet["C"] {
		t.Errorf("All nodes A, B, C should be in first group, got %v", result[0])
	}
}

func TestTopologicalSortEmptyNodes(t *testing.T) {
	nodes := []Node{}

	result, err := TopologicalSort(nodes)
	if err != nil {
		t.Fatalf("TopologicalSort failed: %v", err)
	}

	if len(result) != 0 {
		t.Errorf("Empty nodes should return empty result, got %v", result)
	}
}

func TestTopologicalSortWithCycle(t *testing.T) {
	nodes := []Node{
		{ID: "A", Agent: "agent", Prompt: "Task A", DependsOn: []string{"B"}},
		{ID: "B", Agent: "agent", Prompt: "Task B", DependsOn: []string{"C"}},
		{ID: "C", Agent: "agent", Prompt: "Task C", DependsOn: []string{"A"}},
	}

	_, err := TopologicalSort(nodes)
	if err == nil {
		t.Error("TopologicalSort should fail on cyclic graph")
	}

	if !strings.Contains(err.Error(), "cycle") {
		t.Errorf("Error should mention cycle, got: %v", err)
	}
}

func TestTopologicalSortDisconnectedComponents(t *testing.T) {
	nodes := []Node{
		{ID: "A", Agent: "agent", Prompt: "Task A", DependsOn: []string{}},
		{ID: "B", Agent: "agent", Prompt: "Task B", DependsOn: []string{"A"}},
		{ID: "X", Agent: "agent", Prompt: "Task X", DependsOn: []string{}},
		{ID: "Y", Agent: "agent", Prompt: "Task Y", DependsOn: []string{"X"}},
	}

	result, err := TopologicalSort(nodes)
	if err != nil {
		t.Fatalf("TopologicalSort failed: %v", err)
	}

	// Should have 2 groups: [A, X], [B, Y]
	if len(result) != 2 {
		t.Errorf("Expected 2 execution groups, got %d", len(result))
	}

	// First group should have A and X (no dependencies)
	if len(result[0]) != 2 {
		t.Errorf("First group should have 2 items, got %d", len(result[0]))
	}

	// Verify first group contains A and X
	firstGroup := make(map[string]bool)
	for _, id := range result[0] {
		firstGroup[id] = true
	}
	if !firstGroup["A"] || !firstGroup["X"] {
		t.Errorf("First group should contain A and X, got %v", result[0])
	}

	// Second group should have B and Y (depend on first group)
	if len(result[1]) != 2 {
		t.Errorf("Second group should have 2 items, got %d", len(result[1]))
	}

	// Verify second group contains B and Y
	secondGroup := make(map[string]bool)
	for _, id := range result[1] {
		secondGroup[id] = true
	}
	if !secondGroup["B"] || !secondGroup["Y"] {
		t.Errorf("Second group should contain B and Y, got %v", result[1])
	}
}

func TestValidateDAGErrorTypes(t *testing.T) {
	// Test ValidationError is returned for cycles
	cycleNodes := []Node{
		{ID: "A", Agent: "agent", Prompt: "Task A", DependsOn: []string{"B"}},
		{ID: "B", Agent: "agent", Prompt: "Task B", DependsOn: []string{"A"}},
	}

	err := ValidateDAG(cycleNodes)
	if err == nil {
		t.Fatal("Expected error for cycle")
	}

	// Should be ValidationError with field "nodes"
	if !strings.Contains(err.Error(), "validation error") {
		t.Errorf("Expected ValidationError, got: %v", err)
	}
}

// TestTopologicalSortErrorTypes tests that proper error types are returned
func TestTopologicalSortErrorTypes(t *testing.T) {
	// Test with cycle
	cycleNodes := []Node{
		{ID: "A", Agent: "agent", Prompt: "Task A", DependsOn: []string{"B"}},
		{ID: "B", Agent: "agent", Prompt: "Task B", DependsOn: []string{"A"}},
	}

	_, err := TopologicalSort(cycleNodes)
	if err == nil {
		t.Fatal("Expected error for cycle")
	}

	if !strings.Contains(err.Error(), "cycle") {
		t.Errorf("Error should mention cycle, got: %v", err)
	}

	// TopologicalSort returns fmt.Errorf, not ValidationError
	if !strings.Contains(err.Error(), "cannot sort cyclic graph") {
		t.Errorf("Expected TopologicalSort error format, got: %v", err)
	}
}

// Helper function to compare execution groups (order within groups doesn't matter)
func equalGroups(a, b [][]string) bool {
	if len(a) != len(b) {
		return false
	}

	for i := range a {
		if len(a[i]) != len(b[i]) {
			return false
		}

		// Convert to sets for comparison
		setA := make(map[string]bool)
		setB := make(map[string]bool)

		for _, item := range a[i] {
			setA[item] = true
		}
		for _, item := range b[i] {
			setB[item] = true
		}

		for item := range setA {
			if !setB[item] {
				return false
			}
		}
		for item := range setB {
			if !setA[item] {
				return false
			}
		}
	}

	return true
}

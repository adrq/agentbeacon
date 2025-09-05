package main

import (
	"os"
	"testing"
)

func TestOrchestratorParseFlags(t *testing.T) {
	tests := []struct {
		name              string
		args              []string
		wantWorkers       int
		wantSchedulerPort int
	}{
		{
			name:              "default values",
			args:              []string{},
			wantWorkers:       2,
			wantSchedulerPort: 9456,
		},
		{
			name:              "custom workers",
			args:              []string{"-workers", "5"},
			wantWorkers:       5,
			wantSchedulerPort: 9456,
		},
		{
			name:              "custom scheduler port",
			args:              []string{"-scheduler-port", "8080"},
			wantWorkers:       2,
			wantSchedulerPort: 8080,
		},
		{
			name:              "both custom values",
			args:              []string{"-workers", "3", "-scheduler-port", "7000"},
			wantWorkers:       3,
			wantSchedulerPort: 7000,
		},
		{
			name:              "values in different order",
			args:              []string{"-scheduler-port", "5555", "-workers", "10"},
			wantWorkers:       10,
			wantSchedulerPort: 5555,
		},
		{
			name:              "single worker",
			args:              []string{"-workers", "1"},
			wantWorkers:       1,
			wantSchedulerPort: 9456,
		},
		{
			name:              "zero workers",
			args:              []string{"-workers", "0"},
			wantWorkers:       0,
			wantSchedulerPort: 9456,
		},
		{
			name:              "minimum port",
			args:              []string{"-scheduler-port", "1"},
			wantWorkers:       2,
			wantSchedulerPort: 1,
		},
		{
			name:              "high port number",
			args:              []string{"-scheduler-port", "65535"},
			wantWorkers:       2,
			wantSchedulerPort: 65535,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			workers, schedulerPort := parseFlags(tt.args)

			if workers != tt.wantWorkers {
				t.Errorf("parseFlags() workers = %v, want %v", workers, tt.wantWorkers)
			}

			if schedulerPort != tt.wantSchedulerPort {
				t.Errorf("parseFlags() schedulerPort = %v, want %v", schedulerPort, tt.wantSchedulerPort)
			}
		})
	}
}

func TestOrchestratorGetColor(t *testing.T) {
	tests := []struct {
		name         string
		input        string
		wantNonEmpty bool
	}{
		{
			name:         "empty string",
			input:        "",
			wantNonEmpty: true, // Even empty string should get a color if terminal
		},
		{
			name:         "scheduler",
			input:        "scheduler",
			wantNonEmpty: true,
		},
		{
			name:         "worker-1",
			input:        "worker-1",
			wantNonEmpty: true,
		},
		{
			name:         "worker-2",
			input:        "worker-2",
			wantNonEmpty: true,
		},
		{
			name:         "long process name",
			input:        "very-long-process-name-with-many-dashes",
			wantNonEmpty: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := getColor(tt.input)

			// If isTerminal() returns true, we should get a color
			// If isTerminal() returns false, we should get empty string
			terminalResult := isTerminal()

			if terminalResult {
				if tt.wantNonEmpty && result == "" {
					t.Errorf("getColor(%q) returned empty string when terminal detected", tt.input)
				}
				if result != "" {
					// Verify it's one of the expected colors
					validColor := false
					for _, color := range colors {
						if result == color {
							validColor = true
							break
						}
					}
					if !validColor {
						t.Errorf("getColor(%q) returned invalid color %q", tt.input, result)
					}
				}
			} else {
				if result != "" {
					t.Errorf("getColor(%q) returned %q when not a terminal, expected empty string", tt.input, result)
				}
			}
		})
	}
}

func TestOrchestratorGetColorDeterminism(t *testing.T) {
	// Test that same input always produces same output
	testCases := []string{
		"scheduler",
		"worker-1",
		"worker-2",
		"worker-10",
		"test",
		"",
		"a",
		"long-process-name-with-dashes",
		"special-chars-!@#$%",
		"unicode-test-αβγ",
	}

	for _, input := range testCases {
		t.Run("determinism_"+input, func(t *testing.T) {
			// Get color multiple times
			color1 := getColor(input)
			color2 := getColor(input)
			color3 := getColor(input)

			if color1 != color2 || color2 != color3 {
				t.Errorf("getColor(%q) is not deterministic: got %q, %q, %q", input, color1, color2, color3)
			}
		})
	}
}

func TestOrchestratorGetColorDistribution(t *testing.T) {
	// Test that different inputs generally produce different colors
	inputs := []string{
		"scheduler",
		"worker-1",
		"worker-2",
		"worker-3",
		"worker-4",
		"worker-5",
		"worker-6",
		"worker-7",
		"test-process-a",
		"test-process-b",
	}

	colors := make(map[string]string)
	for _, input := range inputs {
		colors[input] = getColor(input)
	}

	// Count unique colors (only if terminal outputs colors)
	uniqueColors := make(map[string]bool)
	nonEmptyColors := 0
	for _, color := range colors {
		if color != "" {
			uniqueColors[color] = true
			nonEmptyColors++
		}
	}

	// If we're getting colors (terminal mode), we should see some variety
	if nonEmptyColors > 0 {
		if len(uniqueColors) == 1 && nonEmptyColors > 1 {
			t.Errorf("All %d inputs mapped to same color - hash function may have issues", nonEmptyColors)
			for input, color := range colors {
				if color != "" {
					t.Logf("%s -> %q", input, color)
				}
			}
		}

		// With 6 colors available and 10 inputs, we should see some variety
		// (though hash collisions are mathematically possible)
		if len(uniqueColors) < 2 && nonEmptyColors >= 6 {
			t.Logf("Warning: Only %d unique colors for %d inputs (possible but unlikely)", len(uniqueColors), nonEmptyColors)
		}
	}
}

func TestOrchestratorGetColorHashCalculation(t *testing.T) {
	// Test that the hash calculation works as expected
	testCases := []struct {
		input        string
		expectedHash int
	}{
		{
			input:        "",
			expectedHash: 0,
		},
		{
			input:        "a",
			expectedHash: int('a'), // 97
		},
		{
			input:        "ab",
			expectedHash: int('a')*31 + int('b'), // 97*31 + 98 = 3105
		},
	}

	for _, tt := range testCases {
		t.Run("hash_"+tt.input, func(t *testing.T) {
			// Calculate hash manually to verify algorithm
			hash := 0
			for _, r := range tt.input {
				hash = hash*31 + int(r)
			}

			if hash != tt.expectedHash {
				t.Errorf("Manual hash calculation for %q: got %d, want %d", tt.input, hash, tt.expectedHash)
			}

			// Verify getColor uses same calculation
			color := getColor(tt.input)
			expectedIndex := hash % len(colors)

			if isTerminal() && color != "" {
				expectedColor := colors[expectedIndex]
				if color != expectedColor {
					t.Errorf("getColor(%q) = %q, want %q (hash=%d, index=%d)",
						tt.input, color, expectedColor, hash, expectedIndex)
				}
			}
		})
	}
}

func TestOrchestratorIsTerminal(t *testing.T) {
	// Test isTerminal function
	result := isTerminal()

	// The function should not panic and return a boolean
	t.Logf("isTerminal() returned: %v", result)

	// Test that the function is consistent across multiple calls
	result2 := isTerminal()
	if result != result2 {
		t.Errorf("isTerminal() is not consistent: first call = %v, second call = %v", result, result2)
	}

	// Test a few more times to ensure stability
	for i := 0; i < 5; i++ {
		if isTerminal() != result {
			t.Errorf("isTerminal() result changed between calls")
			break
		}
	}
}

func TestOrchestratorIsTerminalImplementation(t *testing.T) {
	// Test the core logic of isTerminal by examining os.Stdout.Stat()
	fileInfo, err := os.Stdout.Stat()
	if err != nil {
		t.Logf("os.Stdout.Stat() error: %v", err)
		// If Stat() fails, isTerminal should handle it gracefully
		result := isTerminal()
		// The function ignores the error and just checks the mode, so we can't predict the result
		t.Logf("isTerminal() with Stat() error: %v", result)
		return
	}

	// Check if os.ModeCharDevice bit is set
	isCharDevice := (fileInfo.Mode() & os.ModeCharDevice) != 0
	result := isTerminal()

	if result != isCharDevice {
		t.Errorf("isTerminal() = %v, but (fileInfo.Mode() & os.ModeCharDevice) != 0 = %v", result, isCharDevice)
	}

	t.Logf("File mode: %v, isCharDevice: %v, isTerminal(): %v", fileInfo.Mode(), isCharDevice, result)
}

func TestOrchestratorColorConstants(t *testing.T) {
	// Test that color constants are valid ANSI codes
	expectedColors := []string{
		"\033[1;32m", // Green
		"\033[1;34m", // Blue
		"\033[1;35m", // Magenta
		"\033[1;36m", // Cyan
		"\033[1;33m", // Yellow
		"\033[1;31m", // Red
	}

	if len(colors) != len(expectedColors) {
		t.Errorf("colors array length = %d, want %d", len(colors), len(expectedColors))
	}

	for i, expectedColor := range expectedColors {
		if i < len(colors) && colors[i] != expectedColor {
			t.Errorf("colors[%d] = %q, want %q", i, colors[i], expectedColor)
		}
	}

	// Test color reset constant
	expectedReset := "\033[0m"
	if colorReset != expectedReset {
		t.Errorf("colorReset = %q, want %q", colorReset, expectedReset)
	}
}

func TestOrchestratorGetColorEdgeCases(t *testing.T) {
	// Test edge cases for getColor function
	testCases := []struct {
		name  string
		input string
	}{
		{
			name:  "very long string",
			input: "this-is-a-very-long-process-name-that-should-still-work-correctly-with-the-hash-function-even-though-it-has-many-characters",
		},
		{
			name:  "string with numbers",
			input: "worker-123456789",
		},
		{
			name:  "string with special characters",
			input: "process_with-special.chars@domain.com",
		},
		{
			name:  "unicode characters",
			input: "αβγδε-worker",
		},
		{
			name:  "single character",
			input: "x",
		},
		{
			name:  "repeated characters",
			input: "aaaaaaaaaa",
		},
	}

	for _, tt := range testCases {
		t.Run(tt.name, func(t *testing.T) {
			// Should not panic and should return consistent results
			color1 := getColor(tt.input)
			color2 := getColor(tt.input)

			if color1 != color2 {
				t.Errorf("getColor(%q) not consistent: %q vs %q", tt.input, color1, color2)
			}

			// If we get a color (terminal mode), it should be valid
			if color1 != "" {
				validColor := false
				for _, validCol := range colors {
					if color1 == validCol {
						validColor = true
						break
					}
				}
				if !validColor {
					t.Errorf("getColor(%q) returned invalid color: %q", tt.input, color1)
				}
			}
		})
	}
}

// Benchmark tests for performance verification
func BenchmarkOrchestratorGetColor(b *testing.B) {
	testInputs := []string{
		"scheduler",
		"worker-1",
		"worker-2",
		"worker-10",
		"long-process-name-with-many-characters-to-test-performance",
	}

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		input := testInputs[i%len(testInputs)]
		getColor(input)
	}
}

func BenchmarkOrchestratorIsTerminal(b *testing.B) {
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		isTerminal()
	}
}

func BenchmarkOrchestratorParseFlags(b *testing.B) {
	args := []string{"-workers", "5", "-scheduler-port", "8080"}

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		parseFlags(args)
	}
}

func BenchmarkOrchestratorHashCalculation(b *testing.B) {
	input := "worker-12345"

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		// Replicate the hash calculation from getColor
		hash := 0
		for _, r := range input {
			hash = hash*31 + int(r)
		}
		_ = hash % len(colors)
	}
}

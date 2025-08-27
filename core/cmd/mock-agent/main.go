package main

import (
	"bufio"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"
	"time"
)

func main() {
	var configFile string
	flag.StringVar(&configFile, "config", "", "JSON file with custom responses")
	flag.Parse()

	// Load custom responses if config file provided
	responses := make(map[string]string)
	if configFile != "" {
		data, err := os.ReadFile(configFile)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Error reading config file: %v\n", err)
			os.Exit(1)
		}

		if err := json.Unmarshal(data, &responses); err != nil {
			fmt.Fprintf(os.Stderr, "Error parsing config JSON: %v\n", err)
			os.Exit(1)
		}
	}

	// Read prompts from stdin and respond on stdout
	scanner := bufio.NewScanner(os.Stdin)
	for scanner.Scan() {
		prompt := strings.TrimSpace(scanner.Text())

		// Check for custom response
		if response, exists := responses[prompt]; exists {
			// Handle special test responses
			if response == "HANG" {
				// Hang indefinitely for timeout testing
				time.Sleep(1 * time.Hour)
				continue
			}
			fmt.Println(response)
		} else {
			// Default response format
			fmt.Printf("Mock response: %s\n", prompt)
		}
	}

	if err := scanner.Err(); err != nil {
		fmt.Fprintf(os.Stderr, "Error reading from stdin: %v\n", err)
		os.Exit(1)
	}
}

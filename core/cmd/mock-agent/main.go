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
			// Handle built-in test commands
			if prompt == "HANG" {
				time.Sleep(1 * time.Hour)
				continue
			} else if strings.HasPrefix(prompt, "DELAY_") {
				delayStr := strings.TrimPrefix(prompt, "DELAY_")
				if delayStr != "" {
					var delay time.Duration
					switch delayStr {
					case "1":
						delay = 1 * time.Second
					case "2":
						delay = 2 * time.Second
					case "3":
						delay = 3 * time.Second
					case "5":
						delay = 5 * time.Second
					case "1500":
						delay = 1500 * time.Millisecond
					default:
						delay = 1 * time.Second
					}
					time.Sleep(delay)
					fmt.Printf("Mock response after %s delay: %s\n", delayStr, prompt)
					continue
				}
			} else if strings.Contains(prompt, "FAIL_NODE") {
				os.Exit(1)
			} else if strings.Contains(prompt, "FAIL_ONCE") {
				// Fail once then succeed - use simple counter based on timestamp
				now := time.Now().UnixNano()
				if now%2 == 0 { // Roughly 50% chance of failure
					fmt.Printf("Mock failure: %s\n", prompt)
					os.Exit(1)
				} else {
					fmt.Printf("Mock success after retry: %s\n", prompt)
				}
			} else {
				fmt.Printf("Mock response: %s\n", prompt)
			}
		}
	}

	if err := scanner.Err(); err != nil {
		fmt.Fprintf(os.Stderr, "Error reading from stdin: %v\n", err)
		os.Exit(1)
	}
}

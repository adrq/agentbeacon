// Stdin/stdout helpers for JSON Lines protocol communication.

import * as readline from "node:readline";
import type { Command, Event } from "./protocol.js";

/**
 * Create a readline-based command reader from stdin.
 * Returns an async iterable of parsed Command objects.
 * Malformed lines are logged to stderr and skipped.
 */
export function createCommandReader(
  input: NodeJS.ReadableStream = process.stdin,
): readline.Interface {
  return readline.createInterface({ input });
}

/**
 * Parse a raw line into a Command, returning null for malformed input.
 */
export function parseCommand(line: string): Command | null {
  try {
    return JSON.parse(line) as Command;
  } catch {
    process.stderr.write(
      `ignoring malformed stdin line, len=${line.length}\n`,
    );
    return null;
  }
}

/**
 * Write a protocol event as a JSON line to stdout.
 * Uses process.stdout.write directly (not console.log) to avoid
 * any console overrides and ensure clean JSON Lines output.
 */
export function emit(event: Event): void {
  try {
    process.stdout.write(JSON.stringify(event) + "\n");
  } catch {
    // Broken pipe — parent process is gone, nothing we can do
  }
}

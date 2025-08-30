import type { EnvironmentAdapter } from './types.js';
import { StandaloneAdapter } from './standalone.js';
import { VSCodeAdapter } from './vscode.js';

// Environment detection
export function detectEnvironment(): EnvironmentAdapter {
  // Check if we're running in VS Code webview
  if (window.acquireVsCodeApi) {
    return new VSCodeAdapter();
  }

  // Default to standalone browser
  return new StandaloneAdapter();
}

// Export the current environment adapter
export const environment = detectEnvironment();

// Export types and classes
export type { EnvironmentAdapter } from './types.js';
export { StandaloneAdapter } from './standalone.js';
export { VSCodeAdapter } from './vscode.js';

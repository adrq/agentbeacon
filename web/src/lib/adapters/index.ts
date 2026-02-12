import type { EnvironmentAdapter } from './types.js';
import { StandaloneAdapter } from './standalone.js';

export function detectEnvironment(): EnvironmentAdapter {
  return new StandaloneAdapter();
}

export const environment = detectEnvironment();

export type { EnvironmentAdapter } from './types.js';
export { StandaloneAdapter } from './standalone.js';

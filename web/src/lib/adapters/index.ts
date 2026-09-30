// SPDX-FileCopyrightText: Copyright 2025 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

import type { EnvironmentAdapter } from './types.js';
import { StandaloneAdapter } from './standalone.js';

export function detectEnvironment(): EnvironmentAdapter {
  return new StandaloneAdapter();
}

export const environment = detectEnvironment();

export type { EnvironmentAdapter } from './types.js';
export { StandaloneAdapter } from './standalone.js';

// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Credentials and stored browser state for E2E runs.
 */

import fs from 'fs';
import os from 'os';
import path from 'path';

export const E2E_USERNAME = process.env.AGENTBEACON_USERNAME ?? 'e2e-admin';
export const E2E_PASSWORD = process.env.AGENTBEACON_PASSWORD ?? '';

const STATE_DIR_ENV = 'AGENTBEACON_E2E_STATE_DIR';

/** This run's private state directory (mode 0700), created on first use and
 * shared with the worker processes through the environment. */
export function stateDir(): string {
  if (!process.env[STATE_DIR_ENV]) {
    process.env[STATE_DIR_ENV] = fs.mkdtempSync(path.join(os.tmpdir(), 'agentbeacon-e2e-'));
  }
  return process.env[STATE_DIR_ENV]!;
}

/** Where this run's logged-in browser state is stored. */
export function storageStatePath(): string {
  return path.join(stateDir(), 'state.json');
}

/** Write the browser state as a new file readable only by its owner. */
export function writeStorageState(state: unknown): void {
  fs.writeFileSync(storageStatePath(), JSON.stringify(state), { mode: 0o600, flag: 'wx' });
}

/** Log in through the API and return the parsed response. */
export async function login(apiURL: string, delivery: 'bearer' | 'cookie', label: string) {
  const res = await fetch(`${apiURL}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: E2E_USERNAME, password: E2E_PASSWORD, label, delivery }),
  });
  if (res.status !== 201) throw new Error(`login failed: ${res.status} ${await res.text()}`);
  return res;
}

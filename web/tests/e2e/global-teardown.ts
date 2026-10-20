// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Signs out the run's cookie and bearer logins and removes its state directory.
 */

import fs from 'fs';
import { request } from '@playwright/test';
import { stateDir, storageStatePath } from './auth';

export default async function globalTeardown() {
  const baseURL = process.env.BASE_URL ?? 'http://localhost:9456';
  const apiURL = process.env.API_URL ?? baseURL;
  try {
    if (fs.existsSync(storageStatePath())) {
      const context = await request.newContext({ baseURL, storageState: storageStatePath() });
      const res = await context.post('/api/v1/auth/logout', {
        headers: { Origin: new URL(baseURL).origin },
      });
      await context.dispose();
      if (res.status() !== 204) throw new Error(`cookie logout failed: ${res.status()}`);
    }
    const token = process.env.E2E_API_TOKEN;
    if (token) {
      const res = await fetch(`${apiURL}/api/v1/auth/logout`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.status !== 204) throw new Error(`bearer logout failed: ${res.status}`);
    }
  } finally {
    fs.rmSync(stateDir(), { recursive: true, force: true });
  }
}

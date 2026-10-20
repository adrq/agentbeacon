// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Logs in once per run: a cookie login stored as the browser state for every
 * page, and a bearer login for the API helpers.
 */

import { request } from '@playwright/test';
import { E2E_PASSWORD, E2E_USERNAME, login, writeStorageState } from './auth';

export default async function globalSetup() {
  const baseURL = process.env.BASE_URL ?? 'http://localhost:9456';
  const apiURL = process.env.API_URL ?? baseURL;

  const context = await request.newContext({ baseURL });
  const res = await context.post('/api/v1/auth/login', {
    data: { username: E2E_USERNAME, password: E2E_PASSWORD, label: 'playwright', delivery: 'cookie' },
  });
  if (res.status() !== 201) throw new Error(`login failed: ${res.status()} ${await res.text()}`);
  writeStorageState(await context.storageState());
  await context.dispose();

  const bearer = await login(apiURL, 'bearer', 'playwright-api');
  process.env.E2E_API_TOKEN = (await bearer.json()).token;
}

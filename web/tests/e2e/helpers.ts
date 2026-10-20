// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

/**
 * Shared E2E test helpers.
 */

export const API_URL = process.env.API_URL ?? 'http://localhost:9456';

/** `fetch` against the API with the run's bearer login. */
export async function apiFetch(path: string, init: RequestInit = {}) {
  const headers = new Headers(init.headers);
  if (!headers.has('Authorization') && process.env.E2E_API_TOKEN) {
    headers.set('Authorization', `Bearer ${process.env.E2E_API_TOKEN}`);
  }
  return fetch(`${API_URL}${path}`, { ...init, headers });
}

export async function apiPost(path: string, body: unknown) {
  const res = await apiFetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`API ${path} failed: ${res.status}`);
  return res.json();
}

export async function apiGet(path: string) {
  const res = await apiFetch(path);
  if (!res.ok) throw new Error(`API ${path} failed: ${res.status}`);
  return res.json();
}

export async function apiDelete(path: string) {
  const res = await apiFetch(path, { method: 'DELETE' });
  if (!res.ok && res.status !== 404) throw new Error(`API ${path} failed: ${res.status}`);
}

/** Find or create a driver for the given platform, return its id. */
export async function ensureDriver(platform: string): Promise<string> {
  const drivers: { id: string; platform: string }[] = await apiGet('/api/v1/drivers');
  const existing = drivers.find(d => d.platform === platform);
  if (existing) return existing.id;
  const result = await apiPost('/api/v1/drivers', { name: platform, platform });
  return result.id;
}

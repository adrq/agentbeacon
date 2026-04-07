/**
 * Shared E2E test helpers.
 */

export const API_URL = process.env.API_URL ?? 'http://localhost:9456';

export async function apiPost(path: string, body: unknown) {
  const res = await fetch(`${API_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`API ${path} failed: ${res.status}`);
  return res.json();
}

export async function apiGet(path: string) {
  const res = await fetch(`${API_URL}${path}`);
  if (!res.ok) throw new Error(`API ${path} failed: ${res.status}`);
  return res.json();
}

export async function apiDelete(path: string) {
  const res = await fetch(`${API_URL}${path}`, { method: 'DELETE' });
  if (!res.ok && res.status !== 404) throw new Error(`API ${path} failed: ${res.status}`);
}

/** Find or create a driver for the given platform, return its id. */
export async function ensureDriver(platform: string): Promise<string> {
  const drivers: { id: string; platform: string }[] = await apiGet('/api/drivers');
  const existing = drivers.find(d => d.platform === platform);
  if (existing) return existing.id;
  const result = await apiPost('/api/drivers', { name: platform, platform });
  return result.id;
}

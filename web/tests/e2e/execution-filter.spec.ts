import { test, expect } from '@playwright/test';

const API_URL = process.env.API_URL ?? 'http://localhost:9456';

async function apiPost(path: string, body: unknown) {
  const res = await fetch(`${API_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`API ${path} failed: ${res.status}`);
  return res.json();
}

async function apiGet(path: string) {
  const res = await fetch(`${API_URL}${path}`);
  if (!res.ok) throw new Error(`API ${path} failed: ${res.status}`);
  return res.json();
}

async function ensureAgent(): Promise<{ id: string; name: string }> {
  const agents: { id: string; name: string }[] = await apiGet('/api/agents');
  if (agents.length > 0) return agents[0];
  throw new Error('No agents seeded');
}

test('filter execution list by project', async ({ page }) => {
  const agent = await ensureAgent();
  const projectA = await apiPost('/api/projects', { name: 'Filter A', path: '/tmp' });
  const projectB = await apiPost('/api/projects', { name: 'Filter B', path: '/tmp' });

  // Create executions in both projects
  await apiPost('/api/executions', {
    agent_id: agent.id,
    prompt: 'Task for A',
    title: 'Exec A',
    project_id: projectA.id,
  });
  await apiPost('/api/executions', {
    agent_id: agent.id,
    prompt: 'Task for B',
    title: 'Exec B',
    project_id: projectB.id,
  });

  await page.goto('/');

  // Wait for executions to load
  await expect(page.getByText('Exec A')).toBeVisible({ timeout: 10000 });
  await expect(page.getByText('Exec B')).toBeVisible();

  // Filter by project A
  const filter = page.getByLabel('Filter by project');
  await filter.selectOption(projectA.id);

  // Exec A should remain visible; Exec B should be filtered out
  await expect(page.getByText('Exec A')).toBeVisible({ timeout: 10000 });
  await expect(page.getByText('Exec B')).not.toBeVisible({ timeout: 10000 });

  // Reset filter
  await filter.selectOption('');

  // Both visible again
  await expect(page.getByText('Exec A')).toBeVisible({ timeout: 10000 });
  await expect(page.getByText('Exec B')).toBeVisible({ timeout: 10000 });
});

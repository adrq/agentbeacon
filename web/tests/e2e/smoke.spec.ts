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
  throw new Error('No agents seeded — run scripts/seed_agents.py first');
}

async function claimSession() {
  await apiPost('/api/worker/sync', {
    worker_id: 'playwright-worker',
    active_sessions: [],
    capacity: 5,
  });
}

async function sendQuestion(sessionId: string, question: string, options: { label: string; description: string }[]) {
  const res = await fetch(`${API_URL}/mcp`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${sessionId}`,
    },
    body: JSON.stringify({
      jsonrpc: '2.0',
      id: 1,
      method: 'tools/call',
      params: {
        name: 'ask_user',
        arguments: {
          questions: [{
            question,
            header: 'Test',
            options,
            multiSelect: false,
          }],
        },
      },
    }),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new Error(`MCP ask_user failed: ${res.status} ${text}`);
  }
}

test('app loads with header and new button', async ({ page }) => {
  await page.goto('/');

  await expect(page.getByText('AgentBeacon')).toBeVisible();
  await expect(page.getByRole('button', { name: '+ New' })).toBeVisible();
});

test('theme toggle switches between light and dark', async ({ page }) => {
  await page.goto('/');

  const toggle = page.getByRole('button', { name: /theme/i });
  await expect(toggle).toBeVisible();

  await toggle.click();
  await expect(page.getByRole('button', { name: /theme/i })).toBeVisible();
});

test('create execution via modal', async ({ page }) => {
  const agent = await ensureAgent();

  await page.goto('/');
  await page.getByRole('button', { name: '+ New' }).click();

  await expect(page.getByRole('dialog', { name: 'New Execution' })).toBeVisible();

  await page.getByLabel('Agent').selectOption(agent.name);
  await page.getByRole('textbox', { name: 'Task' }).fill('Playwright smoke test task');
  await page.getByRole('textbox', { name: /title/i }).fill('Smoke test');

  await page.getByRole('button', { name: 'Start' }).click();

  await expect(page.getByRole('dialog')).not.toBeVisible({ timeout: 5000 });
  await expect(page.getByRole('heading', { name: 'Smoke test' })).toBeVisible();
  await expect(page.getByText('Submitted', { exact: true })).toBeVisible();
});

test('full question-answer flow', async ({ page }) => {
  const agent = await ensureAgent();

  const exec = await apiPost('/api/executions', {
    agent_id: agent.id,
    prompt: 'E2E question flow test',
    title: 'Q&A flow test',
  });
  const execId = exec.execution_id;
  const sessionId = exec.session_id;

  await claimSession();

  await sendQuestion(sessionId, 'Which framework should we use for this project?', [
    { label: 'React', description: 'Component-based UI library' },
    { label: 'Svelte', description: 'Compile-time reactive framework' },
  ]);

  await page.goto(`/#/execution/${execId}`);

  // Wait for question to appear via polling
  const questionCard = page.locator('.question-card');
  await expect(questionCard).toBeVisible({ timeout: 10000 });

  await expect(page.getByRole('radio', { name: /React/ })).toBeVisible();
  await expect(page.getByRole('radio', { name: /Svelte/ })).toBeVisible();
  await expect(page.getByRole('radio', { name: /Decide for me/ })).toBeVisible();

  await expect(page.getByRole('button', { name: /Submit/ })).toBeDisabled();

  await page.getByRole('radio', { name: /Svelte/ }).click();
  await expect(page.getByRole('button', { name: /Submit/ })).toBeEnabled();

  await page.getByRole('button', { name: /Submit/ }).click();

  await expect(page.getByText('User: Svelte')).toBeVisible({ timeout: 10000 });
});

import { test, expect } from '@playwright/test';
import { apiPost, apiGet, ensureDirectAgent, API_URL } from './helpers';

async function ensureProject(): Promise<{ id: string; name: string }> {
  const projects: { id: string; name: string }[] = await apiGet('/api/projects');
  if (projects.length > 0) return projects[0];
  const result = await apiPost('/api/projects', { name: 'smoke-test', path: '/tmp' });
  return { id: result.id, name: result.name };
}

async function claimSession() {
  await apiPost('/api/worker/sync', {});
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

  const toggle = page.getByRole('button', { name: /Activate .+ theme/ });
  await expect(toggle).toBeVisible();

  await toggle.click();
  await expect(page.getByRole('button', { name: /Activate .+ theme/ })).toBeVisible();
});

test('create execution via modal', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const project = await ensureProject();

  await page.goto('/');
  await page.getByRole('button', { name: '+ New' }).click();

  const dialog = page.getByRole('dialog', { name: 'New Execution' });
  await expect(dialog).toBeVisible();

  await dialog.getByLabel('Project', { exact: true }).selectOption(project.id);
  await dialog.getByLabel('Agent').selectOption(agent.id);

  await page.getByRole('textbox', { name: 'Task' }).fill('Playwright smoke test task');
  await page.getByRole('textbox', { name: /title/i }).fill('Smoke test');

  await page.getByRole('button', { name: 'Start' }).click();

  await expect(page.getByRole('dialog')).not.toBeVisible({ timeout: 5000 });
  await expect(page.getByRole('heading', { name: 'Smoke test' })).toBeVisible();
  await expect(page.getByText('Submitted', { exact: true })).toBeVisible();
});

test('full question-answer flow', async ({ page }) => {
  const agent = await ensureDirectAgent();

  const exec = await apiPost('/api/executions', {
    agent_id: agent.id,
    prompt: 'E2E question flow test',
    title: 'Q&A flow test',
    cwd: '/tmp',
  });
  const execId = exec.execution.id;
  const sessionId = exec.session_id;

  await claimSession();

  await sendQuestion(sessionId, 'Which framework should we use for this project?', [
    { label: 'React', description: 'Component-based UI library' },
    { label: 'Svelte', description: 'Compile-time reactive framework' },
  ]);

  await page.goto(`/#/execution/${execId}`);

  // Question appears in the QuestionBanner within the execution detail
  const banner = page.locator('.question-banner');
  await expect(banner).toBeVisible({ timeout: 10000 });

  // Scope radio/submit to the banner to avoid matching the ActionPanel's DecisionCard
  await expect(banner.getByRole('radio', { name: /React/ })).toBeVisible();
  await expect(banner.getByRole('radio', { name: /Svelte/ })).toBeVisible();
  await expect(banner.getByRole('radio', { name: /Decide for me/ })).toBeVisible();

  await expect(banner.getByRole('button', { name: /Submit/ })).toBeDisabled();

  await banner.getByRole('radio', { name: /Svelte/ }).click();
  await expect(banner.getByRole('button', { name: /Submit/ })).toBeEnabled();

  await banner.getByRole('button', { name: /Submit/ }).click();

  await expect(page.getByText('User: Svelte')).toBeVisible({ timeout: 10000 });
});

test('navigation between views', async ({ page }) => {
  await page.goto('/');

  await page.getByRole('button', { name: 'Projects' }).click();
  await expect(page.getByRole('heading', { name: 'Projects' })).toBeVisible();

  await page.getByRole('button', { name: 'Agents' }).click();
  await expect(page.getByRole('heading', { name: 'Agents' })).toBeVisible();

  await page.getByRole('button', { name: 'Executions' }).click();
});

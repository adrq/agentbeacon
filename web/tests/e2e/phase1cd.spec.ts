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

async function ensureDemoAgent(): Promise<{ id: string; name: string }> {
  const agents: { id: string; name: string; agent_type?: string }[] = await apiGet('/api/agents');
  const demo = agents.find(a => a.name === 'Demo Agent');
  if (!demo) throw new Error('Demo Agent not found — run scripts/seed_agents.py first');
  if (demo.agent_type && demo.agent_type !== 'acp') {
    throw new Error(`Demo Agent has unexpected type: ${demo.agent_type}`);
  }
  return { id: demo.id, name: demo.name };
}

/**
 * ACP mock agent without --scenario flag, for testing special commands
 * (SEND_MARKDOWN, SEND_TOOL_CALL, SEND_THOUGHT, SEND_PLAN).
 * Still a mock agent — never calls real AI services.
 */
async function ensureDirectAgent(): Promise<{ id: string; name: string }> {
  const agents: { id: string; name: string }[] = await apiGet('/api/agents');
  const existing = agents.find(a => a.name === 'Mock Agent (Direct)');
  if (existing) return { id: existing.id, name: existing.name };

  const result = await apiPost('/api/agents', {
    name: 'Mock Agent (Direct)',
    agent_type: 'acp',
    description: 'Mock ACP agent without scenario for special command tests',
    config: {
      command: 'uv',
      args: ['run', 'python', '-m', 'agentmaestro.mock_agent', '--mode', 'acp'],
      timeout: 60,
    },
  });
  return { id: result.id, name: result.name };
}

async function createExecution(agentId: string, prompt: string, title: string) {
  const exec = await apiPost('/api/executions', {
    agent_id: agentId,
    prompt,
    title,
    cwd: '/tmp',
  });
  cleanupExecIds.push(exec.execution.id);
  return { execId: exec.execution.id, sessionId: exec.session_id };
}

/** Wait for external worker to pick up session (polls every 500ms). */
async function waitForWorkerPickup(execId: string, timeoutMs = 10000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const data = await apiGet(`/api/executions/${execId}`);
    const status = data.execution?.status ?? data.status;
    if (status === 'working' || status === 'input-required' || status === 'completed') return;
    await new Promise(r => setTimeout(r, 500));
  }
  throw new Error(`Worker did not pick up execution ${execId} within ${timeoutMs}ms`);
}

// Track execution IDs for cleanup — workers get stuck on input-required sessions
const cleanupExecIds: string[] = [];

test.afterEach(async () => {
  for (const id of cleanupExecIds) {
    try {
      await apiPost(`/api/executions/${id}/cancel`, {});
    } catch { /* best effort */ }
  }
  cleanupExecIds.length = 0;
});

// --- Test 1: Markdown rendering via SEND_MARKDOWN ---

test('markdown rendering: heading, table, code block with syntax highlighting', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_MARKDOWN', 'Markdown test');
  await waitForWorkerPickup(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Wait for markdown to fully render (h1 only exists after the unified pipeline runs)
  const markdown = page.locator('.agent-bubble .markdown-body');
  await expect(markdown.locator('h1')).toBeVisible({ timeout: 15000 });

  // Table rendered as HTML element
  await expect(markdown.locator('table')).toBeVisible();

  // Code block with syntax highlighting
  await expect(markdown.locator('pre')).toBeVisible();
  await expect(page.locator('.shiki').first()).toBeVisible();

  // Heading rendered as styled element, not literal markdown syntax
  const text = await markdown.first().textContent() ?? '';
  expect(text).not.toContain('# Analysis Report');
  expect(text).toContain('Analysis Report');
});

// --- Test 2: Structured tool_call rendering ---

test('tool_call renders with gear icon in log view', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Tool call test');
  await waitForWorkerPickup(execId);

  await page.goto(`/#/execution/${execId}`);

  const entry = page.locator('.timeline-entry').filter({ hasText: 'Read file config.json' });
  await expect(entry).toBeVisible({ timeout: 15000 });
  await expect(entry.locator('.ev-icon')).toContainText('\u2699');
});

// --- Test 3: Thinking data rendering ---

test('thinking renders with ellipsis icon', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_THOUGHT', 'Thought test');
  await waitForWorkerPickup(execId);

  await page.goto(`/#/execution/${execId}`);

  const entry = page.locator('.timeline-entry').filter({ hasText: 'analyze the code structure' });
  await expect(entry).toBeVisible({ timeout: 15000 });
  await expect(entry.locator('.ev-icon')).toContainText('\u22EF');
});

// --- Test 4: Plan data rendering ---

test('plan renders with hamburger icon and step count', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_PLAN', 'Plan test');
  await waitForWorkerPickup(execId);

  await page.goto(`/#/execution/${execId}`);

  const entry = page.locator('.timeline-entry').filter({ hasText: 'Plan (2 steps)' });
  await expect(entry).toBeVisible({ timeout: 15000 });
  await expect(entry.locator('.ev-icon')).toContainText('\u2630');
});

// --- Test 5: Full question-answer flow (demo scenario) ---

test('demo scenario: question banner, options, submit, agent resumes', async ({ page }) => {
  const agent = await ensureDemoAgent();
  const { execId } = await createExecution(agent.id, 'Test question flow', 'Q&A flow test');
  await waitForWorkerPickup(execId);

  await page.goto(`/#/execution/${execId}`);

  // Wait for question banner (demo Phase 0 takes ~2-3s with default delays)
  const banner = page.locator('.question-banner');
  await expect(banner).toBeVisible({ timeout: 20000 });

  // Question text from demo scenario
  await expect(page.locator('.question-text')).toContainText('Which approach should I take?');

  // 3 scenario options + Other + Decide for me
  await expect(page.getByRole('radio', { name: /Refactor existing code/ })).toBeVisible();
  await expect(page.getByRole('radio', { name: /Write new module/ })).toBeVisible();
  await expect(page.getByRole('radio', { name: /Add a wrapper/ })).toBeVisible();
  await expect(page.getByRole('radio', { name: /Decide for me/ })).toBeVisible();

  // Execution title always displayed in banner
  await expect(banner.locator('.banner-meta')).toContainText('Q&A flow test');

  // Submit disabled before selection
  await expect(page.getByRole('button', { name: /Submit/ })).toBeDisabled();

  // Select and submit
  await page.getByRole('radio', { name: /Refactor existing code/ }).click();
  await expect(page.getByRole('button', { name: /Submit/ })).toBeEnabled();
  await page.getByRole('button', { name: /Submit/ }).click();

  // Submitted state
  await expect(page.locator('.submitted-banner')).toBeVisible({ timeout: 5000 });

  // Wait for agent Phase 1 to complete
  await expect(
    page.locator('.timeline-entry').filter({ hasText: 'Done!' })
  ).toBeVisible({ timeout: 15000 });

  // Switch to Chat view
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Agent messages rendered via Markdown component
  await expect(page.locator('.agent-bubble .markdown-body').first()).toBeVisible({ timeout: 5000 });

  // User answer appears as plain text in user bubble
  await expect(page.locator('.user-bubble')).toBeVisible();
});

// --- Test 6: Chat/Log toggle preserves data ---

test('chat/log toggle preserves events', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'Toggle test', 'Toggle test');
  await waitForWorkerPickup(execId);

  await page.goto(`/#/execution/${execId}`);

  // Wait for agent response in Log view (direct agent completes quickly)
  const responseEntry = page.locator('.timeline-entry').filter({ hasText: 'Mock ACP response' });
  await expect(responseEntry).toBeVisible({ timeout: 15000 });

  const logCount = await page.locator('.timeline-entry').count();
  expect(logCount).toBeGreaterThan(0);

  // Switch to Chat: agent bubble(s) visible
  await page.getByRole('tab', { name: 'Chat' }).click();
  await expect(page.locator('.chat-row').first()).toBeVisible({ timeout: 5000 });

  // Switch back to Log: same entries present
  await page.getByRole('tab', { name: 'Log' }).click();
  const logCountAfter = await page.locator('.timeline-entry').count();
  expect(logCountAfter).toBeGreaterThanOrEqual(logCount);
});

// --- Test 7: Theme toggle preserves markdown rendering ---

test('theme toggle preserves syntax highlighting', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_MARKDOWN', 'Theme test');
  await waitForWorkerPickup(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Wait for shiki syntax highlighting
  await expect(page.locator('.shiki').first()).toBeVisible({ timeout: 15000 });

  // Toggle theme (use aria-label which is "Activate light theme" or "Activate dark theme")
  const themeBtn = page.getByRole('button', { name: /Activate .+ theme/ });
  await themeBtn.click();

  // Syntax highlighting survives toggle
  await expect(page.locator('.shiki').first()).toBeVisible();

  // Toggle back
  await page.getByRole('button', { name: /Activate .+ theme/ }).click();
  await expect(page.locator('.shiki').first()).toBeVisible();
});

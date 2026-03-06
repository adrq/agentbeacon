import { test, expect } from '@playwright/test';
import {
  ensureDirectAgent, ensureDemoAgent, ensureShowcaseAgent, ensureTCLeadAgent,
  ensureTCChildAgent, createExecution,
  waitForWorkerIdle, waitForWorkerPickup, waitForTurnEnd, waitForEvent,
} from './helpers';

test.beforeAll(async () => {
  await waitForWorkerIdle();
});

test.afterEach(async () => {
  await waitForWorkerIdle();
});

// --- Test 1: Markdown rendering via SEND_MARKDOWN ---

test('markdown rendering: heading, table, code block with syntax highlighting', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_MARKDOWN', 'Markdown test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  const markdown = page.locator('.agent-prose .markdown-body');
  await expect(markdown.locator('h1')).toBeVisible({ timeout: 10000 });

  await expect(markdown.locator('table')).toBeVisible();

  await expect(markdown.locator('pre')).toBeVisible();
  await expect(page.locator('.shiki').first()).toBeVisible();

  const text = await markdown.first().textContent() ?? '';
  expect(text).not.toContain('# Analysis Report');
  expect(text).toContain('Analysis Report');
});

// --- Test 2: Structured tool_call rendering ---

test('tool_call renders with gear icon in log view and enriched card in chat', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Tool call test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);

  // Log view: gear icon + title
  const entry = page.locator('.timeline-entry').filter({ hasText: 'Read file config.json' });
  await expect(entry).toBeVisible({ timeout: 10000 });
  await expect(entry.locator('.ev-icon')).toContainText('\u2699');

  // Chat view: enriched ToolGroup with collapsible content
  await page.getByRole('tab', { name: 'Chat' }).click();
  const toolCard = page.locator('.tool-group').first();
  await expect(toolCard).toBeVisible({ timeout: 10000 });
  await expect(toolCard).toContainText('Read file config.json');
  await expect(toolCard.locator('details')).toBeVisible();
});

// --- Test 3: Thinking data rendering ---

test('thinking renders with ellipsis icon', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_THOUGHT', 'Thought test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);

  const entry = page.locator('.timeline-entry').filter({ hasText: 'analyze the code structure' });
  await expect(entry).toBeVisible({ timeout: 10000 });
  await expect(entry.locator('.ev-icon')).toContainText('\u22EF');
});

// --- Test 4: Plan data rendering ---

test('plan renders with hamburger icon and step count', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_PLAN', 'Plan test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);

  const entry = page.locator('.timeline-entry').filter({ hasText: 'Plan (2 steps)' });
  await expect(entry).toBeVisible({ timeout: 10000 });
  await expect(entry.locator('.ev-icon')).toContainText('\u2630');
});

// --- Test 5: Full question-answer flow (demo scenario) ---
// Requires Demo Agent pre-seeded via scripts/seed_agents.py (run by scripts/e2e.sh).

test('demo scenario: question banner, options, submit, agent resumes', async ({ page }) => {
  await waitForWorkerIdle();

  const agent = await ensureDemoAgent();
  const { execId } = await createExecution(agent.id, 'Test question flow', 'Q&A flow test');
  // Demo agent is heavier than Direct — needs subprocess spawn + scenario init
  await waitForWorkerPickup(execId, 15000);

  await page.goto(`/#/execution/${execId}`);

  const banner = page.locator('.question-banner');
  await expect(banner).toBeVisible({ timeout: 20000 });

  const main = page.locator('#main-content');
  await expect(main.locator('.question-text')).toContainText('Which approach should I take?');

  await expect(main.getByRole('radio', { name: /Refactor existing code/ })).toBeVisible();
  await expect(main.getByRole('radio', { name: /Write new module/ })).toBeVisible();
  await expect(main.getByRole('radio', { name: /Add a wrapper/ })).toBeVisible();
  await expect(main.getByRole('radio', { name: /Decide for me/ })).toBeVisible();

  await expect(banner.locator('.banner-meta')).toContainText('Q&A flow test');

  await expect(main.getByRole('button', { name: /Submit/ })).toBeDisabled();

  await main.getByRole('radio', { name: /Refactor existing code/ }).click();
  await expect(main.getByRole('button', { name: /Submit/ })).toBeEnabled();
  await main.getByRole('button', { name: /Submit/ }).click();

  // Demo Agent now asks a second question after the first answer.
  // Wait for the second question to appear (banner resets and shows new batch).
  await expect(main.locator('.question-text')).toContainText(
    'How should I handle edge cases?', { timeout: 25000 }
  );
  await main.getByRole('radio', { name: /Strict validation/ }).click();
  await main.getByRole('button', { name: /Submit/ }).click();

  await expect(
    page.locator('.timeline-entry').filter({ hasText: 'Done!' })
  ).toBeVisible({ timeout: 20000 });

  await page.getByRole('tab', { name: 'Chat' }).click();

  await expect(page.locator('.agent-prose .markdown-body').first()).toBeVisible({ timeout: 5000 });
  await expect(page.locator('.user-bubble').first()).toBeVisible();
});

// --- Test 6: Chat/Log toggle preserves data ---

test('chat/log toggle preserves events', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'Toggle test', 'Toggle test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);

  const responseEntry = page.locator('.timeline-entry').filter({ hasText: 'Mock ACP response' });
  await expect(responseEntry).toBeVisible({ timeout: 10000 });

  const logCount = await page.locator('.timeline-entry').count();
  expect(logCount).toBeGreaterThan(0);

  await page.getByRole('tab', { name: 'Chat' }).click();
  await expect(page.locator('.chat-row').first()).toBeVisible({ timeout: 5000 });

  await page.getByRole('tab', { name: 'Log' }).click();
  const logCountAfter = await page.locator('.timeline-entry').count();
  expect(logCountAfter).toBeGreaterThanOrEqual(logCount);
});

// --- Test 7: Theme toggle preserves markdown rendering ---

test('theme toggle preserves syntax highlighting', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_MARKDOWN', 'Theme test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  await expect(page.locator('.shiki').first()).toBeVisible({ timeout: 10000 });

  const themeBtn = page.getByRole('button', { name: /Activate .+ theme/ });
  await themeBtn.click();

  await expect(page.locator('.shiki').first()).toBeVisible();

  await page.getByRole('button', { name: /Activate .+ theme/ }).click();
  await expect(page.locator('.shiki').first()).toBeVisible();
});

// --- Test 8: Unknown data types render via DataFallback ---

test('unknown data type renders as compact label, not DataFallback JSON', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_MODE_UPDATE', 'Mode update test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // mode_change is a known type routed to compact renderer, not DataFallback
  const modeLabel = page.locator('.chat-row').filter({ hasText: 'mode_change' });
  await expect(modeLabel).toBeVisible({ timeout: 10000 });
  // Should NOT have a DataFallback JSON viewer (no <pre> with raw JSON)
  await expect(modeLabel.locator('.fallback-json')).not.toBeVisible();
});

// --- Test 9: Showcase scenario renders all event types ---
// Requires Showcase Agent pre-seeded via scripts/seed_agents.py (run by scripts/e2e.sh).

test('showcase scenario: all renderer types in log and chat views', async ({ page }) => {
  await waitForWorkerIdle();

  const agent = await ensureShowcaseAgent();
  const { execId } = await createExecution(agent.id, 'Full showcase', 'Showcase test');
  await waitForWorkerPickup(execId, 15000);

  await page.goto(`/#/execution/${execId}`);

  // Wait for the final markdown event to appear in log view
  const markdownEntry = page.locator('.timeline-entry').filter({ hasText: 'Refactoring Complete' });
  await expect(markdownEntry).toBeVisible({ timeout: 30000 });

  // Showcase agent completes without escalate — question banner must NOT appear
  await expect(page.locator('.question-banner')).not.toBeVisible();

  // Log view: verify all event types rendered
  // Thinking (ellipsis icon)
  const thinkingEntry = page.locator('.timeline-entry').filter({ hasText: 'analyze the codebase' });
  await expect(thinkingEntry).toBeVisible();
  await expect(thinkingEntry.locator('.ev-icon')).toContainText('\u22EF');

  // Tool calls (gear icon)
  const readFileEntry = page.locator('.timeline-entry').filter({ hasText: 'read_file(src/config.rs)' });
  await expect(readFileEntry).toBeVisible();
  await expect(readFileEntry.locator('.ev-icon')).toContainText('\u2699');

  const grepEntry = page.locator('.timeline-entry').filter({ hasText: 'grep(TODO|FIXME' });
  await expect(grepEntry).toBeVisible();

  const writeEntry = page.locator('.timeline-entry').filter({ hasText: 'write_file(' });
  await expect(writeEntry).toBeVisible();

  // Plan (hamburger icon)
  const planEntry = page.locator('.timeline-entry').filter({ hasText: 'Plan (4 steps)' });
  await expect(planEntry).toBeVisible();
  await expect(planEntry.locator('.ev-icon')).toContainText('\u2630');

  // Tool call update (completed)
  const updateEntry = page.locator('.timeline-entry').filter({ hasText: 'Write src/config.rs' });
  await expect(updateEntry).toBeVisible();

  // Message chunks
  const msgEntry = page.locator('.timeline-entry').filter({ hasText: 'Found the configuration module' });
  await expect(msgEntry).toBeVisible();

  // Switch to chat view and verify renderers
  await page.getByRole('tab', { name: 'Chat' }).click();

  // ThinkingBlock (collapsible)
  const thinkingBlock = page.locator('.thinking-block').first();
  await expect(thinkingBlock).toBeVisible({ timeout: 10000 });
  await expect(thinkingBlock).toContainText('Thinking...');

  // ToolGroup — enriched with title, collapsible content section
  const toolCard = page.locator('.tool-group').first();
  await expect(toolCard).toBeVisible();
  await expect(toolCard).toContainText('read_file');
  // Showcase tool calls have content, rendered in a collapsible details
  const toolCardWithContent = page.locator('.tool-group').filter({ hasText: 'Write src/config.rs' });
  await expect(toolCardWithContent).toBeVisible();

  // Markdown rendering: heading, table, code block, list
  const markdown = page.locator('.agent-prose .markdown-body').last();
  await expect(markdown.locator('h1')).toBeVisible();
  await expect(markdown.locator('h1')).toContainText('Refactoring Complete');
  await expect(markdown.locator('table')).toBeVisible();
  await expect(markdown.locator('pre')).toBeVisible();
  await expect(markdown.locator('li').first()).toBeVisible();
  await expect(markdown.locator('blockquote')).toBeVisible();

  // Syntax highlighting
  await expect(page.locator('.shiki').first()).toBeVisible();
});

// --- Test 10: Turn-complete event rendering ---

test('turn_complete event renders with return arrow icon in log view', async ({ page }) => {
  test.setTimeout(60000);
  await waitForWorkerIdle();

  const lead = await ensureTCLeadAgent();
  const child = await ensureTCChildAgent();

  const { execId } = await createExecution(lead.id, 'Turn-complete rendering test', 'TC render test', [child.id]);
  await waitForWorkerPickup(execId, 15000);

  // Wait for turn-complete event to be recorded before navigating to UI
  await waitForEvent(execId, 'turn_complete', 30000);

  // Navigate to execution — selects lead session by default
  await page.goto(`/#/execution/${execId}`);

  // Log view: turn_complete event with return arrow icon
  const tcEntry = page.locator('.timeline-entry').filter({ hasText: 'Child reported' });
  await expect(tcEntry).toBeVisible({ timeout: 10000 });
  await expect(tcEntry.locator('.ev-icon')).toContainText('\u21A9');
  await expect(tcEntry).toContainText('END_TURN_PHASE_0');
});

// --- Test 11: Multi-turn Q&A with two question batches ---
// Verifies the QuestionBanner resets and displays a second question after the first is answered.

test('demo scenario: multi-turn Q&A with two question batches', async ({ page }) => {
  await waitForWorkerIdle();

  const agent = await ensureDemoAgent();
  const { execId } = await createExecution(agent.id, 'Test multi-turn', 'Multi-turn Q&A test');
  await waitForWorkerPickup(execId, 15000);

  await page.goto(`/#/execution/${execId}`);

  // --- First question batch ---
  const main = page.locator('#main-content');
  const banner = page.locator('.question-banner');
  await expect(banner).toBeVisible({ timeout: 20000 });
  await expect(main.locator('.question-text')).toContainText('Which approach should I take?');

  await main.getByRole('radio', { name: /Refactor existing code/ }).click();
  await main.getByRole('button', { name: /Submit/ }).click();

  // --- Second question batch (key assertion: banner shows new question) ---
  // The banner transitions from Q1 → submitted → reset → Q2. The text change
  // is the reliable signal that the full reactive cycle completed.
  await expect(main.locator('.question-text')).toContainText(
    'How should I handle edge cases?', { timeout: 25000 }
  );

  // Verify second batch options are correct
  await expect(main.getByRole('radio', { name: /Strict validation/ })).toBeVisible();
  await expect(main.getByRole('radio', { name: /Lenient parsing/ })).toBeVisible();

  await main.getByRole('radio', { name: /Strict validation/ }).click();
  await main.getByRole('button', { name: /Submit/ }).click();

  // Agent completes — "Done!" appears in timeline
  await expect(
    page.locator('.timeline-entry').filter({ hasText: 'Done!' })
  ).toBeVisible({ timeout: 20000 });

  // Verify both user answers appear in log view (use "User:" prefix to
  // avoid matching the agent's echo like "Got it, going with: ... Refactor")
  await expect(
    page.locator('.timeline-entry').filter({ hasText: 'User: Refactor existing code' })
  ).toBeVisible();
  await expect(
    page.locator('.timeline-entry').filter({ hasText: 'User: Strict validation' })
  ).toBeVisible();
});

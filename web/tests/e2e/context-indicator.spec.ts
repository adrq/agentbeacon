import { test, expect } from '@playwright/test';
import {
  ensureClaudeAgent,
  createExecution,
  waitForWorkerIdle,
  waitForTurnEnd,
  apiGet,
} from './helpers';

test.beforeAll(async () => {
  await waitForWorkerIdle();
});

test.afterEach(async () => {
  await waitForWorkerIdle();
});

// --- Test 1: Usage content blocks appear in SSE event payloads ---

test('claude mock: usage_update content blocks in assistant messages', async () => {
  const agent = await ensureClaudeAgent();
  const { execId } = await createExecution(agent.id, 'Test usage tracking', 'Context indicator test');
  await waitForTurnEnd(execId, 20000);

  // Get all events for the execution
  const result = await apiGet(`/api/executions/${execId}`);
  const sessions = result.sessions;
  expect(sessions.length).toBeGreaterThan(0);

  const leadSession = sessions.find((s: { parent_session_id: null }) => !s.parent_session_id);
  expect(leadSession).toBeDefined();

  const events = await apiGet(`/api/sessions/${leadSession.id}/events`);

  // Find assistant messages with usage_update data parts
  const messageEvents = events.filter((e: { event_type: string }) => e.event_type === 'message');
  const messagesWithUsage = messageEvents.filter((e: { payload: { parts: unknown[] } }) => {
    const parts = e.payload.parts || [];
    return parts.some((p: { data?: { type?: string } }) =>
      'data' in p && p.data?.type === 'usage_update'
    );
  });

  // Mock SDK should emit usage_update on assistant messages
  expect(messagesWithUsage.length).toBeGreaterThan(0);

  // Verify structure of usage_update data part
  const firstUsageMessage = messagesWithUsage[0];
  const usagePart = firstUsageMessage.payload.parts.find(
    (p: { data?: { type?: string } }) =>
      'data' in p && p.data?.type === 'usage_update'
  );

  expect(usagePart).toBeDefined();
  expect(usagePart.data).toHaveProperty('input_tokens');
  expect(usagePart.data).toHaveProperty('output_tokens');
  expect(typeof usagePart.data.input_tokens).toBe('number');
  expect(typeof usagePart.data.output_tokens).toBe('number');
});

// --- Test 2: usage_snapshot appears before result event ---

test('claude mock: usage_snapshot message before result', async () => {
  const agent = await ensureClaudeAgent();
  const { execId } = await createExecution(agent.id, 'Test usage snapshot', 'Snapshot test');
  await waitForTurnEnd(execId, 20000);

  const result = await apiGet(`/api/executions/${execId}`);
  const sessions = result.sessions;
  const leadSession = sessions.find((s: { parent_session_id: null }) => !s.parent_session_id);

  const events = await apiGet(`/api/sessions/${leadSession.id}/events`);

  // Find usage_snapshot message
  const snapshotMessages = events.filter((e: { event_type: string; payload: { parts: unknown[] } }) => {
    if (e.event_type !== 'message') return false;
    const parts = e.payload.parts || [];
    return parts.some((p: { data?: { type?: string } }) =>
      'data' in p && p.data?.type === 'usage_snapshot'
    );
  });

  expect(snapshotMessages.length).toBe(1);

  // Verify structure
  const snapshotPart = snapshotMessages[0].payload.parts.find(
    (p: { data?: { type?: string } }) =>
      'data' in p && p.data?.type === 'usage_snapshot'
  );

  expect(snapshotPart).toBeDefined();
  expect(snapshotPart.data).toHaveProperty('context_window');
  expect(snapshotPart.data.context_window).toBe(200000); // Mock SDK value
  expect(snapshotPart.data).toHaveProperty('input_tokens');
  expect(snapshotPart.data).toHaveProperty('output_tokens');
});

// --- Test 3: compact_boundary appears as compaction message ---

test('claude mock: compaction content block appears', async () => {
  const agent = await ensureClaudeAgent();
  const { execId } = await createExecution(agent.id, 'Test compaction', 'Compaction test');
  await waitForTurnEnd(execId, 20000);

  const result = await apiGet(`/api/executions/${execId}`);
  const sessions = result.sessions;
  const leadSession = sessions.find((s: { parent_session_id: null }) => !s.parent_session_id);

  const events = await apiGet(`/api/sessions/${leadSession.id}/events`);

  // Find compaction message
  const compactionMessages = events.filter((e: { event_type: string; payload: { parts: unknown[] } }) => {
    if (e.event_type !== 'message') return false;
    const parts = e.payload.parts || [];
    return parts.some((p: { data?: { type?: string } }) =>
      'data' in p && p.data?.type === 'compaction'
    );
  });

  expect(compactionMessages.length).toBe(1);

  // Verify structure
  const compactionPart = compactionMessages[0].payload.parts.find(
    (p: { data?: { type?: string } }) =>
      'data' in p && p.data?.type === 'compaction'
  );

  expect(compactionPart).toBeDefined();
  expect(compactionPart.data).toHaveProperty('trigger');
  expect(compactionPart.data.trigger).toBe('auto');
});

// --- Test 4: Context indicator visible in UI ---

test('claude mock: context indicator visible in chat toolbar', async ({ page }) => {
  const agent = await ensureClaudeAgent();
  const { execId } = await createExecution(agent.id, 'Test UI indicator', 'UI test');
  await waitForTurnEnd(execId, 20000);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Wait for context indicator to appear
  const contextIndicator = page.locator('.context-indicator');
  await expect(contextIndicator).toBeVisible({ timeout: 10000 });

  // Should show token counts
  await expect(contextIndicator).toContainText(/\d+K/); // Formatted tokens
});

// --- Test 5: Context bar visible in session tree ---

test('claude mock: context bar in session tree', async ({ page }) => {
  const agent = await ensureClaudeAgent();
  const { execId } = await createExecution(agent.id, 'Test tree indicator', 'Tree test');
  await waitForTurnEnd(execId, 20000);

  await page.goto(`/#/execution/${execId}`);

  // Wait for session tree to load
  const sessionTree = page.locator('.session-tree');
  await expect(sessionTree).toBeVisible({ timeout: 10000 });

  // Context bar should be visible
  const contextBar = page.locator('.context-bar');
  await expect(contextBar).toBeVisible({ timeout: 5000 });

  // Should have a fill indicating usage
  const contextFill = page.locator('.context-fill');
  await expect(contextFill).toBeVisible();
});

// --- Test 6: Compaction divider renders in chat ---

test('claude mock: compaction divider in chat view', async ({ page }) => {
  const agent = await ensureClaudeAgent();
  const { execId } = await createExecution(agent.id, 'Test compaction divider', 'Divider test');
  await waitForTurnEnd(execId, 20000);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Compaction divider should be visible
  const compactionRow = page.locator('.compaction-row');
  await expect(compactionRow).toBeVisible({ timeout: 10000 });

  const compactionLabel = page.locator('.compaction-label');
  await expect(compactionLabel).toContainText('context compacted');
});

// --- Test 7: Compaction appears in log view ---

test('claude mock: compaction in log view', async ({ page }) => {
  const agent = await ensureClaudeAgent();
  const { execId } = await createExecution(agent.id, 'Test compaction log', 'Log test');
  await waitForTurnEnd(execId, 20000);

  await page.goto(`/#/execution/${execId}`);
  // Log view is default, no need to click

  // Find compaction entry in timeline
  const compactionEntry = page.locator('.timeline-entry').filter({ hasText: 'Context compacted' });
  await expect(compactionEntry).toBeVisible({ timeout: 10000 });

  // Should have rotate icon ↻
  await expect(compactionEntry.locator('.ev-icon')).toContainText('\u21BB');
});

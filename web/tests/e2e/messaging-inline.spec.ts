import { test, expect } from '@playwright/test';
import {
  ensureDirectAgent, ensureTCLeadAgent, ensureTCChildAgent,
  ensureTCMarkdownLeadAgent, ensureTCChildMarkdownAgent,
  ensureTCMsgLeadAgent, ensureTCMsgChildAgent,
  createExecution, waitForWorkerIdle, waitForTurnEnd,
  waitForWorkerPickup, waitForEvent,
  getHierarchicalName, sendAgentMessage, apiGet, apiPost,
} from './helpers';

test.beforeAll(async () => {
  await waitForWorkerIdle();
});

test.afterEach(async () => {
  await waitForWorkerIdle();
});

// --- Test 1: Mock agent inter-agent message renders in chat view ---

test('mock agent inter-agent message renders in chat view', async ({ page }) => {
  const lead = await ensureTCMsgLeadAgent();
  const child = await ensureTCMsgChildAgent();
  const { execId } = await createExecution(lead.id, 'Message test', 'msg test', [child.id]);

  // Wait for full cycle: delegate → child sends message → child end_turn → turn-complete → lead ACK
  await waitForTurnEnd(execId);
  // Lead may need extra turn to process turn-complete after message
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  const lateral = page.locator('.lateral-message');
  await expect(lateral.first()).toBeVisible({ timeout: 10000 });
  await expect(lateral.first().locator('.lateral-header')).toContainText('From');
  await expect(lateral.first().locator('.lateral-body')).toContainText('Status update');
  // No DataFallback with sender data
  await expect(page.locator('.fallback-card:has-text("sender")')).not.toBeVisible();
});

// --- Test 2: Mock agent inter-agent message renders in log view ---

test('mock agent inter-agent message renders in log view', async ({ page }) => {
  const lead = await ensureTCMsgLeadAgent();
  const child = await ensureTCMsgChildAgent();
  const { execId } = await createExecution(lead.id, 'Log msg test', 'log test', [child.id]);
  await waitForTurnEnd(execId);
  // Second turn: lead processes turn-complete after child's message
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  // Log view is the default
  const lateralEntry = page.locator('.ev-icon.lateral');
  await expect(lateralEntry.first()).toBeVisible({ timeout: 10000 });
});

// --- Test 3: Injected inter-agent message renders correctly ---

test('injected inter-agent message renders correctly', async ({ page }) => {
  const lead = await ensureTCLeadAgent();
  const child = await ensureTCChildAgent();
  const { execId } = await createExecution(lead.id, 'Inject test', 'inject', [child.id]);
  await waitForTurnEnd(execId);

  // Discover child session and lead hierarchical name
  const detail = await apiGet(`/api/executions/${execId}`);
  const sessions = detail.sessions ?? detail.execution?.sessions ?? [];
  const childSession = sessions.find((s: { agent_id: string }) => s.agent_id === child.id);
  if (!childSession) throw new Error('Child session not found');
  const leadSessionId = detail.session_id ?? sessions.find((s: { parent_session_id: string | null }) => !s.parent_session_id)?.id;
  const leadName = await getHierarchicalName(execId, leadSessionId);

  // Inject a message from child to lead
  await sendAgentMessage(childSession.id, leadName, 'Hello from injected test');
  // Wait for lead to process the message prompt
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();
  const lateral = page.locator('.lateral-message');
  await expect(lateral).toBeVisible({ timeout: 10000 });
  await expect(lateral.locator('.lateral-body')).toContainText('Hello from injected test');
});

// --- Test 4: Human user messages still render as user bubbles ---

test('human user messages still render as user bubbles', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId, sessionId } = await createExecution(agent.id, 'SEND_MARKDOWN', 'User bubble test');
  await waitForTurnEnd(execId);

  // Send a follow-up user message to create a user event (initial prompt is not stored as an event)
  await apiPost(`/api/sessions/${sessionId}/message`, { message: 'Follow up message' });
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();
  await expect(page.locator('.user-bubble').first()).toBeVisible({ timeout: 10000 });
  await expect(page.locator('.lateral-message')).not.toBeVisible();
});

// --- Test 5: Child response visible under Messages filter ---

test('child response visible under Messages filter', async ({ page }) => {
  test.setTimeout(60000);
  await waitForWorkerIdle();

  const lead = await ensureTCLeadAgent();
  const child = await ensureTCChildAgent();
  const { execId } = await createExecution(lead.id, 'Filter visibility test', 'msg filter', [child.id]);
  await waitForWorkerPickup(execId, 15000);
  await waitForEvent(execId, 'turn_complete', 30000);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Visible under All
  await expect(page.locator('.child-response')).toBeVisible({ timeout: 10000 });

  // Visible under Messages
  await page.getByRole('radio', { name: 'Messages' }).click();
  await expect(page.locator('.child-response')).toBeVisible({ timeout: 5000 });

  // Hidden under Status
  await page.getByRole('radio', { name: 'Status' }).click();
  await expect(page.locator('.child-response')).not.toBeVisible();
});

// --- Test 6: Child response has green left border ---

test('child response has green left border', async ({ page }) => {
  test.setTimeout(60000);
  await waitForWorkerIdle();

  const lead = await ensureTCLeadAgent();
  const child = await ensureTCChildAgent();
  const { execId } = await createExecution(lead.id, 'Green border test', 'border test', [child.id]);
  await waitForWorkerPickup(execId, 15000);
  await waitForEvent(execId, 'turn_complete', 30000);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  const childResponse = page.locator('.child-response');
  await expect(childResponse).toBeVisible({ timeout: 10000 });

  const borderColor = await childResponse.evaluate(el => getComputedStyle(el).borderLeftColor);
  // Expect a green-ish color (RGB green component > red and blue)
  const match = borderColor.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/);
  expect(match).toBeTruthy();
  if (match) {
    const r = Number(match[1]);
    const g = Number(match[2]);
    const b = Number(match[3]);
    expect(g).toBeGreaterThan(r);
    expect(g).toBeGreaterThan(b);
  }

  // Verify left alignment
  const row = page.locator('.child-response-row');
  const justifyContent = await row.evaluate(el => getComputedStyle(el).justifyContent);
  expect(justifyContent).toBe('flex-start');
});

// --- Test 7: Child response header shows child agent name ---

test('child response header shows child agent name', async ({ page }) => {
  test.setTimeout(60000);
  await waitForWorkerIdle();

  const lead = await ensureTCLeadAgent();
  const child = await ensureTCChildAgent();
  const { execId } = await createExecution(lead.id, 'Agent name test', 'name test', [child.id]);
  await waitForWorkerPickup(execId, 15000);
  await waitForEvent(execId, 'turn_complete', 30000);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  const header = page.locator('.child-response-header');
  await expect(header).toBeVisible({ timeout: 10000 });
  await expect(header).toContainText('TC Child Agent');
});

// --- Test 8: EventsTimeline still shows compact turn-complete summary ---

test('EventsTimeline still shows compact turn-complete summary', async ({ page }) => {
  test.setTimeout(60000);
  await waitForWorkerIdle();

  const lead = await ensureTCLeadAgent();
  const child = await ensureTCChildAgent();
  const { execId } = await createExecution(lead.id, 'Timeline compact test', 'timeline test', [child.id]);
  await waitForWorkerPickup(execId, 15000);
  await waitForEvent(execId, 'turn_complete', 30000);

  await page.goto(`/#/execution/${execId}`);
  // Log view is default — verify turn_complete shows as compact entry
  const turnCompleteEntry = page.locator('.ev-icon.turn-complete');
  await expect(turnCompleteEntry).toBeVisible({ timeout: 10000 });
});

// --- Test 9: Child markdown response renders rich content ---

test('child markdown response renders headings, code blocks, and lists', async ({ page }) => {
  test.setTimeout(60000);
  await waitForWorkerIdle();

  const lead = await ensureTCMarkdownLeadAgent();
  const child = await ensureTCChildMarkdownAgent();
  const { execId } = await createExecution(lead.id, 'Markdown render test', 'md test', [child.id]);
  await waitForWorkerPickup(execId, 15000);
  await waitForEvent(execId, 'turn_complete', 30000);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  const childResponse = page.locator('.child-response');
  await expect(childResponse).toBeVisible({ timeout: 10000 });

  const body = childResponse.locator('.child-response-body .markdown-body');
  await expect(body).toBeVisible();

  // Verify markdown elements rendered (not plain text)
  await expect(body.locator('h2')).toBeVisible();
  await expect(body.locator('code').first()).toBeVisible();
  await expect(body.locator('ul li')).toHaveCount(3);
  await expect(body.locator('blockquote')).toBeVisible();
});

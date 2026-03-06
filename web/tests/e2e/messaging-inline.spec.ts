import { test, expect } from '@playwright/test';
import {
  ensureDirectAgent, ensureTCLeadAgent, ensureTCChildAgent,
  ensureTCMsgLeadAgent, ensureTCMsgChildAgent,
  createExecution, waitForWorkerIdle, waitForTurnEnd,
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

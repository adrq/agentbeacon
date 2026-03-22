import { test, expect } from '@playwright/test';
import {
  ensureDirectAgent, ensureTCLeadAgent, ensureTCChildAgent,
  ensureTCMsgLeadAgent, ensureTCMsgChildAgent,
  createExecution,
  waitForWorkerIdle, waitForTurnEnd,
  waitForWorkerPickup, waitForEvent,
} from './helpers';

test.beforeAll(async () => {
  await waitForWorkerIdle();
});

test.afterEach(async () => {
  await waitForWorkerIdle();
});

// --- Sidebar node uses slug + agent pill format ---

test('sidebar nodes show slug and agent pill instead of raw agent name', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Agent identity sidebar test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);

  const sidebarNode = page.locator('.sidebar-node').first();
  await expect(sidebarNode).toBeVisible({ timeout: 10000 });

  // Node label should contain .node-slug (not raw text "Lead (AgentName)")
  const nodeLabel = sidebarNode.locator('.node-label');
  await expect(nodeLabel).toBeVisible();

  // node-slug must be attached and have non-empty text that looks like a slug (word-word)
  const nodeSlug = sidebarNode.locator('.node-slug');
  await expect(nodeSlug).toBeAttached();
  const slugText = await nodeSlug.innerText();
  expect(slugText.trim()).not.toBe('');
  expect(slugText.trim()).toMatch(/^[a-z0-9]+-?[a-z0-9]+/);

  // node-label should not contain the old "Lead (" format
  const labelText = await nodeLabel.innerText();
  expect(labelText).not.toContain('Lead (');
});

test('sidebar node shows agent pill when identity data is available', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Agent identity pill test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);

  const sidebarNode = page.locator('.sidebar-node').first();
  await expect(sidebarNode).toBeVisible({ timeout: 10000 });

  // When identity data loads, agent-pill should appear inside node-label
  const agentPill = sidebarNode.locator('.agent-pill');
  await expect(agentPill).toBeVisible({ timeout: 5000 });

  // Pill text should be a non-empty agent config name
  const pillText = await agentPill.innerText();
  expect(pillText.trim().length).toBeGreaterThan(0);
});

// --- Chat view agent prose header uses slug + pill format ---

test('agent prose header shows slug and agent pill instead of plain agent name', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_MARKDOWN', 'Agent identity chat header test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  const agentProse = page.locator('.agent-prose').first();
  await expect(agentProse).toBeVisible({ timeout: 10000 });

  // Header should contain .agent-header-slug
  const header = agentProse.locator('.agent-prose-header');
  await expect(header).toBeVisible();

  const slug = header.locator('.agent-header-slug');
  await expect(slug).toBeVisible();

  // Slug text should be non-empty and look like a slug
  const slugText = await slug.innerText();
  expect(slugText.trim()).toMatch(/^[a-z0-9]+-?[a-z0-9]+/);
});

test('agent prose header shows agent pill when identity data is available', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_MARKDOWN', 'Agent identity pill chat test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  const agentProse = page.locator('.agent-prose').first();
  await expect(agentProse).toBeVisible({ timeout: 10000 });

  const pill = agentProse.locator('.agent-prose-header .agent-pill');
  await expect(pill).toBeVisible({ timeout: 5000 });

  // Pill should contain the agent config name
  const pillText = await pill.innerText();
  expect(pillText.trim().length).toBeGreaterThan(0);
});

// --- Lateral message header uses slug + pill + path + copy format ---

test('lateral message header shows slug, path, and copy button', async ({ page }) => {
  test.setTimeout(60000);
  await waitForWorkerIdle();

  const lead = await ensureTCMsgLeadAgent();
  const child = await ensureTCMsgChildAgent();
  const { execId } = await createExecution(lead.id, 'Lateral identity test', 'lateral identity', [child.id]);
  await waitForTurnEnd(execId);
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  const lateral = page.locator('.lateral-message').first();
  await expect(lateral).toBeVisible({ timeout: 10000 });

  const header = lateral.locator('.lateral-header');
  await expect(header).toBeVisible();

  // Header should contain slug with non-empty text
  const slug = header.locator('.lateral-slug');
  await expect(slug).toBeVisible();
  const slugText = await slug.innerText();
  expect(slugText.trim().length).toBeGreaterThan(0);

  // Header should contain path with hierarchical separator
  const path = header.locator('.lateral-path');
  await expect(path).toBeVisible();
  const pathText = await path.innerText();
  expect(pathText).toContain('/');

  // Header should contain a copy button
  const copyBtn = header.locator('.copy-btn');
  await expect(copyBtn).toBeVisible();

  // Header should NOT contain "From " prefix (old format removed)
  const headerText = await header.innerText();
  expect(headerText).not.toMatch(/^From /);
});

// --- Child response header uses slug + pill + path + copy format ---

test('child response header shows slug and agent pill', async ({ page }) => {
  test.setTimeout(60000);
  await waitForWorkerIdle();

  const lead = await ensureTCLeadAgent();
  const child = await ensureTCChildAgent();
  const { execId } = await createExecution(lead.id, 'Child identity test', 'child identity', [child.id]);
  await waitForWorkerPickup(execId, 15000);
  await waitForEvent(execId, 'turn_complete', 30000);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  const childResponse = page.locator('.child-response').first();
  await expect(childResponse).toBeVisible({ timeout: 10000 });

  const header = childResponse.locator('.child-response-header');
  await expect(header).toBeVisible();

  // Header should contain .lateral-slug element with non-empty text
  const slug = header.locator('.lateral-slug');
  await expect(slug).toBeVisible();
  const slugText = await slug.innerText();
  expect(slugText.trim().length).toBeGreaterThan(0);

  // Agent pill should appear once identity data loads
  const pill = header.locator('.agent-pill');
  await expect(pill).toBeVisible({ timeout: 5000 });
  const pillText = await pill.innerText();
  expect(pillText.trim().length).toBeGreaterThan(0);
});

test('child response header shows hierarchical path and copy button when identity available', async ({ page }) => {
  test.setTimeout(60000);
  await waitForWorkerIdle();

  const lead = await ensureTCLeadAgent();
  const child = await ensureTCChildAgent();
  const { execId } = await createExecution(lead.id, 'Child path test', 'child path', [child.id]);
  await waitForWorkerPickup(execId, 15000);
  await waitForEvent(execId, 'turn_complete', 30000);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  const childResponse = page.locator('.child-response').first();
  await expect(childResponse).toBeVisible({ timeout: 10000 });

  const header = childResponse.locator('.child-response-header');
  const path = header.locator('.lateral-path');
  await expect(path).toBeVisible({ timeout: 5000 });

  // Path should contain hierarchical separator
  const pathText = await path.innerText();
  expect(pathText).toContain('/');

  const copyBtn = header.locator('.copy-btn');
  await expect(copyBtn).toBeVisible();
});

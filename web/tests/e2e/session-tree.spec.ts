import { test, expect } from '@playwright/test';
import { execSync } from 'child_process';
import {
  apiGet, apiPost, ensureDirectAgent, createExecution,
  waitForWorkerIdle, waitForTurnEnd, waitForTerminal,
} from './helpers';

const PORT = process.env.AGENTBEACON_PORT ?? '9456';
const DB_PATH = `${process.cwd()}/../scheduler-${PORT}.db`;

function sqliteExec(sql: string) {
  execSync(`sqlite3 "${DB_PATH}" "${sql}"`);
}

test.beforeAll(async () => {
  await waitForWorkerIdle();
});

test.afterEach(async () => {
  await waitForWorkerIdle();
});

test('clicking an execution in the sidebar expands the session tree', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Sidebar tree expand test');
  await waitForTurnEnd(execId);

  // Navigate to the executions list view and click the execution item
  await page.goto('/');
  await page.getByRole('button', { name: 'Executions' }).click();

  const execItem = page.locator('.exec-item', { hasText: 'Sidebar tree expand test' });
  await expect(execItem).toBeVisible({ timeout: 15000 });
  await execItem.click();

  // The sidebar tree should appear below the selected execution
  const sidebarTree = page.locator('.sidebar-tree');
  await expect(sidebarTree).toBeVisible({ timeout: 10000 });
});

test('session nodes appear in sidebar with correct status indicators', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Sidebar node status test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);

  // Sidebar node should be visible with a node-icon
  const sidebarNode = page.locator('.sidebar-node').first();
  await expect(sidebarNode).toBeVisible({ timeout: 10000 });

  // Node icon should show a non-empty status character (●, ✓, ✗, !, ○)
  const nodeIcon = sidebarNode.locator('.node-icon');
  await expect(nodeIcon).toBeVisible();
  const iconText = await nodeIcon.innerText();
  expect(iconText.trim()).not.toBe('');
});

test('clicking a session node in the sidebar selects it', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId, sessionId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Sidebar select test');
  await waitForTurnEnd(execId);

  // Insert a working child session — it won't be auto-selected (only lead is)
  const childId = `child-sel-${Date.now()}`;
  sqliteExec(`INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status, slug, cwd) VALUES ('${childId}', '${execId}', '${sessionId}', '${agent.id}', 'working', 'sel-child', '/tmp')`);

  await page.goto(`/#/execution/${execId}`);

  // Lead node is auto-selected
  const leadNode = page.locator('.sidebar-node').first();
  await expect(leadNode).toBeVisible({ timeout: 10000 });
  await expect(leadNode).toHaveClass(/active/, { timeout: 5000 });

  // Child node should NOT be auto-selected
  const childNode = page.locator(`.sidebar-node[data-session-id="${childId}"]`);
  await expect(childNode).toBeVisible({ timeout: 5000 });
  await expect(childNode).not.toHaveClass(/active/);

  // Click child to select it
  await childNode.click();
  await expect(childNode).toHaveClass(/active/);
});

test('detail view does not show old SessionTree disclosure header', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'No old tree test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);

  // The old .tree-disclosure element should NOT exist in the detail panel
  await expect(page.locator('.tree-disclosure')).not.toBeVisible();

  // The old .tree-body with bounded height should NOT exist
  await expect(page.locator('.tree-body')).not.toBeVisible();
});

test('detail view does not show pool section or detail meta', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'No pool meta test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);

  await expect(page.locator('.pool-section')).not.toBeVisible();
  await expect(page.locator('.detail-meta')).not.toBeVisible();
  await expect(page.locator('.completion-summary')).not.toBeVisible();
});

test('detail header is compact single-line bar', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Compact header test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);

  const header = page.locator('.detail-header');
  await expect(header).toBeVisible({ timeout: 10000 });

  // Header should be around 36px tall (compact single-line bar)
  const height = await header.evaluate(el => el.getBoundingClientRect().height);
  expect(height).toBeLessThan(60);
});

test('terminal children auto-collapse into summary line in sidebar', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId, sessionId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Sidebar auto-collapse test');
  await waitForTurnEnd(execId);

  const childId1 = `child-sac-1-${Date.now()}`;
  const childId2 = `child-sac-2-${Date.now()}`;
  sqliteExec(`INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status, slug, cwd, last_progress_at) VALUES ('${childId1}', '${execId}', '${sessionId}', '${agent.id}', 'completed', 'c1', '/tmp', CURRENT_TIMESTAMP)`);
  sqliteExec(`INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status, slug, cwd, last_progress_at) VALUES ('${childId2}', '${execId}', '${sessionId}', '${agent.id}', 'completed', 'c2', '/tmp', CURRENT_TIMESTAMP)`);

  await page.goto(`/#/execution/${execId}`);

  const sidebarTree = page.locator('.sidebar-tree');
  await expect(sidebarTree).toBeVisible({ timeout: 10000 });

  // Terminal summary should be visible (not the individual completed nodes)
  const summary = page.locator('.terminal-summary');
  await expect(summary).toBeVisible();
  await expect(summary).toContainText('completed');
});

test('clicking terminal summary line expands children in sidebar', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId, sessionId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Sidebar summary expand test');
  await waitForTurnEnd(execId);

  const childId1 = `child-sse-1-${Date.now()}`;
  const childId2 = `child-sse-2-${Date.now()}`;
  sqliteExec(`INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status, slug, cwd, last_progress_at) VALUES ('${childId1}', '${execId}', '${sessionId}', '${agent.id}', 'completed', 'c1', '/tmp', CURRENT_TIMESTAMP)`);
  sqliteExec(`INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status, slug, cwd, last_progress_at) VALUES ('${childId2}', '${execId}', '${sessionId}', '${agent.id}', 'completed', 'c2', '/tmp', CURRENT_TIMESTAMP)`);

  await page.goto(`/#/execution/${execId}`);

  const summary = page.locator('.terminal-summary');
  await expect(summary).toBeVisible({ timeout: 10000 });

  await summary.click();

  // Summary should disappear, individual completed nodes should appear
  await expect(summary).not.toBeVisible();
  const completedNodes = page.locator('.sidebar-node.completed');
  await expect(completedNodes.first()).toBeVisible();
});

test('action buttons appear on sidebar node hover', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Sidebar action btn test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);

  // Find a non-terminal sidebar node to hover
  const sidebarNode = page.locator('.sidebar-node:not(.canceled):not(.completed):not(.failed)').first();
  await expect(sidebarNode).toBeVisible({ timeout: 10000 });

  // Cancel button is attached to DOM (opacity hidden by default, shown on hover via CSS)
  const cancelBtn = sidebarNode.locator('.cancel-btn');
  await expect(cancelBtn).toBeAttached();
  await sidebarNode.hover();
  await expect(cancelBtn).toBeVisible();
});

test('data-session-id attribute present on sidebar nodes', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Sidebar data attr test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);

  const sidebarNode = page.locator('.sidebar-node').first();
  await expect(sidebarNode).toBeVisible({ timeout: 10000 });

  const sid = await sidebarNode.getAttribute('data-session-id');
  expect(sid).toBeTruthy();
});

test('exec meta line shows in sidebar tree', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Sidebar exec meta test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);

  const execMeta = page.locator('.exec-meta');
  await expect(execMeta).toBeVisible({ timeout: 10000 });
});

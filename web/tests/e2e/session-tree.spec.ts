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

test('session tree has bounded height with internal scroll', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Bounded height test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);

  const treeBody = page.locator('.tree-body');
  await expect(treeBody).toBeVisible({ timeout: 10000 });

  // Verify CSS constraints
  const maxHeight = await treeBody.evaluate(el => getComputedStyle(el).maxHeight);
  expect(maxHeight).toBeTruthy();
  expect(maxHeight).not.toBe('none');

  const overflowY = await treeBody.evaluate(el => getComputedStyle(el).overflowY);
  expect(overflowY).toBe('auto');
});

test('disclosure header shows session counts', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Counts test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);

  const disclosure = page.locator('.tree-disclosure');
  await expect(disclosure).toBeVisible({ timeout: 10000 });

  // Non-terminal execution with 1 active session
  await expect(disclosure.locator('.count-active')).toContainText('active');
  await expect(disclosure.locator('.count-total')).toContainText('total');
});

test('disclosure toggle collapses and expands tree', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Toggle test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);

  const disclosure = page.locator('.tree-disclosure');
  const treeBody = page.locator('.tree-body');
  await expect(treeBody).toBeVisible({ timeout: 10000 });

  // Click to collapse
  await disclosure.click();
  await expect(treeBody).not.toBeVisible();

  // Click to expand
  await disclosure.click();
  await expect(treeBody).toBeVisible();
});

test('terminal execution defaults to collapsed tree', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'EXIT_1', 'Terminal collapse test');
  await waitForTerminal(execId);

  await page.goto(`/#/execution/${execId}`);

  const disclosure = page.locator('.tree-disclosure');
  await expect(disclosure).toBeVisible({ timeout: 10000 });

  // Tree body should NOT be visible (collapsed by default for terminal)
  const treeBody = page.locator('.tree-body');
  await expect(treeBody).not.toBeVisible();

  // Counts still shown in header
  await expect(disclosure.locator('.count-total')).toContainText('total');

  // Click to open
  await disclosure.click();
  await expect(treeBody).toBeVisible();
});

test('terminal children auto-collapse into summary line', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId, sessionId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Auto-collapse test');
  await waitForTurnEnd(execId);

  // Add completed child sessions via SQL
  const childId1 = `child-ac-1-${Date.now()}`;
  const childId2 = `child-ac-2-${Date.now()}`;
  sqliteExec(`INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status, slug, cwd) VALUES ('${childId1}', '${execId}', '${sessionId}', '${agent.id}', 'completed', 'c1', '/tmp')`);
  sqliteExec(`INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status, slug, cwd) VALUES ('${childId2}', '${execId}', '${sessionId}', '${agent.id}', 'completed', 'c2', '/tmp')`);

  await page.goto(`/#/execution/${execId}`);

  // Wait for tree to render
  const treeBody = page.locator('.tree-body');
  await expect(treeBody).toBeVisible({ timeout: 10000 });

  // Terminal summary should be visible (not the individual completed nodes)
  const summary = page.locator('.terminal-summary');
  await expect(summary).toBeVisible();
  await expect(summary).toContainText('completed');
});

test('clicking summary line expands terminal children', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId, sessionId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Summary expand test');
  await waitForTurnEnd(execId);

  // Add completed child sessions
  const childId1 = `child-se-1-${Date.now()}`;
  const childId2 = `child-se-2-${Date.now()}`;
  sqliteExec(`INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status, slug, cwd) VALUES ('${childId1}', '${execId}', '${sessionId}', '${agent.id}', 'completed', 'c1', '/tmp')`);
  sqliteExec(`INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status, slug, cwd) VALUES ('${childId2}', '${execId}', '${sessionId}', '${agent.id}', 'completed', 'c2', '/tmp')`);

  await page.goto(`/#/execution/${execId}`);

  const summary = page.locator('.terminal-summary');
  await expect(summary).toBeVisible({ timeout: 10000 });

  // Click summary to expand
  await summary.click();

  // Summary should disappear, individual nodes should appear
  await expect(summary).not.toBeVisible();

  // The completed child nodes should now be visible
  const completedNodes = page.locator('.tree-node.completed');
  await expect(completedNodes.first()).toBeVisible();
});

test('action buttons still work on tree nodes', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Tree action test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);

  const sessionNode = page.locator('.tree-node').first();
  await expect(sessionNode).toBeVisible({ timeout: 10000 });

  // Hover to reveal cancel button
  await sessionNode.hover();
  const cancelBtn = sessionNode.locator('.cancel-btn');
  await expect(cancelBtn).toBeVisible();
});

test('selecting session scrolls it into view in bounded tree', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId, sessionId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Scroll into view test');
  await waitForTurnEnd(execId);

  // Add enough active child sessions to overflow the tree body
  for (let i = 0; i < 15; i++) {
    const childId = `child-siv-${i}-${Date.now()}`;
    sqliteExec(`INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status, slug, cwd) VALUES ('${childId}', '${execId}', '${sessionId}', '${agent.id}', 'submitted', 'c${i}', '/tmp')`);
  }

  await page.goto(`/#/execution/${execId}`);

  const treeBody = page.locator('.tree-body');
  await expect(treeBody).toBeVisible({ timeout: 10000 });

  // Tree should overflow (scrollHeight > clientHeight)
  const overflows = await treeBody.evaluate(el => el.scrollHeight > el.clientHeight);
  expect(overflows).toBe(true);

  // Click the last node to select it — it should scroll into view
  const lastNode = page.locator('.tree-node').last();
  const lastSid = await lastNode.getAttribute('data-session-id');
  expect(lastSid).toBeTruthy();

  await lastNode.click();

  // After click + scroll animation, the node should be within the visible area
  await page.waitForTimeout(500); // allow smooth scroll to settle
  const isVisible = await treeBody.evaluate((container, sid) => {
    const node = container.querySelector(`[data-session-id="${sid}"]`);
    if (!node) return false;
    const cRect = container.getBoundingClientRect();
    const nRect = node.getBoundingClientRect();
    return nRect.top >= cRect.top && nRect.bottom <= cRect.bottom;
  }, lastSid);
  expect(isVisible).toBe(true);
});

test('data-session-id attribute present on tree nodes', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Data attr test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);

  const sessionNode = page.locator('.tree-node').first();
  await expect(sessionNode).toBeVisible({ timeout: 10000 });

  // Verify data-session-id attribute exists
  const sid = await sessionNode.getAttribute('data-session-id');
  expect(sid).toBeTruthy();
});

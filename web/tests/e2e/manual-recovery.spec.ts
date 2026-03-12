import { test, expect } from '@playwright/test';
import { execSync } from 'child_process';
import {
  apiGet, apiPost, ensureClaudeAgent, createExecution,
  waitForWorkerIdle, waitForTurnEnd, waitForTerminal, API_URL,
} from './helpers';

const PORT = process.env.AGENTBEACON_PORT ?? '9456';
const DB_PATH = `${process.cwd()}/../scheduler-${PORT}.db`;

function sqliteExec(sql: string) {
  execSync(`sqlite3 "${DB_PATH}" "${sql}"`);
}

function sqliteQuery(sql: string): string {
  return execSync(`sqlite3 "${DB_PATH}" "${sql}"`).toString().trim();
}

/** Set up a failed execution with agent_session_id so recovery is possible. */
async function setupRecoverableFailedExecution() {
  const agent = await ensureClaudeAgent();
  const { execId, sessionId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Recovery test');
  await waitForTurnEnd(execId);

  // Cancel then set to failed state with agent_session_id
  await apiPost(`/api/executions/${execId}/cancel`, {});
  // Wait a moment for cancellation to propagate
  await new Promise(r => setTimeout(r, 1000));

  sqliteExec(`UPDATE sessions SET status='failed', agent_session_id='cs_e2e_test', cwd='/tmp', completed_at=CURRENT_TIMESTAMP WHERE id='${sessionId}'`);
  sqliteExec(`UPDATE executions SET status='failed', completed_at=CURRENT_TIMESTAMP WHERE id='${execId}'`);

  return { agent, execId, sessionId };
}

/** Set up a failed execution WITHOUT agent_session_id. */
async function setupNonRecoverableFailedExecution() {
  const agent = await ensureClaudeAgent();
  const { execId, sessionId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Non-recoverable test');
  await waitForTurnEnd(execId);

  await apiPost(`/api/executions/${execId}/cancel`, {});
  await new Promise(r => setTimeout(r, 1000));

  sqliteExec(`UPDATE sessions SET status='failed', agent_session_id=NULL, cwd='/tmp', completed_at=CURRENT_TIMESTAMP WHERE id='${sessionId}'`);
  sqliteExec(`UPDATE executions SET status='failed', completed_at=CURRENT_TIMESTAMP WHERE id='${execId}'`);

  return { agent, execId, sessionId };
}

test.beforeAll(async () => {
  await waitForWorkerIdle();
});

test.afterEach(async () => {
  await waitForWorkerIdle();
});

test('recover button visible in header for failed execution with agent_session_id', async ({ page }) => {
  const { execId } = await setupRecoverableFailedExecution();

  await page.goto(`/#/execution/${execId}`);
  await expect(page.locator('h2')).toBeVisible({ timeout: 10000 });

  await expect(page.getByRole('button', { name: 'Attempt Recovery' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Re-run' })).toBeVisible();
});

test('recover button hidden in header when no agent_session_id', async ({ page }) => {
  const { execId } = await setupNonRecoverableFailedExecution();

  await page.goto(`/#/execution/${execId}`);
  await expect(page.locator('h2')).toBeVisible({ timeout: 10000 });

  await expect(page.getByRole('button', { name: 'Attempt Recovery' })).not.toBeAttached();
  await expect(page.getByRole('button', { name: 'Re-run' })).toBeVisible();
});

test('recover icon visible in session tree for recoverable failed session', async ({ page }) => {
  const { execId } = await setupRecoverableFailedExecution();

  await page.goto(`/#/execution/${execId}`);

  // Tree defaults to collapsed for terminal executions — open it first
  const disclosure = page.locator('.tree-disclosure');
  await expect(disclosure).toBeVisible({ timeout: 10000 });
  await disclosure.click();

  const sessionNode = page.locator('.tree-node').first();
  await expect(sessionNode).toBeVisible({ timeout: 10000 });

  // Hover to reveal the action button
  await sessionNode.hover();
  const recoverBtn = sessionNode.locator('.recover-btn');
  await expect(recoverBtn).toBeAttached();
});

test('recover header button triggers recovery and UI updates', async ({ page }) => {
  const { execId } = await setupRecoverableFailedExecution();

  await page.goto(`/#/execution/${execId}`);
  await expect(page.getByRole('button', { name: 'Attempt Recovery' })).toBeVisible({ timeout: 10000 });

  await page.getByRole('button', { name: 'Attempt Recovery' }).click();

  // Execution should transition away from "failed"
  // Wait for the status text to change — it should no longer say "Failed"
  await expect(page.locator('.detail-title-row')).not.toContainText('Failed', { timeout: 15000 });
});

test('recover shows error toast when agent deleted before click', async ({ page }) => {
  const { execId, sessionId } = await setupRecoverableFailedExecution();

  // Get the agent ID from the session and soft-delete it
  const agentId = sqliteQuery(`SELECT agent_id FROM sessions WHERE id='${sessionId}'`);
  sqliteExec(`UPDATE agents SET deleted_at=CURRENT_TIMESTAMP WHERE id='${agentId}'`);

  await page.goto(`/#/execution/${execId}`);
  await expect(page.getByRole('button', { name: 'Attempt Recovery' })).toBeVisible({ timeout: 10000 });

  await page.getByRole('button', { name: 'Attempt Recovery' }).click();

  // Error should appear (either inline error or the button should remain visible)
  await expect(page.locator('.action-error')).toBeVisible({ timeout: 5000 });
  await expect(page.locator('.action-error')).toContainText('agent not found');

  // Restore the agent for cleanup
  sqliteExec(`UPDATE agents SET deleted_at=NULL WHERE id='${agentId}'`);
});

test('session tree recover icon triggers child session recovery', async ({ page }) => {
  // Set up execution that's working with a failed child
  const agent = await ensureClaudeAgent();
  const { execId, sessionId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Child recovery test');
  await waitForTurnEnd(execId);

  // Add a failed child session with agent_session_id
  const childId = `child-e2e-${Date.now()}`;
  sqliteExec(`INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status, slug, cwd, agent_session_id) VALUES ('${childId}', '${execId}', '${sessionId}', '${agent.id}', 'failed', 'child', '/tmp', 'cs_child_e2e')`);

  await page.goto(`/#/execution/${execId}`);

  // Wait for tree to render
  const treeBody = page.locator('.tree-body');
  await expect(treeBody).toBeVisible({ timeout: 10000 });

  // Failed child is auto-collapsed into summary — expand it first.
  // Wait for either the summary or the failed node to appear.
  const summary = page.locator('.terminal-summary');
  const childNode = page.locator('.tree-node.failed');
  await expect(summary.or(childNode)).toBeVisible({ timeout: 10000 });
  if (await summary.isVisible()) {
    await summary.click();
  }

  await expect(childNode).toBeVisible({ timeout: 10000 });

  // Hover and click the recover button
  await childNode.hover();
  const recoverBtn = childNode.locator('.recover-btn');
  await expect(recoverBtn).toBeAttached();
  await recoverBtn.click();

  // Child should transition from failed
  await expect(childNode).not.toBeAttached({ timeout: 10000 });
});

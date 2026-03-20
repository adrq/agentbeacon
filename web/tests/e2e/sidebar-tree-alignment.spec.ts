import { test, expect } from '@playwright/test';
import { execSync } from 'child_process';
import {
  ensureDirectAgent, ensureClaudeAgent, createExecution,
  waitForWorkerIdle, waitForTurnEnd,
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

test('context placeholder renders for sessions without context data', async ({ page }) => {
  // The placeholder branch fires when available=true (claude_sdk agent) but contextWindow=0
  // (no usage_snapshot received yet). To test this reliably, insert a canceled child session
  // using a claude_sdk agent ID — it won't be processed by the worker (terminal status),
  // so no usage_snapshot is ever emitted, contextWindow stays 0, and the {:else} branch fires.
  // A direct-agent session always gets available=false → .unavailable, never .placeholder.
  const leadAgent = await ensureDirectAgent();
  const claudeAgent = await ensureClaudeAgent();
  const { execId, sessionId } = await createExecution(leadAgent.id, 'SEND_TOOL_CALL', 'Context placeholder test');
  await waitForTurnEnd(execId);

  // Canceled child with claude_sdk agent: available=true, contextWindow=0 → placeholder
  const childId = `child-placeholder-${Date.now()}`;
  sqliteExec(`INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status, slug, cwd, last_progress_at) VALUES ('${childId}', '${execId}', '${sessionId}', '${claudeAgent.id}', 'canceled', 'ph-child', '/tmp', CURRENT_TIMESTAMP)`);

  await page.goto(`/#/execution/${execId}`);

  // Terminal children are collapsed into a summary — expand it to see the child node
  const terminalSummary = page.locator('.terminal-summary');
  await expect(terminalSummary).toBeVisible({ timeout: 10000 });
  await terminalSummary.click();

  // The injected child node: available=true but contextWindow=0 → usagePct=null, not unavailable
  const childNode = page.locator(`.sidebar-node[data-session-id="${childId}"]`);
  await expect(childNode).toBeVisible({ timeout: 5000 });

  const placeholder = childNode.locator('.context-bar.placeholder');
  await expect(placeholder).toBeAttached();

  // Placeholder should NOT be .unavailable (that branch requires usage && !usage.available)
  const unavailable = childNode.locator('.context-bar.unavailable');
  expect(await unavailable.count()).toBe(0);
});

test('node-status elements align horizontally across mixed-capability sessions', async ({ page }) => {
  // Lead session: direct agent → available=false → .context-bar.unavailable (2rem wide)
  // Child session: claude_sdk agent, canceled, no usage_snapshot → available=true, contextWindow=0
  //   → .context-bar.placeholder (also 2rem wide, visibility:hidden)
  // This is the exact "mixed" scenario the bug fix targets.
  const leadAgent = await ensureDirectAgent();
  const claudeAgent = await ensureClaudeAgent();
  const { execId, sessionId } = await createExecution(leadAgent.id, 'SEND_TOOL_CALL', 'Alignment mixed test');
  await waitForTurnEnd(execId);

  // Canceled child with claude_sdk agent → placeholder branch, not unavailable
  const childId = `child-align-${Date.now()}`;
  sqliteExec(`INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status, slug, cwd, last_progress_at) VALUES ('${childId}', '${execId}', '${sessionId}', '${claudeAgent.id}', 'canceled', 'align-child', '/tmp', CURRENT_TIMESTAMP)`);

  await page.goto(`/#/execution/${execId}`);

  // Expand the terminal summary so the child node is visible alongside the lead
  const terminalSummary = page.locator('.terminal-summary');
  await expect(terminalSummary).toBeVisible({ timeout: 10000 });
  await terminalSummary.click();

  // Both nodes must now be visible
  const leadNode = page.locator(`.sidebar-node[data-session-id="${sessionId}"]`);
  const childNode = page.locator(`.sidebar-node[data-session-id="${childId}"]`);
  await expect(leadNode).toBeVisible({ timeout: 5000 });
  await expect(childNode).toBeVisible({ timeout: 5000 });

  // Verify the mixed context-bar types are actually present (test exercises the right code paths)
  await expect(leadNode.locator('.context-bar.unavailable')).toBeAttached();
  await expect(childNode.locator('.context-bar.placeholder')).toBeAttached();

  // Collect .node-status left edges for both visible nodes
  const leadBox = await leadNode.locator('.node-status').boundingBox();
  const childBox = await childNode.locator('.node-status').boundingBox();
  expect(leadBox).not.toBeNull();
  expect(childBox).not.toBeNull();

  // .node-status x positions must be close despite different context-bar types.
  // Child is depth=1 so indented by 0.75rem (~12px). Without the fix, the missing context-bar
  // column would shift the child left by an additional ~36px, giving ~48px total spread.
  // Threshold of 25px catches that regression while allowing the ~12px depth indentation.
  const spread = Math.abs(leadBox!.x - childBox!.x);
  expect(spread).toBeLessThan(25);
});

test('action-zone container is present on sidebar nodes', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Action zone test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);

  const sidebarNode = page.locator('.sidebar-node').first();
  await expect(sidebarNode).toBeVisible({ timeout: 10000 });

  // action-zone should always be present
  const actionZone = sidebarNode.locator('.action-zone');
  await expect(actionZone).toBeAttached();

  // action-zone should have a fixed width of ~36px (2.25rem)
  const width = await actionZone.evaluate(el => el.getBoundingClientRect().width);
  expect(width).toBeGreaterThan(20);
  expect(width).toBeLessThan(60);
});

test('action buttons still appear on hover after action-zone wrapper', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_TOOL_CALL', 'Action zone hover test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);

  // Find a non-terminal sidebar node
  const sidebarNode = page.locator('.sidebar-node:not(.canceled):not(.completed):not(.failed)').first();
  await expect(sidebarNode).toBeVisible({ timeout: 10000 });

  const cancelBtn = sidebarNode.locator('.action-zone .cancel-btn');
  await expect(cancelBtn).toBeAttached();

  // Before hover: opacity should be 0 (button is hidden via CSS)
  const opacityBefore = await cancelBtn.evaluate(el => getComputedStyle(el).opacity);
  expect(opacityBefore).toBe('0');

  // After hover: CSS rule .sidebar-node:hover .action-zone .action-btn sets opacity to 1.
  // Use toHaveCSS (retries) rather than evaluate (runs once) to account for the 0.1s transition.
  await sidebarNode.hover();
  await expect(cancelBtn).toHaveCSS('opacity', '1', { timeout: 1000 });
});

test('context-bar placeholder is not visible to user but occupies space', async ({ page }) => {
  // Same scenario as the placeholder-render test: canceled child with claude_sdk agent gives
  // available=true, contextWindow=0 → placeholder branch fires.
  const leadAgent = await ensureDirectAgent();
  const claudeAgent = await ensureClaudeAgent();
  const { execId, sessionId } = await createExecution(leadAgent.id, 'SEND_TOOL_CALL', 'Placeholder visibility test');
  await waitForTurnEnd(execId);

  const childId = `child-vis-${Date.now()}`;
  sqliteExec(`INSERT INTO sessions (id, execution_id, parent_session_id, agent_id, status, slug, cwd, last_progress_at) VALUES ('${childId}', '${execId}', '${sessionId}', '${claudeAgent.id}', 'canceled', 'vis-child', '/tmp', CURRENT_TIMESTAMP)`);

  await page.goto(`/#/execution/${execId}`);

  // Terminal children are collapsed — expand the summary to make the child visible
  const terminalSummary = page.locator('.terminal-summary');
  await expect(terminalSummary).toBeVisible({ timeout: 10000 });
  await terminalSummary.click();

  const childNode = page.locator(`.sidebar-node[data-session-id="${childId}"]`);
  await expect(childNode).toBeVisible({ timeout: 5000 });

  const placeholder = childNode.locator('.context-bar.placeholder');
  await expect(placeholder).toBeAttached();

  // visibility:hidden means not visible to user but still occupies layout space
  const visibility = await placeholder.evaluate(el => getComputedStyle(el).visibility);
  expect(visibility).toBe('hidden');

  // Should still have dimensions (occupies space for alignment)
  const box = await placeholder.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.width).toBeGreaterThan(10);
});

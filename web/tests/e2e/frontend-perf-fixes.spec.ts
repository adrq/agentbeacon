import { test, expect } from '@playwright/test';
import {
  ensureDirectAgent, ensureShowcaseAgent, createExecution,
  waitForWorkerIdle, waitForTurnEnd, waitForWorkerPickup, waitForWorking,
} from './helpers';

test.beforeAll(async () => {
  await waitForWorkerIdle();
});

test.afterEach(async () => {
  await waitForWorkerIdle();
});

// --- Fix #3: content-visibility: auto on chat rows ---

test('chat rows have content-visibility: auto for off-screen rendering skip', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'STREAM_CHUNKS', 'Content-visibility test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  await expect(page.locator('.chat-row').first()).toBeVisible({ timeout: 10000 });

  const cv = await page.locator('.chat-row').first().evaluate(
    el => window.getComputedStyle(el).contentVisibility,
  );
  expect(cv).toBe('auto');
});

// --- Fix #2: Streaming markdown in the active row never visually shrinks ---

test('active streaming row text never shrinks during render', async ({ page }) => {
  test.setTimeout(60000);
  await waitForWorkerIdle();

  const agent = await ensureShowcaseAgent();
  const { execId } = await createExecution(agent.id, 'Full showcase', 'No-shrink test');
  await waitForWorkerPickup(execId, 15000);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  await expect(page.locator('.chat-row').first()).toBeVisible({ timeout: 10000 });

  // Track the *last* agent-prose row (the actively streaming one) and detect
  // any >30% length regression — the exact threshold the render guard uses.
  const shrinkDetected = await page.evaluate(() => {
    return new Promise<boolean>(resolve => {
      let maxLen = 0;
      let checks = 0;
      const maxChecks = 75; // 15s at 200ms intervals
      const interval = setInterval(() => {
        const rows = document.querySelectorAll('.agent-prose .markdown-body');
        if (rows.length === 0) { if (++checks >= maxChecks) { clearInterval(interval); resolve(false); } return; }
        const last = rows[rows.length - 1];
        const len = (last.textContent ?? '').length;
        if (len > 0 && maxLen > 0 && len < maxLen * 0.7) {
          clearInterval(interval);
          resolve(true);
        }
        if (len > maxLen) maxLen = len;
        // Reset tracker when a new row appears (new message block)
        if (len < maxLen * 0.3 && len > 0 && len < 20) maxLen = len;
        if (++checks >= maxChecks) {
          clearInterval(interval);
          resolve(false);
        }
      }, 200);
    });
  });

  expect(shrinkDetected).toBe(false);
});

// --- Fix #1: Ephemeral-to-persisted handoff preserves visible text ---

test('last agent row never drops during streaming-to-persisted handoff', async ({ page }) => {
  test.setTimeout(60000);
  await waitForWorkerIdle();

  // Open during working so we observe the live ephemeral->persisted transition.
  const agent = await ensureShowcaseAgent();
  const { execId } = await createExecution(agent.id, 'Full showcase', 'Handoff test');
  await waitForWorkerPickup(execId, 15000);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  await expect(page.locator('.chat-row').first()).toBeVisible({ timeout: 10000 });

  // Track the *last* agent-prose row (the active streaming one) through the
  // handoff window. A bug here would cause visible text to drop to near-zero
  // when the ephemeral buffer clears before persisted text catches up.
  const dropDetected = await page.evaluate(() => {
    return new Promise<boolean>(resolve => {
      let maxLen = 0;
      let prevRowCount = 0;
      let checks = 0;
      const maxChecks = 100; // 20s at 200ms intervals
      const interval = setInterval(() => {
        const rows = document.querySelectorAll('.agent-prose .markdown-body');
        if (rows.length === 0) { if (++checks >= maxChecks) { clearInterval(interval); resolve(false); } return; }
        const last = rows[rows.length - 1];
        const len = (last.textContent ?? '').length;
        // Reset tracker when a genuinely new row appears (new message block)
        if (rows.length > prevRowCount && prevRowCount > 0) {
          maxLen = 0;
        }
        prevRowCount = rows.length;
        // Detect catastrophic drop in the active row
        if (maxLen > 30 && len > 0 && len < maxLen * 0.3) {
          clearInterval(interval);
          resolve(true);
        }
        if (len > maxLen) maxLen = len;
        if (++checks >= maxChecks) {
          clearInterval(interval);
          resolve(false);
        }
      }, 200);
    });
  });

  expect(dropDetected).toBe(false);

  // Wait for terminal and verify the showcase scenario completed with
  // expected final content — "Refactoring Complete" heading.
  await waitForTurnEnd(execId, 30000);
  const finalH1 = page.locator('.agent-prose .markdown-body h1').filter({ hasText: 'Refactoring Complete' });
  await expect(finalH1).toBeVisible({ timeout: 10000 });
});

// --- Large session rendering ---

test('large session with many events loads and renders', async ({ page }) => {
  test.setTimeout(60000);
  await waitForWorkerIdle();

  const agent = await ensureShowcaseAgent();
  const { execId } = await createExecution(agent.id, 'Full showcase', 'Large session test');
  await waitForWorkerPickup(execId, 15000);
  await waitForTurnEnd(execId, 30000);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Showcase agent produces many events — verify they all render
  await expect(page.locator('.chat-row').first()).toBeVisible({ timeout: 10000 });
  const rowCount = await page.locator('.chat-row').count();
  expect(rowCount).toBeGreaterThanOrEqual(5);

  // Verify content-visibility is applied (perf optimization for many rows)
  const cv = await page.locator('.chat-row').first().evaluate(
    el => window.getComputedStyle(el).contentVisibility,
  );
  expect(cv).toBe('auto');
});

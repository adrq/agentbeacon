/**
 * E2E tests for accessibility and styling: skip link, aria-label, role=alert,
 * tabindex, border-radius token, tooltips, pulse animation, dark mode vars,
 * decision card metadata, question banner background.
 */
import { test, expect } from '@playwright/test';
import {
  apiPost,
  apiGet,
  ensureDemoAgent,
  ensureDirectAgent,
  createExecution,
  waitForTurnEnd,
  waitForWorkerIdle,
  waitForWorkerPickup,
} from './helpers';

test.beforeAll(async () => {
  await waitForWorkerIdle();
});

test.afterEach(async () => {
  await waitForWorkerIdle();
});

test('decision card shows execution title and project name', async ({ page }) => {
  const agent = await ensureDemoAgent();
  const projects: { id: string; name: string }[] = await apiGet('/api/projects');
  let project = projects.find(p => p.name === 'my-webapp');
  if (!project) {
    project = await apiPost('/api/projects', {
      name: 'my-webapp',
      path: '/tmp/my-webapp',
      is_git: false,
    });
  }

  const exec = await apiPost('/api/executions', {
    agent_id: agent.id,
    prompt: 'test polish decision card',
    title: 'Polish E2E Title',
    project_id: project!.id,
    cwd: '/tmp',
  });
  const execId = exec.execution.id;
  await waitForTurnEnd(execId);

  await page.goto('/');

  // Locate the specific decision card by its unique title
  const card = page.locator('.decision-card', { hasText: 'Polish E2E Title' });
  await expect(card).toBeAttached({ timeout: 20000 });

  // Verify title is the execution title (not UUID or "Untitled execution")
  await expect(card.locator('.card-title')).toContainText('Polish E2E Title');

  // Check for project name in agent metadata on the same card
  await expect(card.locator('.card-agent')).toContainText('my-webapp');
});

test('question banner has visible background', async ({ page }) => {
  const agent = await ensureDemoAgent();
  const exec = await apiPost('/api/executions', {
    agent_id: agent.id,
    prompt: 'test banner visibility',
    title: 'Banner Test',
    cwd: '/tmp',
  });
  await waitForTurnEnd(exec.execution.id);

  await page.goto(`/#/execution/${exec.execution.id}`);

  const banner = page.locator('.question-banner');
  await expect(banner.first()).toBeVisible({ timeout: 15000 });

  // Verify the banner has a non-trivial background (alpha >= 0.08)
  const bg = await banner.first().evaluate(el =>
    getComputedStyle(el).backgroundColor
  );
  expect(bg).not.toBe('rgba(0, 0, 0, 0)');
  expect(bg).not.toBe('transparent');
  // Parse alpha explicitly: rgba → 4th channel, rgb → opaque (regression)
  let alpha: number;
  if (bg.startsWith('rgba(')) {
    const parts = bg.slice(5, -1).split(',');
    alpha = parseFloat(parts[3]);
    expect(alpha).not.toBeNaN();
  } else if (bg.startsWith('rgb(')) {
    alpha = 1;
  } else {
    throw new Error(`Unexpected backgroundColor format: ${bg}`);
  }
  // Banner must be translucent (token-derived), not opaque or near-invisible
  expect(alpha).toBeGreaterThanOrEqual(0.08);
  expect(alpha).toBeLessThan(1);
});

test('skip navigation link works', async ({ page }) => {
  await page.goto('/');
  await page.waitForTimeout(500);

  const skipLink = page.locator('.skip-link');
  await expect(skipLink).toHaveAttribute('href', '#main-content');

  // Verify the target element exists and is programmatically focusable
  const mainContent = page.locator('#main-content');
  await expect(mainContent).toBeAttached();
  await expect(mainContent).toHaveAttribute('tabindex', '-1');

  // Skip link should become visible on focus
  await skipLink.focus();
  await page.waitForTimeout(200);
  // After focus, skip-link should be at left: 0 (not -9999px)
  const left = await skipLink.evaluate(el =>
    getComputedStyle(el).left
  );
  expect(left).toBe('0px');
});

test('chat textarea has aria-label', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'aria-label test', 'Aria Test');

  await page.goto(`/#/execution/${execId}`);
  await page.waitForTimeout(1000);

  // Click Chat tab to show the chat input
  const chatTab = page.getByRole('tab', { name: 'Chat' });
  await expect(chatTab).toBeVisible({ timeout: 10000 });
  await chatTab.click();
  await page.waitForTimeout(500);

  const textarea = page.locator('textarea[aria-label="Message to agent"]');
  await expect(textarea).toBeAttached({ timeout: 5000 });
});

test('border radius is 0.375rem', async ({ page }) => {
  await page.goto('/');
  await page.waitForTimeout(500);

  const radius = await page.evaluate(() =>
    getComputedStyle(document.documentElement).getPropertyValue('--radius').trim()
  );
  expect(radius).toMatch(/^0?\.375rem$/);
});

test('nav rail buttons have tooltips', async ({ page }) => {
  await page.goto('/');
  await page.waitForTimeout(500);

  // All nav-rail-item buttons (excluding decisions toggle) should have title
  const navItems = page.locator('.nav-rail-item:not(.decisions-toggle)');
  const count = await navItems.count();
  expect(count).toBeGreaterThanOrEqual(3);

  for (let i = 0; i < count; i++) {
    const title = await navItems.nth(i).getAttribute('title');
    expect(title).toBeTruthy();
  }

  // Decisions toggle should have title matching its aria-label
  const decisionsToggle = page.locator('.nav-rail-item.decisions-toggle');
  const decTitle = await decisionsToggle.getAttribute('title');
  expect(decTitle).toBe('Toggle decisions panel');
});

test('form error has role=alert', async ({ page }) => {
  // Ensure deterministic fixtures exist
  const agent = await ensureDemoAgent();
  const projects: { id: string; name: string }[] = await apiGet('/api/projects');
  let project = projects.find(p => p.name === 'my-webapp');
  if (!project) {
    project = await apiPost('/api/projects', {
      name: 'my-webapp',
      path: '/tmp/my-webapp',
      is_git: false,
    });
  }

  await page.goto('/');
  await page.waitForTimeout(500);

  await page.getByRole('button', { name: '+ New' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog).toBeVisible();

  // Start button should be disabled when form is incomplete
  const startBtn = dialog.getByRole('button', { name: 'Start' });
  await expect(startBtn).toBeDisabled();

  // Fill the form using known fixture labels (not fragile index-based selection)
  await dialog.locator('#exec-project').selectOption({ label: 'my-webapp' });
  await dialog.locator('#exec-agent').selectOption({ label: agent.name });
  await dialog.locator('#exec-task').fill('trigger error test');
  await expect(startBtn).toBeEnabled({ timeout: 3000 });

  // Intercept the API call to force a server error
  await page.route('**/api/executions', route => {
    if (route.request().method() === 'POST') {
      route.fulfill({ status: 500, body: 'Internal Server Error' });
    } else {
      route.continue();
    }
  });

  await startBtn.click();

  // The error div with role="alert" should now be visible
  const alertEl = dialog.getByRole('alert');
  await expect(alertEl).toBeVisible({ timeout: 5000 });

  // Clean up route intercept and close modal
  await page.unroute('**/api/executions');
  await dialog.getByRole('button', { name: 'Cancel' }).click();
});

test('dark mode CSS variables are loaded', async ({ page }) => {
  await page.goto('/');
  await page.waitForTimeout(500);

  const bgVar = await page.evaluate(() =>
    getComputedStyle(document.documentElement).getPropertyValue('--background').trim()
  );
  expect(bgVar.length).toBeGreaterThan(0);
});

test('working status uses glow animation', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'glow test', 'Glow Test');
  await waitForWorkerPickup(execId, 15000);

  // Navigate to execution detail where StatusBadge renders
  await page.goto(`/#/execution/${execId}`);
  await page.waitForTimeout(1000);

  // The working state is transient with mock SDK — verify the CSS animation
  // rule is wired up by checking the stylesheet directly.
  // Svelte scopes keyframe names, so search for any keyframes containing "pulse".
  const pulseAnimation = await page.evaluate(() => {
    for (const sheet of document.styleSheets) {
      try {
        for (const rule of sheet.cssRules) {
          if (rule instanceof CSSKeyframesRule && rule.name.includes('pulse')) {
            const frameText = Array.from(rule.cssRules)
              .map(r => r.cssText).join(' ');
            return frameText;
          }
        }
      } catch { /* cross-origin sheets */ }
    }
    return null;
  });

  expect(pulseAnimation).not.toBeNull();
  expect(pulseAnimation).toContain('box-shadow');
});

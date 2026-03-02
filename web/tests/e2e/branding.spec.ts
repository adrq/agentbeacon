import { test, expect } from '@playwright/test';
import {
  ensureDirectAgent,
  createExecution,
  waitForWorkerIdle,
  waitForTurnEnd,
} from './helpers';

test.beforeAll(async () => {
  await waitForWorkerIdle();
});
test.afterEach(async () => {
  await waitForWorkerIdle();
});

test('app header renders branded logo', async ({ page }) => {
  await page.goto('/');
  const logo = page.locator('img.beacon-icon');
  await expect(logo).toBeVisible();
  // Vite inlines small SVGs as data URIs; verify it's an <img> with branded content
  const { tagName, src } = await logo.evaluate(el => ({
    tagName: el.tagName.toLowerCase(),
    src: (el as HTMLImageElement).src,
  }));
  expect(tagName).toBe('img');
  // FF5722 is the orange beam gradient color unique to the AgentBeacon logo
  expect(src).toContain('FF5722');
});

test('favicon is not vite placeholder', async ({ page }) => {
  await page.goto('/');
  const icons = page.locator('link[rel="icon"]');
  const count = await icons.count();
  for (let i = 0; i < count; i++) {
    const href = await icons.nth(i).getAttribute('href');
    expect(href).not.toContain('vite');
  }
});

test('status badge renders distinct dot shape', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'STREAM_CHUNKS', 'Badge shape test');
  await waitForTurnEnd(execId);
  await page.goto(`/#/execution/${execId}`);
  const dot = page.locator('.badge .dot');
  await expect(dot).toBeVisible();
  // Verify dot has status-specific shape styling, not a generic filled circle.
  // STREAM_CHUNKS may resolve to input-required, canceled, or completed.
  const shape = await dot.evaluate(el => {
    const s = getComputedStyle(el);
    return { clipPath: s.clipPath, height: s.height, borderStyle: s.borderStyle, borderRadius: s.borderRadius };
  });
  const hasDiamond = shape.clipPath !== 'none' && shape.clipPath.includes('polygon');
  const hasDash = shape.height === '2px';
  const hasHollowCircle = shape.borderStyle === 'solid'; // turn-complete or submitted
  const hasCheckmark = shape.borderRadius === '0px'; // completed checkmark or failed X (pseudo-elements)
  expect(hasDiamond || hasDash || hasHollowCircle || hasCheckmark).toBe(true);
});

test('no off-scale 12px font sizes in app shell', async ({ page }) => {
  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'SEND_MARKDOWN', 'Font size test');
  await waitForTurnEnd(execId);
  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();
  await expect(page.locator('.agent-prose').first()).toBeVisible({ timeout: 10000 });
  const offScaleElements = await page.evaluate(() => {
    const shell = document.querySelector('.app-shell');
    if (!shell) return [];
    const offScale: string[] = [];
    for (const el of shell.querySelectorAll('*:not(.markdown-body *)')) {
      const fs = getComputedStyle(el).fontSize;
      if (fs === '12px' && el.textContent?.trim() && el.children.length === 0) {
        offScale.push(`${el.tagName}.${el.className.toString().slice(0, 40)}`);
      }
    }
    return offScale;
  });
  expect(offScaleElements).toHaveLength(0);
});

test('geist fonts are loaded', async ({ page }) => {
  await page.goto('/');
  const fontsLoaded = await page.evaluate(async () => {
    await document.fonts.ready;
    return {
      geist: document.fonts.check('1em Geist'),
      geistMono: document.fonts.check('1em "Geist Mono"'),
    };
  });
  expect(fontsLoaded.geist).toBe(true);
  expect(fontsLoaded.geistMono).toBe(true);
});

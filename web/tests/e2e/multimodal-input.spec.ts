import { test, expect } from '@playwright/test';
import {
  createExecution, ensureDirectAgent, waitForTurnEnd,
  waitForWorkerIdle, apiPost, apiGet,
} from './helpers';

test.beforeAll(async () => {
  await waitForWorkerIdle();
});

test.afterEach(async () => {
  await waitForWorkerIdle();
});

// --- Test 1: text-only message still works after API change ---

test('text-only message still works after API change', async ({ page }) => {
  test.setTimeout(30000);

  const agent = await ensureDirectAgent();
  const { execId, sessionId } = await createExecution(agent.id, 'hello', 'text-only test');
  await waitForTurnEnd(execId);

  // Send a text-only follow-up using the parts-based API
  await apiPost(`/api/sessions/${sessionId}/message`, {
    parts: [{ kind: 'text', text: 'follow up' }],
  });
  await waitForTurnEnd(execId);

  // Navigate to chat view and verify user message is visible
  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  const userBubble = page.locator('.user-bubble');
  await expect(userBubble.last()).toBeVisible({ timeout: 10000 });
  await expect(userBubble.last()).toContainText('follow up');
});

// --- Test 2: image file picker shows preview ---

test('image file picker shows preview', async ({ page }) => {
  test.setTimeout(30000);

  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'preview test', 'image preview test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Wait for the chat input area to be visible
  await expect(page.locator('textarea').first()).toBeVisible({ timeout: 10000 });

  // Create a test PNG blob and dispatch it to the file input
  await page.evaluate(() => {
    const canvas = document.createElement('canvas');
    canvas.width = 50; canvas.height = 50;
    const ctx = canvas.getContext('2d')!;
    ctx.fillStyle = '#ff0000';
    ctx.fillRect(0, 0, 50, 50);
    canvas.toBlob(blob => {
      const file = new File([blob!], 'test.png', { type: 'image/png' });
      const dt = new DataTransfer();
      dt.items.add(file);
      const input = document.querySelector('input[type="file"]') as HTMLInputElement;
      input.files = dt.files;
      input.dispatchEvent(new Event('change', { bubbles: true }));
    }, 'image/png');
  });

  await page.waitForTimeout(500);

  // Verify the attachment preview thumbnail appears
  const thumb = page.locator('.attachment-strip img').first();
  await expect(thumb).toBeVisible({ timeout: 5000 });
});

// --- Test 3: oversized image shows error ---

test('oversized image shows error', async ({ page }) => {
  test.setTimeout(30000);

  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'oversize test', 'oversize image test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Wait for the chat input area to be visible
  await expect(page.locator('textarea').first()).toBeVisible({ timeout: 10000 });

  // Create a 6MB file and dispatch to file input
  await page.evaluate(() => {
    const buf = new ArrayBuffer(6 * 1024 * 1024);
    const file = new File([buf], 'huge.png', { type: 'image/png' });
    const dt = new DataTransfer();
    dt.items.add(file);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    input.files = dt.files;
    input.dispatchEvent(new Event('change', { bubbles: true }));
  });

  await page.waitForTimeout(500);

  // Verify error message appears containing "5MB"
  await expect(page.getByText('5MB')).toBeVisible({ timeout: 5000 });
});

// --- Test 4: remove attachment before send ---

test('remove attachment before send', async ({ page }) => {
  test.setTimeout(30000);

  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'remove test', 'remove attachment test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Wait for the chat input area to be visible
  await expect(page.locator('textarea').first()).toBeVisible({ timeout: 10000 });

  // Add an image via file input
  await page.evaluate(() => {
    const canvas = document.createElement('canvas');
    canvas.width = 50; canvas.height = 50;
    const ctx = canvas.getContext('2d')!;
    ctx.fillStyle = '#00ff00';
    ctx.fillRect(0, 0, 50, 50);
    canvas.toBlob(blob => {
      const file = new File([blob!], 'test.png', { type: 'image/png' });
      const dt = new DataTransfer();
      dt.items.add(file);
      const input = document.querySelector('input[type="file"]') as HTMLInputElement;
      input.files = dt.files;
      input.dispatchEvent(new Event('change', { bubbles: true }));
    }, 'image/png');
  });

  await page.waitForTimeout(500);

  // Verify preview shows
  const thumb = page.locator('.attachment-strip img').first();
  await expect(thumb).toBeVisible({ timeout: 5000 });

  // Click remove button via dispatchEvent (more reliable in Firefox)
  await page.locator('.attachment-remove').first().dispatchEvent('click');

  // Verify preview disappears (wait with timeout for reactivity)
  await expect(thumb).not.toBeVisible({ timeout: 5000 });

  // Verify send button is disabled when textarea is empty
  const sendBtn = page.getByRole('button', { name: 'Send message' });
  await expect(sendBtn).toBeDisabled();
});

// --- Test 5: send message with image attachment ---

test('send message with image attachment', async ({ page }) => {
  test.setTimeout(30000);

  const agent = await ensureDirectAgent();
  const { execId } = await createExecution(agent.id, 'image send test', 'send with image test');
  await waitForTurnEnd(execId);

  await page.goto(`/#/execution/${execId}`);
  await page.getByRole('tab', { name: 'Chat' }).click();

  // Wait for the chat input area to be visible
  const textarea = page.locator('textarea').first();
  await expect(textarea).toBeVisible({ timeout: 10000 });

  // Add image via file input
  await page.evaluate(() => {
    const canvas = document.createElement('canvas');
    canvas.width = 50; canvas.height = 50;
    const ctx = canvas.getContext('2d')!;
    ctx.fillStyle = '#0000ff';
    ctx.fillRect(0, 0, 50, 50);
    canvas.toBlob(blob => {
      const file = new File([blob!], 'test.png', { type: 'image/png' });
      const dt = new DataTransfer();
      dt.items.add(file);
      const input = document.querySelector('input[type="file"]') as HTMLInputElement;
      input.files = dt.files;
      input.dispatchEvent(new Event('change', { bubbles: true }));
    }, 'image/png');
  });

  await page.waitForTimeout(500);

  // Type text in textarea
  await textarea.fill('here is an image');

  // Click send
  const sendBtn = page.getByRole('button', { name: 'Send message' });
  await sendBtn.click();

  // Wait for user text message to appear in chat (exclude image bubbles)
  const textBubble = page.locator('.user-bubble:not(.user-image-bubble)');
  await expect(textBubble.last()).toBeVisible({ timeout: 10000 });
  await expect(textBubble.last()).toContainText('here is an image');

  // Verify image is rendered (as a separate user-image entry)
  const userImage = page.locator('.user-image');
  await expect(userImage.last()).toBeVisible({ timeout: 5000 });
});

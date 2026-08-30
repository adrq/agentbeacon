import { test, expect, type Page } from '@playwright/test';

// Verifies sidebar execution ordering (ExecutionList.svelte): executions
// needing a response sort first, then running, then idle, then finished —
// regardless of recency. Expected top-to-bottom order:
//   pending question  →  working  →  idle awaiting_input  →  completed
//
// Fully network-mocked so it is deterministic and needs no seeded backend data.

const PENDING_EXEC_ID = 'exec-pending-question';
const WORKING_EXEC_ID = 'exec-working';
const IDLE_EXEC_ID = 'exec-idle-awaiting';
const COMPLETED_EXEC_ID = 'exec-completed';

const TITLE_PENDING = 'Pending Question Exec';
const TITLE_WORKING = 'Working Exec';
const TITLE_IDLE = 'Idle Awaiting Exec';
const TITLE_COMPLETED = 'Completed Exec';

function makeExecution(overrides: Record<string, unknown>) {
  return {
    id: 'exec',
    project_id: null,
    parent_execution_id: null,
    context_id: 'ctx',
    desired: 'run',
    outcome: null,
    status: 'working',
    completion_eligible: false,
    title: null,
    metadata: {},
    max_depth: 3,
    max_width: 3,
    sandbox_policy: { fs_level: 'workspace' },
    created_at: '2026-07-24T10:00:00Z',
    updated_at: '2026-07-24T10:00:00Z',
    completed_at: null,
    ...overrides,
  };
}

// Timestamps are deliberately arranged so that a naive "newest first" sort
// would NOT reproduce the expected order — the tier split must dominate.
const EXECUTIONS = [
  makeExecution({
    id: IDLE_EXEC_ID,
    title: TITLE_IDLE,
    status: 'awaiting_input',
    updated_at: '2026-07-24T12:00:00Z', // newest, but idle → must sink below working
  }),
  makeExecution({
    id: COMPLETED_EXEC_ID,
    title: TITLE_COMPLETED,
    status: 'completed',
    outcome: 'completed',
    completed_at: '2026-07-24T11:30:00Z',
    updated_at: '2026-07-24T11:30:00Z',
  }),
  makeExecution({
    id: WORKING_EXEC_ID,
    title: TITLE_WORKING,
    status: 'working',
    updated_at: '2026-07-24T10:30:00Z',
  }),
  makeExecution({
    id: PENDING_EXEC_ID,
    title: TITLE_PENDING,
    status: 'awaiting_input', // idle status; flagged as pending question below
    updated_at: '2026-07-24T09:00:00Z', // oldest, but a real question → must float to top
  }),
];

// One pending decision batch flags PENDING_EXEC_ID as needing a response.
const DECISIONS = {
  decisions: [
    {
      batch_id: 'batch-1',
      execution_id: PENDING_EXEC_ID,
      execution_title: TITLE_PENDING,
      session_id: 'session-1',
      agent_name: 'Test Agent',
      hierarchical_name: 'root',
      status: 'pending',
      importance: 'blocking',
      questions: [
        { question: 'Proceed with the risky migration?', context: null, options: null, batch_index: 0 },
      ],
      answer: null,
      answered_at: null,
      dismissed_at: null,
      created_at: '2026-07-24T09:00:00Z',
    },
  ],
};

async function mockApi(page: Page) {
  // List GET only — the trailing `*` matches optional query params but not the
  // `/api/executions/{id}` detail path (a `*` does not cross `/`).
  await page.route('**/api/executions*', route => {
    if (route.request().method() === 'GET') {
      route.fulfill({ contentType: 'application/json', body: JSON.stringify(EXECUTIONS) });
    } else {
      route.continue();
    }
  });
  await page.route('**/api/decisions*', route => {
    if (route.request().method() === 'GET') {
      route.fulfill({ contentType: 'application/json', body: JSON.stringify(DECISIONS) });
    } else {
      route.continue();
    }
  });
}

test.afterEach(async ({ page }) => {
  await page.unroute('**/api/executions*');
  await page.unroute('**/api/decisions*');
});

test('sidebar sorts pending-question above working above idle above terminal', async ({ page }) => {
  await mockApi(page);

  await page.goto('/#/executions');
  await expect(page.locator('.exec-list')).toBeVisible();

  // Attention banner only appears once a pending question is present — waiting
  // on it ensures the question flag is applied before we assert order.
  await expect(page.locator('.attention-banner')).toBeVisible();

  const items = page.locator('.exec-list .exec-item');
  await expect(items).toHaveCount(4);

  const titles = await page.locator('.exec-list .exec-item .exec-title').allTextContents();
  expect(titles).toEqual([TITLE_PENDING, TITLE_WORKING, TITLE_IDLE, TITLE_COMPLETED]);
});

const FLIPPER_EXEC_ID = 'exec-flipper';
const STALE_EXEC_ID = 'exec-stale-newer';
const UNTOUCHED_EXEC_ID = 'exec-untouched-older';
const TITLE_FLIPPER = 'Flipper Exec';
const TITLE_STALE = 'Stale Newer Exec';
const TITLE_UNTOUCHED = 'Untouched Older Exec';

// `updated_at` does not reflect every activity, so an execution that was just
// worked on can carry an older `updated_at` than one nobody has touched in a
// while. The client-side activity high-water mark must keep it on top once it
// goes idle and both land in the same tier.
test('recently active execution stays above an idle one with a newer updated_at', async ({ page }) => {
  let flipperStatus = 'working';

  // Payload order deliberately contradicts both the expected order and the
  // `updated_at` order: the untouched pair never changes status, so it must fall
  // back to `updated_at`. If observation stamped executions on first sight they
  // would all tie and the sort would silently degrade to this array order.
  await page.route('**/api/executions*', route => {
    if (route.request().method() !== 'GET') {
      route.continue();
      return;
    }
    route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify([
        makeExecution({
          id: UNTOUCHED_EXEC_ID,
          title: TITLE_UNTOUCHED,
          status: 'awaiting_input',
          updated_at: '2026-07-24T10:00:00Z',
        }),
        makeExecution({
          id: STALE_EXEC_ID,
          title: TITLE_STALE,
          status: 'awaiting_input',
          updated_at: '2026-07-24T12:00:00Z',
        }),
        makeExecution({
          id: FLIPPER_EXEC_ID,
          title: TITLE_FLIPPER,
          status: flipperStatus,
          updated_at: '2026-07-24T09:00:00Z',
        }),
      ]),
    });
  });
  await page.route('**/api/decisions*', route => {
    if (route.request().method() === 'GET') {
      route.fulfill({ contentType: 'application/json', body: JSON.stringify({ decisions: [] }) });
    } else {
      route.continue();
    }
  });

  await page.goto('/#/executions');
  await expect(page.locator('.exec-list')).toBeVisible();

  const items = page.locator('.exec-list .exec-item');
  await expect(items).toHaveCount(3);
  expect(await page.locator('.exec-list .exec-item .exec-title').allTextContents())
    .toEqual([TITLE_FLIPPER, TITLE_STALE, TITLE_UNTOUCHED]);

  // Same order below, but earned by recency rather than tier: once the flipper
  // goes idle all three are tier 2.
  flipperStatus = 'awaiting_input';
  await expect(
    items.filter({ hasText: TITLE_FLIPPER }).locator('.exec-status')
  ).toHaveText('turn complete', { timeout: 15_000 });

  expect(await page.locator('.exec-list .exec-item .exec-title').allTextContents())
    .toEqual([TITLE_FLIPPER, TITLE_STALE, TITLE_UNTOUCHED]);
});

const EARLY_EXEC_ID = 'exec-early-working';
const LATE_EXEC_ID = 'exec-late-working';
const TITLE_EARLY = 'Early Working Exec';
const TITLE_LATE = 'Late Working Exec';

// Within the running tier, the execution that most recently started working
// sorts first. Stamping on every poll while `working` instead of only on the
// transition into it would give both the same timestamp and collapse this to
// payload order.
test('concurrently working executions order by when they started working', async ({ page }) => {
  let earlyStatus = 'awaiting_input';
  let lateStatus = 'awaiting_input';

  // The execution that starts working later is listed first in the payload and
  // carries the older `updated_at`, so neither payload order nor `updated_at`
  // can produce the expected order by accident.
  await page.route('**/api/executions*', route => {
    if (route.request().method() !== 'GET') {
      route.continue();
      return;
    }
    route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify([
        makeExecution({
          id: EARLY_EXEC_ID,
          title: TITLE_EARLY,
          status: earlyStatus,
          updated_at: '2026-07-24T12:00:00Z',
        }),
        makeExecution({
          id: LATE_EXEC_ID,
          title: TITLE_LATE,
          status: lateStatus,
          updated_at: '2026-07-24T09:00:00Z',
        }),
      ]),
    });
  });
  await page.route('**/api/decisions*', route => {
    if (route.request().method() === 'GET') {
      route.fulfill({ contentType: 'application/json', body: JSON.stringify({ decisions: [] }) });
    } else {
      route.continue();
    }
  });

  await page.goto('/#/executions');
  await expect(page.locator('.exec-list')).toBeVisible();

  const items = page.locator('.exec-list .exec-item');
  await expect(items).toHaveCount(2);
  // Baseline: both idle and unstamped, so `updated_at` decides.
  expect(await page.locator('.exec-list .exec-item .exec-title').allTextContents())
    .toEqual([TITLE_EARLY, TITLE_LATE]);

  // Separate polls, so the two transitions land at distinguishable times.
  earlyStatus = 'working';
  await expect(
    items.filter({ hasText: TITLE_EARLY }).locator('.exec-status')
  ).toHaveText('working', { timeout: 15_000 });

  lateStatus = 'working';
  await expect(
    items.filter({ hasText: TITLE_LATE }).locator('.exec-status')
  ).toHaveText('working', { timeout: 15_000 });

  expect(await page.locator('.exec-list .exec-item .exec-title').allTextContents())
    .toEqual([TITLE_LATE, TITLE_EARLY]);
});

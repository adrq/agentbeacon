// SPDX-FileCopyrightText: Copyright 2026 Adrian Quiroga
// SPDX-License-Identifier: AGPL-3.0-or-later

import { test, expect, type Page } from '@playwright/test';
import { apiDelete, apiGet } from './helpers';
import { E2E_PASSWORD, E2E_USERNAME } from './auth';

test.use({ storageState: { cookies: [], origins: [] } });

async function signIn(page: Page, password = E2E_PASSWORD) {
  await page.getByTestId('login-username').fill(E2E_USERNAME);
  await page.getByTestId('login-password').fill(password);
  await page.getByTestId('login-submit').click();
}

test('an unauthenticated visit shows the login page', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByTestId('login-form')).toBeVisible();
  await expect(page.locator('.nav-rail')).not.toBeAttached();
});

test('a wrong password shows an error and stays on the login page', async ({ page }) => {
  await page.goto('/');
  await signIn(page, 'not-the-password');
  await expect(page.getByTestId('login-error')).toHaveText('Invalid username or password.');
  await expect(page.getByTestId('login-form')).toBeVisible();
});

test('signing in shows the app and signing out returns to the login page', async ({ page }) => {
  await page.goto('/');
  await signIn(page);
  await expect(page.locator('.nav-rail')).toBeVisible();

  await page.goto('/#/settings');
  await page.getByTestId('sign-out').click();
  await expect(page.getByTestId('login-form')).toBeVisible();

  await page.reload();
  await expect(page.getByTestId('login-form')).toBeVisible();
});

test('a login revoked through the API returns to the login page', async ({ page }) => {
  await page.goto('/');
  await signIn(page);
  await expect(page.locator('.nav-rail')).toBeVisible();

  const logins: { id: string; label: string | null; created_at: string }[] =
    await apiGet('/api/v1/auth/logins');
  const web = logins
    .filter(l => l.label === 'web')
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
  for (const login of web) {
    await apiDelete(`/api/v1/auth/logins/${login.id}`);
  }

  await page.goto('/#/settings');
  await page.reload();
  await expect(page.getByTestId('login-form')).toBeVisible();
});

test('a failed sign-out keeps the app, and a retry signs out', async ({ page }) => {
  await page.goto('/');
  await signIn(page);
  await expect(page.locator('.nav-rail')).toBeVisible();

  await page.goto('/#/settings');
  await page.route('**/api/v1/auth/logout', route => route.abort('failed'));
  await page.getByTestId('sign-out').click();
  await expect(page.getByTestId('sign-out-error')).toHaveText(
    'Sign out failed. You are still signed in.'
  );
  await expect(page.getByTestId('sign-out')).toHaveText('Retry sign out');
  await expect(page.getByTestId('login-form')).not.toBeAttached();

  await page.route('**/api/v1/auth/logout', route =>
    route.fulfill({ status: 500, body: 'server error' })
  );
  await page.getByTestId('sign-out').click();
  await expect(page.getByTestId('sign-out-error')).toBeVisible();
  await expect(page.getByTestId('login-form')).not.toBeAttached();

  await page.reload();
  await expect(page.locator('.nav-rail')).toBeVisible();

  await page.unroute('**/api/v1/auth/logout');
  await page.goto('/#/settings');
  await page.getByTestId('sign-out').click();
  await expect(page.getByTestId('login-form')).toBeVisible();
  await page.reload();
  await expect(page.getByTestId('login-form')).toBeVisible();
});

test('a sign-out answered 401 shows the login page', async ({ page }) => {
  await page.goto('/');
  await signIn(page);
  await expect(page.locator('.nav-rail')).toBeVisible();

  await page.goto('/#/settings');
  await page.route('**/api/v1/auth/logout', route =>
    route.fulfill({
      status: 401,
      contentType: 'application/problem+json',
      body: JSON.stringify({ code: 'auth.unauthorized', title: 'Unauthorized', status: 401 }),
    })
  );
  await page.getByTestId('sign-out').click();
  await expect(page.getByTestId('login-form')).toBeVisible();
  await expect(page.getByTestId('sign-out-error')).not.toBeAttached();
});

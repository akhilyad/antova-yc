import { test, expect } from '@playwright/test';

const WORKBENCH = 'https://antova-workbench.akhilfut.workers.dev';

test('landing renders the pitch without page errors', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto('/');
  await expect(page.getByRole('heading', { level: 1 })).toContainText('Find what the lowest bid');
  await expect(page.getByText('Priced per tender.')).toBeVisible();
  // No horizontal scroll at any width.
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
  expect(errors).toEqual([]);
});

test('hero CTA opens the live workbench sample audit', async ({ page }) => {
  await page.goto('/');
  const cta = page.locator('main a', { hasText: 'Audit a tender free' }).first();
  await expect(cta).toHaveAttribute('href', `${WORKBENCH}/`);
});

test('audit form rejects a malformed email before sending anything', async ({ page }) => {
  const posts: string[] = [];
  page.on('request', (r) => r.method() === 'POST' && posts.push(r.url()));
  await page.goto('/#audit');
  await page.locator('#audit-email').fill('not-an-email');
  await page.locator('#audit button[type=submit]').click();
  await expect(page.getByText('Enter a valid email address.')).toBeVisible();
  expect(posts).toEqual([]);
});

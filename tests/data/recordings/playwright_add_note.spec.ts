import { test, expect } from '@playwright/test';

test('test', async ({ page }) => {
  await page.goto('http://127.0.0.1:8911/login');
  await page.getByPlaceholder('Username').fill('demo');
  await page.getByPlaceholder('Password').fill('demo123');
  await page.getByRole('button', { name: 'Log in' }).click();
  await expect(page.locator('#welcome')).toBeVisible();
  await page.locator('#note-input').fill('recorded note ts');
  await page.locator('#note-input').press('Enter');
  await expect(page.locator('#notes')).toContainText('recorded note ts');
});

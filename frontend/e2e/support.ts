import { readFile } from 'node:fs/promises'
import { expect } from '@playwright/test'
import type { Locator, Page } from '@playwright/test'

export const SEEDED_PROJECT = 'Защита продукта DeckDNA'

export async function openApp(page: Page, path = '/'): Promise<void> {
  await page.goto(path)
  await expect(page.getByRole('banner').getByRole('link', { name: 'DeckDNA — к проектам' })).toBeVisible()
}

export async function downloadFrom(page: Page, trigger: Locator): Promise<{ name: string; bytes: Buffer }> {
  const [download] = await Promise.all([page.waitForEvent('download'), trigger.click()])
  expect(await download.failure()).toBeNull()
  const path = await download.path()
  return { name: download.suggestedFilename(), bytes: await readFile(path) }
}

export async function expectRenderedPreview(preview: Locator): Promise<void> {
  await preview.scrollIntoViewIfNeeded()
  await expect(preview).toHaveAttribute('data-state', 'ready', { timeout: 20_000 })
}

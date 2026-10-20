import { expect, test } from '@playwright/test'
import { openApp, SEEDED_PROJECT } from './support'

test('polish keeps the assistant, slide and navigation usable at desktop widths in both themes', async ({ page }, testInfo) => {
  await openApp(page)
  await page.getByRole('list', { name: 'Проекты', exact: true }).getByRole('link', { name: new RegExp(SEEDED_PROJECT) }).click()
  const chat = page.getByRole('region', { name: 'Помощник', exact: true })
  await expect(chat.getByRole('textbox')).toBeEnabled()
  const preview = page.getByRole('img', { name: 'Слайд 1', exact: true })
  await expect(preview).toHaveAttribute('data-state', 'ready', { timeout: 30_000 })
  await expect(page.getByRole('button', { name: 'Все рамки' })).toHaveAttribute('aria-pressed', 'false')
  for (const width of [1280, 1440, 1920]) {
    await page.setViewportSize({ width, height: 900 })
    await expect(preview).toHaveAttribute('data-state', 'ready')
    await expect.poll(async () => {
      const box = await preview.boundingBox()
      return Boolean(box && box.y + box.height <= 860 && box.width > 600)
    }).toBe(true)
    const panel = await chat.boundingBox()
    const slide = await preview.boundingBox()
    expect(panel && slide && panel.x >= slide.x + slide.width).toBeTruthy()
    await expect(chat.getByRole('button', { name: 'Отправить' })).toBeInViewport()
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width)
    if (testInfo.project.name === 'chromium') await page.screenshot({ path: testInfo.outputPath(`polish-${width}.png`) })
  }
  await page.getByRole('button', { name: 'Переключить тему' }).click()
  await expect(chat.getByRole('textbox')).toBeInViewport()
  if (testInfo.project.name === 'chromium') await page.screenshot({ path: testInfo.outputPath('polish-dark.png') })
  await page.getByRole('button', { name: 'Следующий слайд' }).click()
  await expect(chat).toContainText('Слайд 2 · Доводка')
  await expect(page.getByRole('button', { name: 'Все рамки' })).toHaveAttribute('aria-pressed', 'false')
})

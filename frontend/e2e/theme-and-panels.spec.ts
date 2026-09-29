import { expect, test } from '@playwright/test'
import { openApp } from './support'

test('dark theme survives a reload', async ({ page }) => {
  await openApp(page)
  const html = page.locator('html')
  await expect(html).toHaveAttribute('data-theme', 'light')
  const toggle = page.getByRole('button', { name: 'Переключить тему' })
  await expect(toggle).toHaveAttribute('data-theme-target', 'dark')
  await toggle.click()
  await expect(html).toHaveAttribute('data-theme', 'dark')
  await expect(toggle).toHaveAttribute('data-theme-target', 'light')
  const darkBackground = await page.evaluate(() => getComputedStyle(document.body).backgroundColor)

  await page.reload()
  await expect(page.getByRole('heading', { level: 1, name: 'Проекты' })).toBeVisible()
  await expect(html).toHaveAttribute('data-theme', 'dark')
  expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe(darkBackground)

  await page.getByRole('button', { name: 'Переключить тему' }).click()
  await expect(html).toHaveAttribute('data-theme', 'light')
  expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).not.toBe(darkBackground)
})

test('provider panel rejects a model above 35B', async ({ page }) => {
  await openApp(page)
  await page.getByRole('button', { name: 'Модели', exact: true }).click()
  const panel = page.getByRole('dialog', { name: 'Провайдер моделей' })
  await expect(panel).toBeVisible()
  await expect(panel).toContainText('до 35B')

  const form = panel.getByRole('form', { name: 'Подключение провайдера моделей' })
  await form.getByRole('textbox', { name: 'Адрес API' }).fill('https://inference.example/v1')
  const textModel = form.getByRole('textbox', { name: 'Модель для текста' })
  await textModel.fill('Qwen/Qwen2.5-72B-Instruct')
  await form.getByRole('textbox', { name: 'Модель для картинок слайдов' }).fill('Qwen/Qwen2.5-VL-32B-Instruct')
  await form.getByRole('textbox', { name: 'Токен' }).fill('e2e-token')
  await form.getByRole('button', { name: 'Подключить и проверить' }).click()

  await expect(textModel).toHaveAttribute('aria-invalid', 'true')
  await expect(form.getByRole('alert').filter({ hasText: 'Больше 35B параметров' })).toBeVisible()

  await textModel.fill('Qwen/Qwen3-32B')
  await expect(textModel).not.toHaveAttribute('aria-invalid', 'true')
  await expect(form.getByRole('alert').filter({ hasText: 'Больше 35B параметров' })).toHaveCount(0)
})

test('about panel lists audit rules split into D and N', async ({ page }) => {
  await openApp(page)
  await page.getByRole('button', { name: 'О системе' }).click()
  const panel = page.getByRole('dialog', { name: 'О системе' })
  const rules = panel.getByRole('region', { name: 'Правила аудита' })
  await expect(rules).toBeVisible()

  const intro = (await rules.getByRole('paragraph').first().textContent()) ?? ''
  const declared = intro.match(/(\d+) детерминированных.*?(\d+) контекстных/)
  expect(declared).not.toBeNull()
  const [, deterministic, contextual] = declared ?? []

  const items = rules.getByRole('listitem')
  await expect(items.filter({ has: page.getByLabel(/^Детерминированная проверка/) })).toHaveCount(Number(deterministic))
  await expect(items.filter({ has: page.getByLabel(/^Контекстная проверка/) })).toHaveCount(Number(contextual))
  expect(Number(deterministic)).toBeGreaterThan(0)
  expect(Number(contextual)).toBeGreaterThan(0)

  await panel.getByRole('button', { name: 'Закрыть' }).click()
  await expect(panel).toBeHidden()
})

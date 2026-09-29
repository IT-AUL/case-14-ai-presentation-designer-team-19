import { existsSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'

const TEMPLATE = process.env.E2E_TEMPLATE ?? ''
const CONTENT = fileURLToPath(new URL('../../tests/fixtures/content/deckdna_pitch_rich.md', import.meta.url))

test.skip(!TEMPLATE || !existsSync(TEMPLATE), 'Set E2E_TEMPLATE to a .pptx template to run against a live backend')

const apiFailures: string[] = []

function trackApi(page: Page) {
  page.on('response', (response) => {
    const url = response.url()
    if (url.includes('/api/v1/') && response.status() >= 500) apiFailures.push(`${response.status()} ${response.request().method()} ${url}`)
  })
  page.on('pageerror', (error) => apiFailures.push(`pageerror ${error.message}`))
}

test('live backend: template, brief, three variants, audit repair, export', async ({ page }) => {
  trackApi(page)
  const name = `Live ${Date.now()}`
  await page.goto('/')
  await expect(page.getByRole('banner').getByRole('link', { name: 'DeckDNA — к проектам' })).toBeVisible()

  await test.step('create project and upload a real template', async () => {
    await page.getByRole('button', { name: 'Новый проект' }).click()
    const dialog = page.getByRole('dialog', { name: 'Новый проект' })
    await dialog.getByRole('textbox', { name: /^Название/ }).fill(name)
    await dialog.getByRole('button', { name: 'Создать и загрузить шаблон' }).click()
    const dropzone = page.getByRole('button', { name: /Перетащите шаблон сюда/ })
    const [chooser] = await Promise.all([page.waitForEvent('filechooser'), dropzone.click()])
    await chooser.setFiles(TEMPLATE)
    await expect(page.getByRole('region', { name: 'Что система поняла' })).toBeVisible({ timeout: 120_000 })
  })

  await test.step('design DNA shows real palette, fonts and layouts', async () => {
    const palette = page.getByRole('region', { name: 'Палитра' })
    await expect(palette.getByRole('list', { name: 'Цвета темы' }).getByRole('listitem')).not.toHaveCount(0)
    const fonts = page.getByRole('region', { name: 'Шрифты' })
    await expect(fonts.getByRole('list', { name: 'Шрифты на слайдах' }).getByRole('listitem')).not.toHaveCount(0)
  })

  await test.step('brief: upload content and start generation', async () => {
    await page.getByRole('button', { name: /Дальше: бриф/ }).click()
    await expect(page.getByRole('heading', { level: 1, name: 'О чём презентация' })).toBeVisible()
    await page.getByRole('radiogroup', { name: 'Назначение' }).getByRole('radio', { name: /^Продукт/ }).check()
    const fileInput = page.locator('input[type="file"]').first()
    await fileInput.setInputFiles(CONTENT)
    await expect(page.getByRole('main')).toContainText('deckdna_pitch_rich.md', { timeout: 30_000 })
    await page.getByRole('button', { name: /Собрать 3 варианта/ }).click()
  })

  await test.step('generation finishes with three variants', async () => {
    await expect(page.getByRole('heading', { level: 1, name: 'Три варианта готовы' })).toBeVisible({ timeout: 240_000 })
    for (const name of ['Близко к шаблону', 'Сбалансированный', 'Визуальный']) {
      await expect(page.getByRole('article', { name, exact: true }).getByRole('link', { name, exact: true })).toBeVisible()
    }
    await expect(page.getByRole('timer')).toHaveCount(0)
  })

  await test.step('variants render real PDF thumbnails', async () => {
    await page.getByRole('link', { name: /Сравнить по слайдам/ }).click()
    for (const strategy of ['Близко к шаблону', 'Сбалансированный', 'Визуальный']) {
      const first = page.getByRole('list', { name: `Слайды варианта «${strategy}»` }).getByRole('img').first()
      await expect(async () => {
        await first.scrollIntoViewIfNeeded()
        await expect(first).toHaveAttribute('data-state', 'ready', { timeout: 5_000 })
      }).toPass({ timeout: 40_000 })
    }
  })

  await test.step('audit: overlays are drawn and repair produces a journal', async () => {
    await page.getByRole('region', { name: 'Сбалансированный' }).getByRole('link', { name: 'Открыть и проверить' }).click()
    await expect(page.getByRole('region', { name: 'Сводка аудита' })).toBeVisible()
    const panel = page.getByRole('complementary', { name: 'Проблемы' })
    await panel.getByRole('radio', { name: 'По слайдам' }).click()
    const selectable = panel.getByRole('checkbox', { name: /^Выбрать для исправления/ })
    await expect(selectable.first()).toBeVisible()
    await selectable.nth(0).check()
    await page.getByRole('button', { name: /^Исправить выбранное \(1\)/ }).click()
    const journalTab = panel.getByRole('tab', { name: /^Журнал · [1-9]/ })
    await expect(journalTab).toBeVisible({ timeout: 120_000 })
  })

  await test.step('export: PPTX downloads with a valid package', async () => {
    await page.getByRole('region', { name: 'Сводка аудита' }).getByRole('link', { name: /Экспорт/ }).click()
    await expect(page.getByRole('heading', { level: 1, name: 'Заберите колоду' })).toBeVisible()
    const link = page.getByRole('complementary', { name: 'Скачать колоду' }).getByRole('link', { name: 'Скачать PPTX' })
    await expect(link).toBeVisible({ timeout: 60_000 })
    const [download] = await Promise.all([page.waitForEvent('download'), link.click()])
    const bytes = await readFile(await download.path())
    expect(bytes.subarray(0, 2).toString('latin1')).toBe('PK')
    expect(bytes.length).toBeGreaterThan(10_000)
  })

  expect(apiFailures).toEqual([])
})

test('live backend: system panel reflects the real capabilities', async ({ page }) => {
  trackApi(page)
  await page.goto('/')
  await page.getByRole('button', { name: 'О системе' }).click()
  const dialog = page.getByRole('dialog', { name: 'О системе' })
  const capabilities = dialog.getByRole('region', { name: 'Возможности сервиса' })
  await expect(capabilities).toBeVisible()
  await expect(capabilities.getByRole('listitem').filter({ hasText: 'Экспорт PPTX' })).toContainText('есть')
  await expect(capabilities.getByRole('listitem').filter({ hasText: 'Экспорт PDF' })).toContainText('есть')
  await expect(capabilities.getByRole('listitem').filter({ hasText: 'Паспорт качества' })).toContainText('есть')
  expect(apiFailures).toEqual([])
})

test('live backend: provider session is created, probed and revoked', async ({ page }) => {
  trackApi(page)
  await page.goto('/')
  await page.getByRole('button', { name: 'Модели', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Провайдер моделей' })
  const form = dialog.getByRole('form', { name: 'Подключение провайдера моделей' })
  await form.getByRole('textbox', { name: 'Адрес API' }).fill('http://127.0.0.1:9/v1')
  await form.getByRole('textbox', { name: /^Модель для текста/ }).fill('qwen3-32b')
  await form.getByRole('textbox', { name: /^Модель для картинок слайдов/ }).fill('qwen2.5-vl-32b')
  await form.getByLabel('Токен').fill('test-token-not-a-secret')
  await form.getByRole('button', { name: /Сохранить|Подключить/ }).click()
  await expect(dialog.getByRole('region', { name: 'Активное подключение' })).toBeVisible()
  await dialog.getByRole('button', { name: 'Проверить подключение' }).click()
  await expect(dialog.getByRole('region', { name: 'Проверка возможностей' })).toBeVisible({ timeout: 60_000 })
  await dialog.getByRole('button', { name: 'Отключить' }).click()
  await expect(dialog.getByRole('region', { name: 'Активное подключение' })).toBeHidden()
  expect(apiFailures).toEqual([])
})

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

test('live backend: seven steps from files to a downloaded deck', async ({ page }) => {
  test.setTimeout(600_000)
  trackApi(page)
  const rejected: string[] = []
  page.on('response', (response) => {
    if (response.url().includes('/api/v1/') && response.status() === 422) rejected.push(`${response.request().method()} ${response.url()}`)
  })
  await page.goto('/')
  await expect(page.getByRole('banner').getByRole('link', { name: 'DeckDNA — к проектам' })).toBeVisible()

  await test.step('step 1: a real template, content and a topic', async () => {
    await page.getByRole('button', { name: 'Новый проект' }).click()
    const dialog = page.getByRole('dialog', { name: 'Новый проект' })
    await dialog.getByRole('textbox', { name: /^Название/ }).fill(`Live ${Date.now()}`)
    await dialog.getByRole('button', { name: 'Создать и закинуть файлы' }).click()
    await expect(page.getByRole('heading', { level: 1, name: 'Что у вас есть?' })).toBeVisible()
    await page.getByRole('textbox', { name: 'О чём презентация' }).fill('DeckDNA: компилятор корпоративных презентаций для жюри хакатона')
    const dropzone = page.getByRole('button', { name: /Перетащите файлы сюда/ })
    const [chooser] = await Promise.all([page.waitForEvent('filechooser'), dropzone.click()])
    await chooser.setFiles([TEMPLATE, CONTENT])
    await expect(page.getByRole('region', { name: 'Как будет выглядеть презентация' })).toContainText('Так будут выглядеть ваши слайды', { timeout: 180_000 })
    await expect(page.getByRole('button', { name: 'Дальше' })).toBeEnabled({ timeout: 60_000 })
    await page.getByRole('button', { name: 'Дальше' }).click()
  })

  await test.step('steps 2–3: what was found, then the plan', async () => {
    await expect(page.getByRole('heading', { level: 1, name: 'Вот что я нашёл' })).toBeVisible({ timeout: 180_000 })
    await expect(page.getByRole('button', { name: 'Дальше' })).toBeEnabled({ timeout: 180_000 })
    await page.getByRole('button', { name: 'Дальше' }).click()
    await expect(page).toHaveURL(/\/(questions|plan)$/, { timeout: 180_000 })
    if (page.url().endsWith('/questions')) await page.getByRole('button', { name: 'Составить план', exact: true }).click()
    await expect(page.getByRole('heading', { level: 1, name: 'План презентации' })).toBeVisible({ timeout: 240_000 })
  })

  await test.step('step 4: edit a slide title and pick a style', async () => {
    await page.getByRole('list', { name: 'Слайды' }).getByRole('button', { name: /^Слайд 2:/ }).click()
    const title = page.getByRole('complementary', { name: 'Слайд 2' }).getByRole('textbox', { name: 'Заголовок — вывод слайда' })
    await title.fill('Шаблон — это правила, а не картинка')
    await page.keyboard.press('Escape')
    await page.getByRole('button', { name: 'Выбрать стиль' }).click()
  })

  await test.step('steps 5–6: build in the recommended style', async () => {
    await expect(page.getByRole('heading', { level: 1, name: 'Как оформить?' })).toBeVisible({ timeout: 60_000 })
    await expect(page.getByRole('button', { name: 'Собрать', exact: true })).toBeEnabled({ timeout: 60_000 })
    await page.getByRole('button', { name: 'Собрать', exact: true }).click()
    await expect(page.getByRole('heading', { level: 1, name: /Собираю презентацию|Презентация готова/ })).toBeVisible({ timeout: 60_000 })
    await expect(page).toHaveURL(/\/polish/, { timeout: 300_000 })
  })

  await test.step('step 7: repair one issue and download the PPTX', async () => {
    await expect(page.getByRole('region', { name: 'Сводка аудита' })).toBeVisible({ timeout: 60_000 })
    const panel = page.getByRole('complementary', { name: 'Проблемы' })
    await panel.getByRole('radio', { name: 'По слайдам' }).click()
    const selectable = panel.getByRole('checkbox', { name: /^Выбрать для исправления/ })
    await expect(selectable.first()).toBeVisible()
    await selectable.nth(0).check()
    await page.getByRole('button', { name: /^Исправить выбранное \(1\)/ }).click()
    await expect(panel.getByRole('tab', { name: /^Журнал · [1-9]/ })).toBeVisible({ timeout: 120_000 })

    const downloads = page.getByRole('group', { name: 'Скачать презентацию' })
    const pptx = downloads.getByRole('link', { name: 'Скачать PPTX' }).or(downloads.getByRole('button', { name: 'Скачать PPTX' }))
    await expect(pptx).toBeVisible({ timeout: 60_000 })
    const [download] = await Promise.all([page.waitForEvent('download', { timeout: 120_000 }), pptx.click()])
    const bytes = await readFile(await download.path())
    expect(bytes.subarray(0, 2).toString('latin1')).toBe('PK')
    expect(bytes.length).toBeGreaterThan(10_000)
  })

  expect(rejected).toEqual([])
  expect(apiFailures).toEqual([])
})

test('live backend: system panel reflects the real capabilities', async ({ page }) => {
  trackApi(page)
  await page.goto('/')
  await page.getByRole('button', { name: 'Настройки' }).click()
  await page.getByRole('menuitem', { name: 'О системе' }).click()
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
  await page.getByRole('button', { name: 'Настройки' }).click()
  await page.getByRole('menuitem', { name: /Модели/ }).click()
  const dialog = page.getByRole('dialog', { name: 'Провайдер моделей' })
  const form = dialog.getByRole('form', { name: 'Подключение провайдера моделей' })
  await form.getByRole('textbox', { name: 'Адрес API' }).fill('https://example.com/v1')
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

import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support'

const TEMPLATE = fileURLToPath(new URL('./fixtures/minimal-template.pptx', import.meta.url))
const TEMPLATE_PAYLOAD = { name: 'minimal-template.pptx', mimeType: 'application/vnd.openxmlformats-officedocument.presentationml.presentation', buffer: readFileSync(TEMPLATE) }

async function createProject(page: Page): Promise<void> {
  await openApp(page)
  await page.getByRole('button', { name: 'Новый проект' }).click()
  const dialog = page.getByRole('dialog', { name: 'Новый проект' })
  await expect(dialog).toBeVisible()
  await dialog.getByRole('textbox', { name: /^Название/ }).fill(`E2E приём ${Date.now()}`)
  await dialog.getByRole('button', { name: 'Создать и закинуть файлы' }).click()
  await expect(dialog).toBeHidden()
  await expect(page).toHaveURL(/\/projects\/[^/]+\/start$/)
  await expect(page.getByRole('heading', { level: 1, name: 'Что у вас есть?' })).toBeVisible()
}

test('assistant step 1: upload files, check their types, see the template on the table, go next', async ({ page }) => {
  await createProject(page)

  const next = page.getByRole('button', { name: 'Дальше' })
  await expect(next).toBeDisabled()
  await expect(page.getByRole('button', { name: 'Сделай сам' })).toBeDisabled()

  await page.getByRole('textbox', { name: 'О чём презентация' }).fill('Итоги продаж за два года для совета директоров')

  const dropzone = page.getByRole('button', { name: /Перетащите файлы сюда/ })
  const [chooser] = await Promise.all([page.waitForEvent('filechooser'), dropzone.click()])
  await chooser.setFiles([
    TEMPLATE_PAYLOAD,
    { name: 'sales.csv', mimeType: 'text/csv', buffer: Buffer.from('month,sales\n1,10\n') },
    { name: 'brand_tokens.json', mimeType: 'application/json', buffer: Buffer.from('{"color":{"primary":"#E4572E"}}') },
    { name: 'brand_guide.webp', mimeType: 'image/webp', buffer: Buffer.from('RIFF') },
    { name: 'raw_scan.tiff', mimeType: 'image/tiff', buffer: Buffer.from('II') },
  ])

  const list = page.getByRole('list', { name: 'Файлы' })
  await expect(list).toContainText('minimal-template.pptx')
  await expect(list).toContainText('sales.csv')
  await expect(list).toContainText('brand_tokens.json')
  await expect(list).toContainText('brand_guide.webp')

  const kind = page.getByRole('combobox', { name: 'Что это за файл: sales.csv' })
  await expect(kind).toHaveValue('data')
  const imageKind = page.getByRole('combobox', { name: 'Что это за файл: brand_guide.webp' })
  await expect(imageKind).toHaveValue('brandbook')

  await page.getByRole('button', { name: 'Убрать raw_scan.tiff' }).click()
  await expect(list).not.toContainText('raw_scan.tiff')

  const table = page.getByRole('region', { name: 'Как будет выглядеть презентация' })
  const passport = table.getByRole('article', { name: 'Паспорт шаблона' })
  await expect(passport).toContainText('54 слайда')
  await expect(passport).not.toContainText('Так будут выглядеть ваши слайды')
  await expect(passport.getByRole('region', { name: 'Палитра' })).toContainText('%')
  await expect(passport.getByRole('region', { name: 'Шрифты' })).toContainText('Заявлено в теме')
  await expect(passport.getByRole('region', { name: 'Шрифты' })).toContainText('На слайдах')
  await expect(passport.getByRole('region', { name: 'Макеты по ролям' })).toContainText('Рабочие')
  expect(await passport.textContent()).not.toMatch(/#[0-9a-f]{6}/i)

  const priority = table.getByRole('radiogroup', { name: 'Чьи цвета и шрифты важнее' })
  await expect(priority.getByRole('radio', { name: 'Брендбук' })).toHaveAttribute('aria-checked', 'true')
  const brand = passport.getByRole('region', { name: /Брендбук/ })
  await expect(brand.locator('[data-chosen]').first()).toContainText('Брендбук')
  await brand.getByRole('button', { name: 'Разобрать брендбук' }).click()
  await expect(brand.getByRole('list', { name: 'Цвета брендбука' })).toContainText('стр. 2')
  await expect(brand.getByRole('list', { name: 'Шрифты брендбука' })).toContainText('стр. 4')
  expect(await passport.textContent()).not.toMatch(/#[0-9a-f]{6}/i)
  await priority.getByRole('radio', { name: 'Шаблон' }).click()
  await expect(priority.getByRole('radio', { name: 'Шаблон' })).toHaveAttribute('aria-checked', 'true')
  await expect(brand.locator('[data-chosen]').first()).toContainText('Шаблон')

  const [download] = await Promise.all([page.waitForEvent('download'), passport.getByRole('button', { name: 'Скачать правила (JSON)' }).click()])
  expect(download.suggestedFilename()).toMatch(/design-dna\.json$/)

  await table.getByRole('button', { name: 'Все правила шаблона' }).click()
  const rules = page.getByRole('complementary', { name: 'Правила шаблона' })
  await expect(rules).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(rules).toBeHidden()

  await expect(next).toBeEnabled()
  await next.click()
  await expect(page).toHaveURL(/\/understanding$/)
})

test('assistant step 1: template only still needs material or idea', async ({ page }) => {
  await createProject(page)

  const dropzone = page.getByRole('button', { name: /Перетащите файлы сюда/ })
  const [chooser] = await Promise.all([page.waitForEvent('filechooser'), dropzone.click()])
  await chooser.setFiles([TEMPLATE])

  await expect(page.getByRole('list', { name: 'Файлы' })).toContainText('minimal-template.pptx')
  await expect(page.getByRole('button', { name: 'Дальше' })).toBeDisabled()
  await expect(page.getByText('Добавьте материалы или напишите, о чём презентация.')).toBeVisible()

  await page.getByRole('textbox', { name: 'О чём презентация' }).fill('Питч нового продукта для инвесторов')
  await expect(page.getByRole('button', { name: 'Дальше' })).toBeEnabled()
})

import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'
import { openApp } from './support'

const TEMPLATE = fileURLToPath(new URL('./fixtures/minimal-template.pptx', import.meta.url))

test('new project: create, upload a template, see the analysis', async ({ page }) => {
  const name = `E2E проект ${Date.now()}`
  await openApp(page)

  await page.getByRole('button', { name: 'Новый проект' }).click()
  const dialog = page.getByRole('dialog', { name: 'Новый проект' })
  await expect(dialog).toBeVisible()
  await dialog.getByRole('textbox', { name: /^Название/ }).fill(name)
  await dialog.getByRole('button', { name: 'Создать и загрузить шаблон' }).click()
  await expect(dialog).toBeHidden()
  await expect(page.getByRole('navigation', { name: 'Навигация' })).toContainText(name)

  const dropzone = page.getByRole('button', { name: /Перетащите шаблон сюда/ })
  await expect(dropzone).toBeVisible()
  const [chooser] = await Promise.all([page.waitForEvent('filechooser'), dropzone.click()])
  await chooser.setFiles(TEMPLATE)

  await expect(page.getByRole('heading', { level: 1, name: 'ДНК шаблона' })).toBeVisible()
  await expect(page.getByRole('region', { name: 'Что система поняла' })).toBeVisible({ timeout: 20_000 })
  await expect(page.getByRole('main')).toContainText('minimal-template.pptx')
  await expect(page.getByRole('region', { name: 'Палитра' }).getByRole('list', { name: 'Цвета темы' }).getByRole('listitem')).not.toHaveCount(0)
  await expect(page.getByRole('button', { name: /Дальше: бриф/ })).toBeEnabled()

  await page.getByRole('banner').getByRole('link', { name: 'DeckDNA — к проектам' }).click()
  await expect(page.getByRole('list', { name: 'Проекты' }).getByRole('link', { name: new RegExp(name) })).toBeVisible()
})

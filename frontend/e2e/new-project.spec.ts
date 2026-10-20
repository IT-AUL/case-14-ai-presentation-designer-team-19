import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'
import { openApp, SEEDED_PROJECT } from './support'

const TEMPLATE = fileURLToPath(new URL('./fixtures/minimal-template.pptx', import.meta.url))

test('new project: create lands on intake and accepts a template', async ({ page }) => {
  const name = `E2E проект ${Date.now()}`
  await openApp(page)

  await page.getByRole('button', { name: 'Новый проект' }).click()
  const dialog = page.getByRole('dialog', { name: 'Новый проект' })
  await expect(dialog).toBeVisible()
  await dialog.getByRole('textbox', { name: /^Название/ }).fill(name)
  await dialog.getByRole('button', { name: 'Создать и закинуть файлы' }).click()
  await expect(dialog).toBeHidden()
  await expect(page).toHaveURL(/\/projects\/[^/]+\/start$/)
  await expect(page.getByRole('navigation', { name: 'Навигация' })).toContainText(name)

  const dropzone = page.getByRole('button', { name: /Перетащите файлы сюда/ })
  await expect(dropzone).toBeVisible()
  const [chooser] = await Promise.all([page.waitForEvent('filechooser'), dropzone.click()])
  await chooser.setFiles(TEMPLATE)

  await expect(page.getByRole('list', { name: 'Файлы' })).toContainText('minimal-template.pptx')
  await expect(page.getByRole('combobox', { name: 'Что это за файл: minimal-template.pptx' })).toHaveValue('template')
})

test('home: a topic and a template start a named project on step 1', async ({ page }) => {
  const topic = `Итоги продаж за год ${Date.now()}`
  await openApp(page)

  const start = page.getByRole('button', { name: 'Начать' })
  await expect(start).toBeDisabled()
  await page.getByRole('textbox', { name: 'О чём презентация' }).fill(topic)
  const [chooser] = await Promise.all([page.waitForEvent('filechooser'), page.getByRole('button', { name: 'Прикрепить шаблон и материалы' }).click()])
  await chooser.setFiles(TEMPLATE)
  await expect(page.getByRole('list', { name: 'Прикреплённые файлы' })).toContainText('minimal-template.pptx')
  await start.click()

  await expect(page).toHaveURL(/\/projects\/[^/]+\/start$/)
  await expect(page.getByRole('navigation', { name: 'Навигация' })).toContainText(topic)
  await expect(page.getByRole('textbox', { name: 'О чём презентация' })).toHaveValue(topic)
  await expect(page.getByRole('list', { name: 'Файлы' })).toContainText('minimal-template.pptx')
})

test('old addresses lead to the single page of their step', async ({ page }) => {
  await openApp(page)
  const link = page.getByRole('list', { name: 'Проекты', exact: true }).getByRole('link', { name: new RegExp(SEEDED_PROJECT) })
  await expect(link).toBeVisible()
  const href = (await link.getAttribute('href')) ?? ''
  const match = /\/projects\/([^/]+)\/runs\/([^/]+)/.exec(href)
  expect(match).not.toBeNull()
  const [, projectId, runId] = match as RegExpExecArray

  await page.goto(`/projects/${projectId}/template`)
  await expect(page).toHaveURL(new RegExp(`/projects/${projectId}/start$`))
  await page.goto(`/projects/${projectId}/brief`)
  await expect(page).toHaveURL(new RegExp(`/projects/${projectId}/start$`))
  for (const old of ['variants', 'audit', 'export']) {
    await page.goto(`/projects/${projectId}/runs/${runId}/${old}`)
    await expect(page).toHaveURL(new RegExp(`/runs/${runId}/polish`), { timeout: 20_000 })
  }
})

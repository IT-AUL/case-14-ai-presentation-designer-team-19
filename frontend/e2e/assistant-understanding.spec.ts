import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support'

async function gotoClient(page: Page, path: string): Promise<void> {
  await page.evaluate((to) => {
    window.history.pushState({}, '', to)
    window.dispatchEvent(new PopStateEvent('popstate'))
  }, path)
}

async function seedAssistantProject(page: Page): Promise<string> {
  const name = `E2E понимание ${Date.now()}`
  await openApp(page)
  await page.getByRole('button', { name: 'Новый проект' }).click()
  const dialog = page.getByRole('dialog', { name: 'Новый проект' })
  await dialog.getByRole('textbox', { name: /^Название/ }).fill(name)
  await dialog.getByRole('button', { name: 'Создать и закинуть файлы' }).click()
  await expect(dialog).toBeHidden()
  await expect(page).toHaveURL(/\/projects\/[^/]+\//)
  const projectId = /\/projects\/([^/]+)/.exec(new URL(page.url()).pathname)?.[1] ?? ''
  const result = await page.evaluate(async (id) => {
    const form = new FormData()
    form.append('idea', 'Итоги продаж 2024–2025 для совета директоров')
    form.append('files', new File([new Uint8Array([0x50, 0x4b, 3, 4])], 'template.pptx'))
    form.append('files', new File(['month,revenue\n2024-01,10\n2024-02,12'], 'purchases.csv', { type: 'text/csv' }))
    const response = await fetch(`/api/v1/projects/${id}/intake`, { method: 'POST', body: form })
    return response.status
  }, projectId)
  expect(result).toBe(202)
  return projectId
}

test('assistant steps 2–3: data insights, insight details, questions and plan', async ({ page }) => {
  test.setTimeout(90_000)
  const projectId = await seedAssistantProject(page)

  await test.step('step 2 lays data insights on the table', async () => {
    await gotoClient(page, `/projects/${projectId}/understanding`)
    await expect(page.getByRole('heading', { level: 1, name: 'Вот что я нашёл' })).toBeVisible()
    await expect(page.getByText(/Хватит примерно на/)).toBeVisible()
    const list = page.getByRole('list', { name: 'Выводы из данных' })
    const card = list.getByRole('listitem').first()
    await expect(card).toBeVisible()
    await expect(page.getByText(/строк/).first()).toBeVisible()
  })

  await test.step('insight cards act on the table', async () => {
    const list = page.getByRole('list', { name: 'Выводы из данных' })
    const card = list.getByRole('listitem', { name: /Вывод:/ }).first()
    await expect(card.getByRole('button', { name: 'Согласен' })).toBeVisible()
    await card.getByRole('button', { name: 'Не включать' }).click()
    const strip = page.getByRole('group', { name: 'Исключённые выводы' })
    await expect(strip).toBeVisible()
    await strip.getByRole('button', { name: 'Вернуть' }).click()
    await expect(strip).toBeHidden()
    await expect(list.getByRole('listitem', { name: /Вывод:/ }).first()).toBeVisible()
  })

  await test.step('an insight opens in the side panel with its data', async () => {
    await page.getByRole('list', { name: 'Выводы из данных' }).getByRole('button', { name: /^Открыть вывод:/ }).first().click()
    const panel = page.getByRole('complementary', { name: 'Вывод' })
    await expect(panel.getByRole('heading', { name: 'Данные и график' })).toBeVisible()
    await expect(panel).toContainText('purchases.csv')
    await page.keyboard.press('Escape')
    await expect(panel).toBeHidden()
  })

  await test.step('step 3 asks with a recommended answer and leads to the plan', async () => {
    await page.getByRole('button', { name: 'Дальше', exact: true }).click()
    await expect(page).toHaveURL(new RegExp(`/projects/${projectId}/questions`))
    const spec = page.getByRole('region', { name: 'Спецификация будущей колоды' })
    await expect(spec).toBeVisible()
    const audience = page.getByRole('radio', { name: /Совет директоров/ })
    await expect(audience).toBeVisible()
    await expect(page.getByText('советую').first()).toBeVisible()
    await audience.click()
    await expect(audience).toBeChecked()
    await expect(spec).toContainText('Совет директоров')
    await expect(spec).toContainText('Финансовый эффект, стратегия и решения')

    await page.getByRole('button', { name: 'Составить план', exact: true }).click()
    await expect(page).toHaveURL(new RegExp(`/projects/${projectId}/plan`), { timeout: 30_000 })
    await expect(page.getByRole('heading', { level: 1, name: 'План презентации' })).toBeVisible({ timeout: 30_000 })
  })
})

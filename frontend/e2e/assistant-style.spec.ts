import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'

function fixture<T>(name: string): { body: T } {
  return JSON.parse(readFileSync(fileURLToPath(new URL(`../src/shared/api/mocks/fixtures/assistant/${name}.json`, import.meta.url)), 'utf8'))
}

interface PlanBody {
  plan_id: string
  plans: { deck_plan: unknown }[]
}

const plan = fixture<PlanBody>('data-plan').body
const projectId = fixture<{ id: string }>('data-project').body.id
const deckPlan = plan.plans[0]?.deck_plan

const storedPlanRequest = {
  request: {
    template_id: 'tpl_7c4f33420b0e4c1cb783c86d1cfb12c1',
    content_pack_id: 'pack_52549aa7639543a8b68b55c5f60d42a8',
    strategies: ['balanced'],
    brief: {
      purpose: 'Итоги продаж за год для совета директоров',
      audience: 'Совет директоров',
      language: 'ru',
      target_slide_count: 8,
    },
    use_llm: false,
  },
  result: {
    planId: plan.plan_id,
    proposals: [{ strategy: 'balanced', deckPlan }],
    sufficiency: null,
    research: null,
  },
  createdAt: '2026-10-17T13:52:04.311Z',
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(
    (seed: { key: string; value: unknown }) => window.localStorage.setItem(seed.key, JSON.stringify(seed.value)),
    { key: `deckdna.plan-request.v1:${projectId}`, value: storedPlanRequest },
  )
  await page.goto(`/projects/${projectId}/style`)
})

test('style step: three styles, recommendation, trial slides, compare, build one style', async ({ page }) => {
  await expect(page.getByRole('heading', { level: 1, name: 'Как оформить?' })).toBeVisible()

  const table = page.getByRole('region', { name: 'Пробные слайды' })
  const group = table.getByRole('radiogroup', { name: 'Стиль вёрстки' })
  await expect(group.getByRole('radio')).toHaveCount(3)

  const visual = group.getByRole('radio', { name: /Визуальный/ })
  await expect(visual).toBeChecked()
  await expect(group.getByText('советую')).toBeVisible()
  await expect(group).toContainText('Больше графиков и схем, меньше текста')

  await expect(table.locator('img')).toHaveCount(9, { timeout: 20_000 })
  for (const strategy of ['faithful', 'balanced', 'visual']) {
    await expect(group.locator(`[data-strategy="${strategy}"]`).getByRole('button', { name: /Сравнить слайд/ })).toHaveCount(3)
  }
  await expect.poll(() => table.locator('img').evaluateAll((images) => images.every((image) => (image as HTMLImageElement).naturalWidth > 0))).toBe(true)
  const strictOverview = await group.locator('[data-strategy="faithful"] img').nth(1).getAttribute('src')
  const visualOverview = await group.locator('[data-strategy="visual"] img').nth(1).getAttribute('src')
  expect(visualOverview).not.toBe(strictOverview)
  await expect(group.locator('[data-recommended]')).toHaveAttribute('data-strategy', 'visual')

  await table.getByRole('button', { name: 'Сравнить слайд 1 в трёх стилях (Визуальный)' }).click()
  const dialog = page.getByRole('dialog')
  await expect(dialog.getByRole('heading', { name: /Сравнение трёх стилей · Слайд 01/ })).toBeVisible()
  await expect(dialog.getByRole('region')).toHaveCount(3)
  await dialog.getByRole('button', { name: 'Следующий слайд' }).click()
  await expect(dialog.getByRole('heading', { name: /Сравнение трёх стилей · Слайд 02/ })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)

  await group.getByText('Сбалансированный').click()
  await expect(group.getByRole('radio', { name: /Сбалансированный/ })).toBeChecked()

  await page.getByRole('button', { name: 'Собрать только выбранный' }).click()
  await expect(page).toHaveURL(new RegExp(`/projects/${projectId}/runs/`))
  await expect(page.getByRole('heading', { level: 1, name: /Собираю презентацию|Презентация готова/ })).toBeVisible({ timeout: 15_000 })
  await expect(page).toHaveURL(/\/polish/, { timeout: 20_000 })
})

test('style step: build all three opens the build table', async ({ page }) => {
  const group = page.getByRole('radiogroup', { name: 'Стиль вёрстки' })
  await expect(group.getByRole('radio')).toHaveCount(3)

  await page.getByRole('button', { name: 'Собрать все три' }).click()
  await expect(page).toHaveURL(new RegExp(`/projects/${projectId}/runs/`))
  await expect(page.getByRole('region', { name: 'Слайды презентации' })).toBeVisible({ timeout: 15_000 })
})

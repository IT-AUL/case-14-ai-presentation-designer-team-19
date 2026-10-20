import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { openApp } from './support'

const PROJECT_ID = 'prj_d7a5e61be4be4491b158c5f93699a38d'
const PACK_ID = 'pack-7aa41ae9'
const TEMPLATE_ID = 'tpl_4497762842c84a478245dcfb47552046'
const KEY_MESSAGE = 'Выручка выросла в 2,1 раза за счёт Москвы и корпоративного сегмента'

interface FixtureBody {
  plan_id: string
  plans: {
    deck_plan: {
      brief: { key_message?: string | null }
      slides: { id: string; supports_key_message?: boolean | null }[]
    }
  }[]
}

const planFixture = JSON.parse(
  readFileSync(fileURLToPath(new URL('../src/shared/api/mocks/fixtures/assistant/data-plan.json', import.meta.url)), 'utf8'),
).body as FixtureBody

function seedPlanRequest() {
  const first = planFixture.plans[0]
  if (!first) throw new Error('plan fixture has no plans')
  const deckPlan = structuredClone(first.deck_plan)
  deckPlan.brief.key_message = KEY_MESSAGE
  deckPlan.slides = deckPlan.slides.map((slide: { id: string }) => (slide.id === 'slide-6' ? { ...slide, supports_key_message: false } : slide))
  return {
    request: {
      template_id: TEMPLATE_ID,
      content_pack_id: PACK_ID,
      brief: deckPlan.brief,
      strategies: ['balanced'],
      use_llm: false,
    },
    result: {
      planId: planFixture.plan_id,
      proposals: [{ strategy: 'balanced', deckPlan }],
      sufficiency: null,
      research: null,
    },
    keyMessage: KEY_MESSAGE,
    createdAt: new Date().toISOString(),
  }
}

async function openPlan(page: Page) {
  const seed = JSON.stringify(seedPlanRequest())
  await page.addInitScript(
    ([key, value]) => window.localStorage.setItem(key as string, value as string),
    [`deckdna.plan-request.v1:${PROJECT_ID}`, seed],
  )
  await openApp(page, `/projects/${PROJECT_ID}/plan`)
  await expect(page.getByRole('heading', { level: 1, name: 'План презентации' })).toBeVisible({ timeout: 30_000 })
}

function slideTile(page: Page, number: number) {
  return page.getByRole('list', { name: 'Слайды' }).getByRole('button', { name: `Подробнее о слайде ${number}`, exact: true })
}

test.describe('plan step', () => {
  test('edits the key message', async ({ page }) => {
    await openPlan(page)

    const input = page.getByRole('textbox', { name: 'Главная мысль' })
    await expect(input).toHaveValue(KEY_MESSAGE)
    await input.fill('Электроника даёт 61% выручки')
    await input.blur()
    await expect(page.getByRole('button', { name: 'Отменить последнее' })).toBeVisible()
  })

  test('reads the storyline on the cards and edits a title in place', async ({ page }) => {
    await openPlan(page)

    const card = page.getByRole('article', { name: 'Слайд 3' })
    await expect(card).toContainText('Данные')
    await expect(card).toContainText('График · Линия')
    await expect(card).toContainText('Из таблицы')
    await expect(card.getByRole('textbox', { name: /^Пункт 1 слайда 3$/ })).toBeVisible()

    const title = card.getByRole('textbox', { name: 'Заголовок слайда 3' })
    await title.fill('Выручка выросла вдвое')
    await expect(page.getByRole('button', { name: 'Отменить последнее' })).toBeVisible()

    await page.getByRole('radio', { name: 'Сетка' }).click()
    await expect(page.getByRole('list', { name: 'Слайды' })).toHaveAttribute('data-view', 'grid')
    await expect(page.getByRole('textbox', { name: 'Заголовок слайда 3' })).toHaveValue('Выручка выросла вдвое')
  })

  test('opens a slide, explains its chart and switches the chart type', async ({ page }) => {
    await openPlan(page)

    await slideTile(page, 3).click()
    const editor = page.getByRole('complementary', { name: 'Слайд 3' })
    await expect(editor.getByRole('textbox', { name: 'Заголовок — вывод слайда' })).toBeVisible()
    await expect(editor).toContainText('24 точки по месяцам')
    const reasoningBox = editor.getByLabel(/^Почему такой график/)
    await expect(reasoningBox).toContainText('Почему такой график')
    await expect(reasoningBox).toContainText('Столбцы:')
    const chart = editor.getByRole('radiogroup', { name: 'Тип графика' })
    await expect(chart.getByRole('radio', { name: 'Линия' })).toHaveAttribute('aria-checked', 'true')

    const patched = page.waitForResponse((response) => response.url().includes('/slides/slide-2') && response.request().method() === 'PATCH')
    await chart.getByRole('radio', { name: 'Столбцы' }).click()
    await patched
    await expect(chart.getByRole('radio', { name: 'Столбцы' })).toHaveAttribute('aria-checked', 'true')

    await page.keyboard.press('Escape')
    await expect(editor).toBeHidden()
  })

  test('shows calm shortage banner when capacity < requested and offers actions', async ({ page }) => {
    const base = seedPlanRequest()
    const thinSeed = JSON.stringify({
      ...base,
      request: {
        ...base.request,
        brief: { ...base.request.brief, target_slide_count: 12 },
      },
      result: {
        ...base.result,
        sufficiency: { status: 'short', contentSlides: 3, targetSlides: 12 },
        contentFill: {
          capacity: 3,
          requested: 12,
          targetSlides: 12,
          factsByOrigin: { user: 2, web: 4, model: 0 },
          webSources: [
            {
              id: 'src-dns-1',
              title: 'DNS: итоги 2025 года и сеть магазинов',
              url: 'https://www.dns-shop.ru/about/',
              site: 'dns-shop.ru',
              accessedAt: null,
              facts: 3,
            },
          ],
          shortageReason: null,
        },
      },
    })
    await page.addInitScript(
      ([key, value]) => window.localStorage.setItem(key as string, value as string),
      [`deckdna.plan-request.v1:${PROJECT_ID}`, thinSeed],
    )
    await openApp(page, `/projects/${PROJECT_ID}/plan`)
    await expect(page.getByRole('heading', { level: 1, name: 'План презентации' })).toBeVisible({ timeout: 30_000 })

    await expect(page.getByText('Материалов хватит примерно на 3 слайда из 12.')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Добавить файлы' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Ответить на вопросы' })).toBeVisible()
    const buildThreeBtn = page.getByRole('button', { name: 'Собрать 3 слайда' })
    await expect(buildThreeBtn).toBeVisible()

    const replanned = page.waitForResponse(
      (response) => response.url().includes(`/projects/${PROJECT_ID}/plans`) && response.request().method() === 'POST',
    )
    await buildThreeBtn.click()
    const res = await replanned
    expect(res.status()).toBe(200)
  })

  test('splits a slide from the editor', async ({ page }) => {
    await openPlan(page)

    const before = await page.getByRole('list', { name: 'Слайды' }).getByRole('button', { name: /^Подробнее о слайде \d+$/ }).count()
    await slideTile(page, 2).click()
    const editor = page.getByRole('complementary', { name: 'Слайд 2' })
    const acted = page.waitForResponse((response) => response.url().includes('/slides/slide-1/actions'))
    await editor.getByRole('button', { name: 'Разбить на два' }).click()
    await acted
    await expect(page.getByRole('list', { name: 'Слайды' }).getByRole('button', { name: /^Подробнее о слайде \d+$/ })).toHaveCount(before + 1)
  })

  test('searches materials for numbers and displays the outcome message', async ({ page }) => {
    await openPlan(page)

    await slideTile(page, 2).click()
    const editor = page.getByRole('complementary', { name: 'Слайд 2' })
    await editor.getByRole('radiogroup', { name: 'Что показать на слайде' }).getByRole('radio', { name: 'График' }).click()
    const findNumbers = editor.getByRole('button', { name: 'Найти цифры' })
    await expect(findNumbers).toBeVisible()
    await findNumbers.click()
    await expect(editor.getByRole('status')).toContainText(/Нашёл и добавил:|Новых цифр в материалах не нашлось/)
  })

  test('flags an off-topic slide and keeps it on request', async ({ page }) => {
    await openPlan(page)

    await expect(page.getByText(/уводят от главной мысли/)).toBeVisible()
    await slideTile(page, 7).click()
    const editor = page.getByRole('complementary', { name: 'Слайд 7' })
    await expect(editor).toContainText('Этот слайд уводит от главной мысли.')
    await editor.getByRole('button', { name: 'Оставить' }).click()
    await expect(editor.getByText('Этот слайд уводит от главной мысли.')).toBeHidden()
  })

  test('leads to the style step and offers the autopilot escape', async ({ page }) => {
    await openPlan(page)

    await expect(page.getByRole('button', { name: 'Сделай сам' })).toBeEnabled()
    await Promise.all([
      page.waitForURL(new RegExp(`/projects/${PROJECT_ID}/style`)),
      page.getByRole('button', { name: 'Выбрать стиль' }).click(),
    ])

    await page.goto(`/projects/${PROJECT_ID}/plan`)
    await expect(page.getByRole('heading', { level: 1, name: 'План презентации' })).toBeVisible()
    await page.getByRole('button', { name: 'Сделай сам' }).click()
    await expect(page).toHaveURL(new RegExp(`/projects/${PROJECT_ID}/runs/run_`), { timeout: 20_000 })
  })
})


test('plan chat rereads the server plan and replaces the draft after an edit', async ({ page }) => {
  await openPlan(page)
  const chat = page.getByRole('region', { name: 'Помощник', exact: true })
  const input = chat.getByRole('textbox', { name: 'Сообщение помощнику' })
  await expect(input).toBeEnabled()
  await input.fill('Сделай первый слайд короче')
  await input.press('Enter')
  await expect(chat.getByText('Обновил план.', { exact: true })).toBeVisible()
  await expect(page.getByRole('list', { name: 'Слайды' })).toContainText('Главное — рост и результат')
})

test('plan gives the assistant a full height column and expands the workspace without losing the chat draft', async ({ page }, testInfo) => {
  await openPlan(page)
  const chat = page.getByRole('region', { name: 'Помощник', exact: true })
  const input = chat.getByRole('textbox', { name: 'Сообщение помощнику' })
  await expect(input).toBeEnabled()
  for (const width of [1280, 1440, 1920]) {
    await page.setViewportSize({ width, height: 900 })
    await expect(input).toBeInViewport()
    const chatBox = await page.getByRole('complementary', { name: 'Панель помощника' }).boundingBox()
    const planBox = await page.getByRole('region', { name: 'План презентации', exact: true }).filter({ has: page.getByRole('list', { name: 'Слайды', exact: true }) }).boundingBox()
    const titleBox = await page.getByRole('heading', { name: 'План презентации', level: 1 }).boundingBox()
    expect(chatBox && planBox && titleBox && titleBox.x < planBox.x && planBox.x + planBox.width <= chatBox.x).toBeTruthy()
    expect(chatBox?.height).toBeGreaterThan(650)
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(width)
    if (testInfo.project.name === 'chromium') await page.screenshot({ path: testInfo.outputPath(`plan-${width}.png`) })
  }
  await input.fill('Сократи вводную часть')
  const canvas = page.getByRole('region', { name: 'План презентации', exact: true }).filter({ has: page.getByRole('list', { name: 'Слайды', exact: true }) })
  const before = await canvas.boundingBox()
  await page.getByRole('button', { name: 'Свернуть помощника' }).click()
  await expect(input).toBeHidden()
  const after = await canvas.boundingBox()
  expect(after && before && after.width - before.width > 250).toBeTruthy()
  await expect(page.getByRole('button', { name: 'Выбрать стиль', exact: true })).toBeInViewport()
  await page.getByRole('button', { name: 'Открыть помощника' }).click()
  await expect(input).toHaveValue('Сократи вводную часть')
})

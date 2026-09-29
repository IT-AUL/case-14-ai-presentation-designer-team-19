import { expect, test } from '@playwright/test'
import type { Locator, Page } from '@playwright/test'
import { SEEDED_PROJECT, downloadFrom, expectRenderedPreview, openApp } from './support'

async function canvasSize(preview: Locator): Promise<{ width: number; height: number }> {
  return preview.evaluate((canvas: HTMLCanvasElement) => ({ width: canvas.width, height: canvas.height }))
}

function auditSummary(page: Page): Locator {
  return page.getByRole('region', { name: 'Сводка аудита' })
}

test('demo path: template DNA, brief, three variants, audit repair, export', async ({ page }) => {
  test.setTimeout(120_000)
  await openApp(page)

  await test.step('projects list shows the seeded project', async () => {
    await expect(page.getByRole('heading', { level: 1, name: 'Проекты' })).toBeVisible()
    await page.getByRole('list', { name: 'Проекты' }).getByRole('link', { name: new RegExp(SEEDED_PROJECT) }).click()
    await expect(page.getByRole('navigation', { name: 'Шаги проекта' })).toBeVisible()
  })

  const steps = page.getByRole('navigation', { name: 'Шаги проекта' })

  await test.step('template step shows the design DNA', async () => {
    await steps.getByRole('link', { name: /Шаблон/ }).click()
    await expect(page.getByRole('heading', { level: 1, name: 'ДНК шаблона' })).toBeVisible()
    const palette = page.getByRole('region', { name: 'Палитра' })
    await expect(palette.getByRole('list', { name: 'Цвета темы' }).getByRole('listitem')).not.toHaveCount(0)
    const fonts = page.getByRole('region', { name: 'Шрифты' })
    await expect(fonts).toContainText('заявлено ≠ фактически')
    await expect(fonts.getByRole('list', { name: 'Шрифты на слайдах' }).getByRole('listitem')).not.toHaveCount(0)
    const dna = await downloadFrom(page, page.getByRole('link', { name: 'ДНК в JSON' }))
    expect(dna.name).toMatch(/^design-dna-.+\.json$/)
    const parsed: unknown = JSON.parse(dna.bytes.toString('utf8'))
    expect(parsed).toEqual(expect.objectContaining({ template_id: expect.any(String) }))
  })

  await test.step('brief: purpose «Продукт» and start generation', async () => {
    await page.getByRole('button', { name: /Дальше: бриф/ }).click()
    await expect(page.getByRole('heading', { level: 1, name: 'О чём презентация' })).toBeVisible()
    const product = page.getByRole('radiogroup', { name: 'Назначение' }).getByRole('radio', { name: /^Продукт/ })
    await product.check()
    await expect(product).toBeChecked()
    await page.getByRole('button', { name: /Собрать 3 варианта/ }).click()
  })

  await test.step('generation page shows the deck being built and finishes with three variants', async () => {
    const tabs = page.getByRole('group', { name: 'Варианты колоды' })
    await expect(tabs).toBeVisible()
    await expect(page.getByRole('region', { name: /^Сборка варианта/ })).toBeVisible()
    await expect(page.getByRole('timer')).toHaveCount(0)
    await expect(page.getByRole('heading', { level: 1, name: 'Три варианта готовы' })).toBeVisible({ timeout: 30_000 })
    for (const name of ['Близко к шаблону', 'Сбалансированный', 'Визуальный']) {
      await expect(page.getByRole('article', { name, exact: true }).getByRole('link', { name, exact: true })).toBeVisible()
    }
  })

  await test.step('variants page renders PDF thumbnails', async () => {
    await page.getByRole('link', { name: /Сравнить по слайдам/ }).click()
    await expect(page.getByRole('heading', { level: 1, name: 'Один контент, один шаблон, три стратегии' })).toBeVisible()
    for (const strategy of ['Близко к шаблону', 'Сбалансированный', 'Визуальный']) {
      const slides = page.getByRole('list', { name: `Слайды варианта «${strategy}»` })
      const first = slides.getByRole('img').first()
      await expectRenderedPreview(first)
      const size = await canvasSize(first)
      expect(size.width).toBeGreaterThan(0)
      expect(size.height).toBeGreaterThan(0)
    }
  })

  await test.step('audit: fix two auto-fixable issues and see them in the journal', async () => {
    await page.getByRole('region', { name: 'Сбалансированный' }).getByRole('link', { name: 'Открыть и проверить' }).click()
    await expect(auditSummary(page)).toBeVisible()
    const panel = page.getByRole('complementary', { name: 'Проблемы' })
    await expect(panel.getByText('108 находок')).toBeVisible()
    const overflow = panel.getByRole('region', { name: 'Текст не помещается в рамку' })
    await overflow.getByRole('button', { name: /Текст не помещается в рамку/, expanded: false }).click()
    const selectable = overflow.getByRole('checkbox', { name: /^Выбрать для исправления/ })
    await expect(selectable.first()).toBeVisible()
    await selectable.nth(0).check()
    await selectable.nth(1).check()
    const repair = page.getByRole('button', { name: 'Исправить выбранное (2)' })
    await expect(repair).toBeEnabled()
    await repair.click()
    const journalTab = panel.getByRole('tab', { name: /^Журнал · [1-9]/ })
    await expect(journalTab).toBeVisible({ timeout: 20_000 })
    await journalTab.click()
    await expect(panel.getByRole('list', { name: 'Ревизии' })).toContainText('исправлено 2')
    const entries = panel.getByRole('list', { name: 'Журнал изменений' }).getByRole('listitem')
    await expect(entries).toHaveCount(2)
    await expect(entries.locator('[data-outcome="fixed"]')).toHaveCount(2)
  })

  await test.step('export: PPTX downloads, open errors are flagged, HTML is shown as unavailable, the passport unfolds', async () => {
    await auditSummary(page).getByRole('link', { name: /Экспорт/ }).click()
    await expect(page.getByRole('heading', { level: 1, name: 'Заберите колоду' })).toBeVisible()
    const side = page.getByRole('complementary', { name: 'Скачать колоду' })
    const downloadLink = side.getByRole('link', { name: 'Скачать PPTX' })
    await expect(downloadLink).toBeVisible({ timeout: 20_000 })
    const deck = await downloadFrom(page, downloadLink)
    expect(deck.name).toMatch(/\.pptx$/)
    expect(deck.bytes.subarray(0, 2).toString('latin1')).toBe('PK')
    expect(deck.bytes.length).toBeGreaterThan(1024)
    await expect(side.getByRole('button', { name: 'HTML' })).toBeDisabled()
    const checks = side.getByRole('list', { name: 'Проверки файла' })
    await expect(checks.getByRole('listitem').filter({ hasText: /^Открыт/ })).toBeVisible()
    await expect(checks.getByRole('link', { name: 'исправить в аудите' })).toBeVisible()
    await page.getByText('Паспорт качества', { exact: true }).first().click()
    await expect(page.getByRole('region', { name: 'Контрольные суммы файлов' })).toBeVisible()
  })
})

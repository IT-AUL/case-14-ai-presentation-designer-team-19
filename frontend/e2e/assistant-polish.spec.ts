import { expect, test } from '@playwright/test'
import { downloadFrom, openApp, SEEDED_PROJECT } from './support'

test('polish step: studio layout, slide rework, audit repair, style switch with slide preserved, passport and downloads', async ({ page }) => {
  test.setTimeout(120_000)
  await openApp(page)

  await test.step('a finished project opens straight on the polish step with calm summary and Slide tab first', async () => {
    await page.getByRole('list', { name: 'Проекты', exact: true }).getByRole('link', { name: new RegExp(SEEDED_PROJECT) }).click()
    await expect(page).toHaveURL(/\/polish\//, { timeout: 20_000 })
    const summary = page.getByRole('region', { name: 'Сводка аудита' })
    await expect(summary).toBeVisible({ timeout: 20_000 })
    await expect(summary.getByRole('status', { name: 'Итог проверки' })).toContainText(/в стиле шаблона\s*100%\s*·\s*блокеров\s*0/i)
    await expect(page.getByRole('combobox', { name: 'Вариант · ревизия' })).toHaveValue(/var_/)
    const panel = page.getByRole('complementary', { name: 'Проблемы' })
    await expect(panel.getByRole('tab', { name: 'Слайд' })).toHaveAttribute('aria-selected', 'true')
  })

  await test.step('the default slide tab reworks only the current slide and explains it without skeleton flash', async () => {
    const panel = page.getByRole('complementary', { name: 'Проблемы' })
    await panel.getByText('О слайде · быстрые правки', { exact: true }).click()
    await expect(panel.getByRole('heading', { name: /^Переделать слайд \d+$/ })).toBeVisible()
    await panel.getByText('Почему так и заметки докладчика', { exact: true }).click()
    await expect(panel.getByRole('heading', { name: 'Почему так' })).toBeVisible()
    await expect(panel.getByRole('heading', { name: 'Заметки докладчика' })).toBeVisible()
    await panel.getByRole('button', { name: 'Короче', exact: true }).click()
    await expect(panel.getByText('Сделал слайд короче.')).toBeVisible({ timeout: 20_000 })
  })

  await test.step('two auto-fixable issues in the second tab are fixed and land in the journal', async () => {
    const panel = page.getByRole('complementary', { name: 'Проблемы' })
    await panel.getByRole('tab', { name: /^Проблемы · \d+/ }).click()
    await expect(panel.getByText('108 находок')).toBeVisible()
    await expect(panel.getByRole('button', { name: /Ещё фильтры/ })).toBeVisible()
    const overflow = panel.getByRole('region', { name: 'Текст не помещается в рамку' })
    await overflow.getByRole('button', { name: /Текст не помещается в рамку/, expanded: false }).click()
    const selectable = overflow.getByRole('checkbox', { name: /^Выбрать для исправления/ })
    await selectable.nth(0).check()
    await selectable.nth(1).check()
    await page.getByRole('button', { name: 'Исправить выбранное (2)' }).click()
    const journalTab = panel.getByRole('tab', { name: /^Журнал · [1-9]/ })
    await expect(journalTab).toBeVisible({ timeout: 20_000 })
    await journalTab.click()
    await expect(panel.getByRole('list', { name: 'Ревизии' })).toContainText('исправлено 2')
  })

  await test.step('switching the style preserves the selected slide number in URL', async () => {
    await page.getByRole('button', { name: /Слайд 3/ }).first().click()
    await expect(page).toHaveURL(/\?slide=3/)
    await expect(page.getByText('03 / 12', { exact: true })).toBeVisible()
    const select = page.getByRole('combobox', { name: 'Вариант · ревизия' })
    const current = await select.inputValue()
    const other = await select
      .locator('option')
      .evaluateAll((options, value) => options.map((option) => (option as HTMLOptionElement).value).find((id) => id !== value), current)
    await select.selectOption(other ?? current)
    await expect(page).toHaveURL(new RegExp(`/polish/${other}\\?slide=3`))
  })

  await test.step('the passport opens with Russian statuses and evidence checklist, and the PPTX downloads with project-style filename', async () => {
    const downloads = page.getByRole('group', { name: 'Скачать презентацию' })
    await downloads.getByRole('button', { name: 'Паспорт качества' }).click()
    const drawer = page.getByRole('dialog', { name: 'Паспорт качества' })
    await expect(drawer).toBeVisible()
    await expect(drawer.getByRole('region', { name: 'Прохождение этапов проверки' })).toBeVisible()
    await expect(drawer.getByRole('region', { name: 'Контрольные суммы файлов' })).toBeVisible({ timeout: 20_000 })
    await expect(drawer).not.toContainText('story-director')
    await page.keyboard.press('Escape')

    const link = downloads.getByRole('link', { name: 'Скачать PPTX' })
    await expect(link).toBeVisible({ timeout: 20_000 })
    const deck = await downloadFrom(page, link)
    expect(deck.name.normalize('NFC')).toMatch(/-(точный|сбалансированный|визуальный)\.pptx$/)
    expect(deck.bytes.subarray(0, 2).toString('latin1')).toBe('PK')
  })
})

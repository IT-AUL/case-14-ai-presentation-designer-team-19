import { expect, test } from '@playwright/test'
import { openApp, SEEDED_PROJECT } from './support'

test('polish chat: streamed edit, refreshed preview, question chips, reload, undo, stop and new conversation', async ({
  page,
}, testInfo) => {
  await openApp(page)
  await page
    .getByRole('list', { name: 'Проекты', exact: true })
    .getByRole('link', { name: new RegExp(SEEDED_PROJECT) })
    .click()
  await expect(page).toHaveURL(/\/polish\//)
  const chat = page.getByRole('region', { name: 'Помощник', exact: true })
  const input = chat.getByRole('textbox', { name: 'Сообщение помощнику' })
  await expect(input).toBeEnabled()
  await input.fill('Сделай этот слайд короче')
  await input.press('Enter')
  await expect(chat.getByRole('button', { name: 'Остановить' })).toBeVisible()
  if (testInfo.project.name === 'chromium') {
    await expect(chat.getByLabel('Выполняю').or(chat.getByLabel('Выполнено'))).toBeVisible()
    await chat.screenshot({ path: testInfo.outputPath('chat-action.png') })
  }
  await expect(chat.getByText('Сохранил изменения. Посмотрите, так лучше?', { exact: true })).toBeVisible()
  await expect(chat.getByLabel('Выполнено')).toBeVisible()
  await expect(chat.getByText('Сделал слайд короче.', { exact: true })).toBeVisible()
  await expect(page.locator('img[src*="art_agent"]')).not.toHaveCount(0)
  await expect(input).toBeEnabled()
  if (testInfo.project.name === 'chromium') {
    await page.screenshot({ path: testInfo.outputPath('chat-conversation.png') })
    await page.getByRole('button', { name: 'Переключить тему' }).click()
    await page.screenshot({ path: testInfo.outputPath('chat-dark.png') })
    await page.getByRole('button', { name: 'Переключить тему' }).click()
  }
  await input.fill('Что улучшить перед питчем?')
  await input.press('Enter')
  const options = chat.getByRole('group', { name: 'Варианты ответа' })
  await expect(options).toBeVisible()
  if (testInfo.project.name === 'chromium') await chat.screenshot({ path: testInfo.outputPath('chat-question.png') })
  await page.reload()
  await expect(chat.getByText('Что важнее для этой презентации?', { exact: true })).toBeVisible()
  await options.getByRole('button', { name: 'Сделай этот слайд короче' }).click()
  await expect(input).toBeEnabled()
  await expect(chat.getByRole('button', { name: 'Отменить последнюю правку' })).toBeVisible()
  await chat.getByRole('button', { name: 'Отменить последнюю правку' }).click()
  await expect(chat.getByRole('article').filter({ hasText: 'Вернул предыдущую правку.' })).toHaveCount(1)
  await expect(input).toBeEnabled()
  await input.fill('Найди свежие цифры по рынку')
  await input.press('Enter')
  await chat.getByRole('button', { name: 'Остановить' }).click()
  await expect(chat.getByText('Остановился. Уже сделанные правки сохранил.', { exact: true })).toBeVisible()
  await expect(input).toBeEnabled()
  await chat.getByRole('button', { name: 'Новый разговор' }).click()
  await expect(chat.getByText('Обсудим презентацию?')).toBeVisible()
  await expect(chat.getByRole('article')).toHaveCount(0)
})

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { createMemoryRouter, RouterProvider } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest'
import { buildIssueViews, canAutoFix, clearOpenCounts, clusterIssueViews, readOpenCounts, type AuditIssue, type AuditRun } from '@/entities/audit'
import { variantKeys } from '@/entities/variant'
import { api } from '@/shared/api'
import { generationFixture as generation, variantFixtures as fixtures } from '@/shared/api/mocks'
import { routes } from '@/shared/config'
import { ToastProvider } from '@/shared/ui'
import { AuditPage } from './AuditPage'

vi.mock('@/shared/ui', async (importOriginal) => {
  const original = await importOriginal<Record<string, unknown>>()
  return {
    ...original,
    PdfPage: ({ pageNumber, label, children }: { pageNumber: number; label?: string; children?: ReactNode }) => (
      <div data-testid="pdf-page" data-page={pageNumber} aria-label={label}>
        {children}
      </div>
    ),
  }
})

const PROJECT_ID = generation.project_id
const RUN_ID = generation.id
const deck = fixtures.balanced
const VARIANT_ID = deck.variant.id

interface FakeInit {
  params?: { path?: Record<string, string> }
  body?: { selected_issue_ids?: string[]; reason?: string }
}

let activeClient: QueryClient
let slideOverride: typeof deck.slides | null
let issues: AuditIssue[]
let audit: AuditRun
let postSpy: MockInstance
let repairCreatesAudit: boolean
let auditRequestFails: boolean
let archived: Map<string, { run: AuditRun; items: AuditIssue[] }>

function ok(data: unknown) {
  return Promise.resolve({ data, error: undefined, response: new Response(null, { status: 200 }) })
}

function fail(path: string) {
  return Promise.resolve({
    data: undefined,
    error: { error: { code: 'not_found', message: `unexpected ${path}`, stage: null, retryable: false, request_id: 'req_test' } },
    response: new Response(null, { status: 404 }),
  })
}

function fakeGet(path: string, init?: FakeInit) {
  const params = init?.params?.path ?? {}
  const variant = Object.values(fixtures).find((candidate) => candidate.variant.id === params.variant_id)
  switch (path) {
    case '/api/v1/generations/{run_id}/variants':
      return ok({ items: Object.values(fixtures).map((candidate) => candidate.variant) })
    case '/api/v1/variants/{variant_id}':
      return variant ? ok(variant.variant) : fail(path)
    case '/api/v1/variants/{variant_id}/slides':
      return ok({ items: slideOverride ?? variant?.slides ?? [] })
    case '/api/v1/exports/{export_id}':
      return ok(deck.export)
    case '/api/v1/audits/{audit_id}':
      return ok(archived.get(params.audit_id ?? '')?.run ?? audit)
    case '/api/v1/audits/{audit_id}/issues':
      return ok({ items: archived.get(params.audit_id ?? '')?.items ?? issues })
    case '/api/v1/jobs/{job_id}':
      return ok({ id: 'job_repair', state: 'completed', result_ids: { applied: '1', skipped: '0', failed: '0', not_implemented: '0', unresolved: '1' } })
    default:
      return fail(path)
  }
}

function fakePost(path: string, init?: FakeInit) {
  switch (path) {
    case '/api/v1/variants/{variant_id}/audits':
      if (auditRequestFails) return fail(path)
      return ok({ audit_id: repairCreatesAudit ? deck.audit.id : audit.id, job_id: 'job_audit' })
    case '/api/v1/audits/{audit_id}/repairs': {
      const [fixedId, ...rest] = init?.body?.selected_issue_ids ?? []
      const revision = audit.deck_revision + 1
      if (repairCreatesAudit) archived.set(audit.id, { run: audit, items: issues })
      issues = issues
        .filter((issue) => issue.id !== fixedId)
        .map((issue) => ({ ...issue, id: `${issue.id}:r${revision}`, deck_revision: revision, status: rest.includes(issue.id) ? 'open' : issue.status }))
      audit = { ...audit, id: repairCreatesAudit ? `${deck.audit.id}:r${revision}` : audit.id, deck_revision: revision }
      return ok({ job_id: 'job_repair', audit_id: audit.id, deck_revision: revision })
    }
    case '/api/v1/issues/{issue_id}/dismiss': {
      const issue = issues.find((candidate) => candidate.id === init?.params?.path?.issue_id)
      return issue ? ok({ ...issue, status: 'dismissed' }) : fail(path)
    }
    default:
      return fail(path)
  }
}

function renderAudit(url: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  activeClient = queryClient
  const router = createMemoryRouter(
    [
      { path: '/projects/:projectId/runs/:runId/polish/:variantId?', element: <AuditPage /> },
      { path: '*', element: <div>other page</div> },
    ],
    { initialEntries: [url] },
  )
  render(
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <RouterProvider router={router} />
      </ToastProvider>
    </QueryClientProvider>,
  )
  return router
}

const fixableCount = deck.issues.filter(canAutoFix).length

beforeEach(() => {
  window.localStorage.clear()
  slideOverride = null
  issues = structuredClone(deck.issues)
  audit = structuredClone(deck.audit)
  repairCreatesAudit = false
  auditRequestFails = false
  archived = new Map()
  vi.spyOn(api, 'GET').mockImplementation(fakeGet as unknown as typeof api.GET)
  postSpy = vi.spyOn(api, 'POST').mockImplementation(fakePost as unknown as typeof api.POST) as unknown as MockInstance
})

afterEach(() => {
  clearOpenCounts()
  vi.restoreAllMocks()
})

describe('AuditPage', { timeout: 20_000 }, () => {
  it('opens the default strategy variant and the slide from the query with Slide tab first', async () => {
    const router = renderAudit(`${routes.polish(PROJECT_ID, RUN_ID)}?slide=3`)
    await waitFor(() => expect(router.state.location.pathname).toBe(routes.polish(PROJECT_ID, RUN_ID, VARIANT_ID)))
    expect(router.state.location.search).toBe('?slide=3')
    expect(await screen.findByText('03 / 12')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Слайд' })).toHaveAttribute('aria-selected', 'true')
    const summaryStatus = screen.getByRole('status', { name: 'Итог проверки' })
    expect(summaryStatus).toHaveTextContent(/в стиле шаблона\s*100%\s*·\s*блокеров\s*0/i)
  })

  it('preserves the current slide query parameter when switching variants', async () => {
    const user = userEvent.setup()
    const router = renderAudit(`${routes.polish(PROJECT_ID, RUN_ID, VARIANT_ID)}?slide=4`)

    expect(await screen.findByText('04 / 12')).toBeInTheDocument()
    const select = screen.getByLabelText('Вариант · ревизия')
    await user.selectOptions(select, fixtures.faithful.variant.id)

    await waitFor(() => expect(router.state.location.pathname).toBe(routes.polish(PROJECT_ID, RUN_ID, fixtures.faithful.variant.id)))
    expect(router.state.location.search).toBe('?slide=4')
  })

  it('groups findings by rule with collapsed groups and a summary line', async () => {
    const user = userEvent.setup()
    renderAudit(routes.polish(PROJECT_ID, RUN_ID, VARIANT_ID))

    const issuesTab = await screen.findByRole('tab', { name: 'Проблемы · 108' })
    await user.click(issuesTab)
    expect(issuesTab).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('108 находок')).toBeInTheDocument()
    expect(screen.getByText('· 6 правил')).toBeInTheDocument()
    expect(screen.getByText(`· ${fixableCount} исправимо`)).toBeInTheDocument()
    expect(screen.queryAllByRole('article')).toHaveLength(0)

    const groups = within(screen.getByRole('complementary', { name: 'Проблемы' })).getAllByRole('region')
    expect(groups.map((group) => group.getAttribute('aria-label'))).toEqual([
      'Текст не помещается в рамку',
      'Контраст текста ниже 4.5:1',
      'Картинка растянута',
      'Слишком мелкий кегль',
      'Кегль не из шкалы шаблона',
      'Цвет не из палитры шаблона',
    ])
    const contrast = screen.getByRole('region', { name: 'Контраст текста ниже 4.5:1' })
    await user.click(within(contrast).getByRole('button', { expanded: false }))
    const cards = within(contrast).getAllByRole('article')
    expect(cards.length).toBeLessThan(18)
    expect(within(contrast).getAllByText(/^×\d+$/).length).toBeGreaterThan(1)
    expect(within(cards[0] as HTMLElement).getByText('Контраст 3.23:1, норма ≥ 4.5:1')).toBeInTheDocument()

    await user.click(within(contrast).getByRole('button', { expanded: true }))
    expect(within(contrast).queryAllByRole('article')).toHaveLength(0)
  })

  it('shows deck-level findings without slide_index and strips English All clear strings', async () => {
    const user = userEvent.setup()
    issues = []
    audit = {
      ...audit,
      issue_count: 1,
      summary_by_type: { 'narrative.ActionTitles': 2 },
      deck_level_findings: [
        {
          id: 'deck_finding_1',
          rule_code: 'deck.consistency',
          severity: 'warning',
          message: 'All clear',
        },
      ],
    } as unknown as AuditRun

    renderAudit(routes.polish(PROJECT_ID, RUN_ID, VARIANT_ID))

    const issuesTab = await screen.findByRole('tab', { name: 'Проблемы · 2' })
    await user.click(issuesTab)

    expect(screen.queryByText(/All clear/i)).not.toBeInTheDocument()
    const actionTitlesGroup = screen.getByRole('region', { name: 'Заголовки-выводы на слайдах' })
    await user.click(within(actionTitlesGroup).getByRole('button', { expanded: false }))
    expect(within(actionTitlesGroup).getByText('Вся колода')).toBeInTheDocument()
  })

  it('selects every auto-fixable issue of a rule from the group checkbox', async () => {
    const user = userEvent.setup()
    renderAudit(routes.polish(PROJECT_ID, RUN_ID, VARIANT_ID))

    await user.click(await screen.findByRole('tab', { name: 'Проблемы · 108' }))
    const contrastCount = deck.issues.filter((issue) => issue.rule_code === 'accessibility.contrast').length
    const groupCheck = await screen.findByRole('checkbox', { name: `Выбрать все исправимые в группе «Контраст текста ниже 4.5:1» (${contrastCount})` })
    await user.click(groupCheck)
    expect(groupCheck).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('button', { name: `Исправить выбранное (${contrastCount})` })).toBeEnabled()

    const contrast = screen.getByRole('region', { name: 'Контраст текста ниже 4.5:1' })
    await user.click(within(contrast).getByRole('button', { expanded: false }))
    const [firstCard] = within(contrast).getAllByRole('checkbox', { name: /^Выбрать для исправления/ })
    await user.click(firstCard as HTMLElement)
    expect(groupCheck).toHaveAttribute('aria-checked', 'mixed')

    expect(screen.queryByRole('checkbox', { name: /Выбрать все исправимые в группе «Кегль не из шкалы шаблона»/ })).not.toBeInTheDocument()
    const fontScale = screen.getByRole('region', { name: 'Кегль не из шкалы шаблона' })
    await user.click(within(fontScale).getByRole('button', { expanded: false }))
    const manual = within(fontScale).getAllByRole('checkbox', { name: /^Только вручную/ })
    expect(manual[0]).toHaveAttribute('aria-disabled', 'true')
    expect(manual[0]).toHaveAttribute('title', 'Только вручную')
    await user.click(manual[0] as HTMLElement)
    expect(manual[0]).toHaveAttribute('aria-checked', 'false')
  })

  it('keeps the D/N split, collapsed extra filters and alternative groupings', async () => {
    const user = userEvent.setup()
    renderAudit(routes.polish(PROJECT_ID, RUN_ID, VARIANT_ID))

    await user.click(await screen.findByRole('tab', { name: 'Проблемы · 108' }))
    const checks = screen.getByRole('group', { name: 'Типы проверок' })
    expect(within(checks).getByText('не запускалась')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Ещё фильтры/ }))
    await user.click(screen.getByRole('radio', { name: 'N · смысл 0' }))
    expect(screen.getByText('По этим фильтрам проблем нет.')).toBeInTheDocument()
    await user.click(screen.getByRole('radio', { name: 'Все 108' }))

    await user.click(screen.getByRole('radio', { name: 'По слайдам' }))
    const clusters = clusterIssueViews(buildIssueViews(deck.issues, []))
    const cards = screen.getAllByRole('article')
    expect(cards).toHaveLength(clusters.length)
    expect(cards.length).toBeLessThan(108)
    expect(screen.getAllByRole('checkbox', { name: /^Выбрать для исправления/ })).toHaveLength(clusters.filter((cluster) => cluster.primary.selectable).length)
    expect(screen.getAllByRole('checkbox', { name: /^Только вручную/ })).toHaveLength(clusters.filter((cluster) => !cluster.primary.selectable).length)

    await user.click(screen.getByRole('checkbox', { name: 'только исправимые' }))
    expect(screen.queryAllByRole('checkbox', { name: /^Только вручную/ })).toHaveLength(0)

    await user.click(screen.getByRole('radio', { name: 'По категориям' }))
    expect(screen.getByRole('region', { name: 'Текст и читаемость' })).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: 'Шаблон и бренд' })).queryByText('Кегль не из шкалы шаблона')).not.toBeInTheDocument()
  })

  it('does not hijack arrow keys from a focused radio button', async () => {
    const user = userEvent.setup()
    const router = renderAudit(`${routes.polish(PROJECT_ID, RUN_ID, VARIANT_ID)}?slide=2`)

    expect(await screen.findByText('02 / 12')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Проблемы · 108' }))
    const bySlide = screen.getByRole('radio', { name: 'По слайдам' })
    bySlide.focus()
    await user.keyboard('{ArrowRight}')
    expect(screen.getByText('02 / 12')).toBeInTheDocument()
    expect(router.state.location.search).toBe('?slide=2')

    await user.keyboard('j')
    const opened = (await screen.findAllByRole('article')).filter((card) => within(card).queryByRole('button', { expanded: true }))
    expect(opened).toHaveLength(1)

    ;(document.activeElement as HTMLElement | null)?.blur()
    await user.keyboard('{ArrowRight}')
    await waitFor(() => expect(router.state.location.search).toBe('?slide=3'))
  })

  it('repairs selected issues and builds the journal by signature', async () => {
    const user = userEvent.setup()
    renderAudit(routes.polish(PROJECT_ID, RUN_ID, VARIANT_ID))

    await user.click(await screen.findByRole('tab', { name: 'Проблемы · 108' }))
    const overflow = await screen.findByRole('region', { name: 'Текст не помещается в рамку' })
    await user.click(within(overflow).getByRole('button', { expanded: false }))
    const [first, second] = within(overflow).getAllByRole('checkbox', { name: /^Выбрать для исправления/ })
    await user.click(first as HTMLElement)
    await user.click(second as HTMLElement)
    await user.click(screen.getByRole('button', { name: 'Исправить выбранное (2)' }))

    expect(await screen.findByText('Ревизия r2: исправлено 1, не удалось 1')).toBeInTheDocument()
    expect(postSpy).toHaveBeenCalledWith('/api/v1/audits/{audit_id}/repairs', expect.objectContaining({ body: expect.objectContaining({ max_iterations: 2 }) }))
    expect(screen.getByRole('tab', { name: 'Журнал · 2' })).toHaveAttribute('aria-selected', 'true')
    const journal = screen.getByRole('list', { name: 'Журнал изменений' })
    expect(within(journal).getAllByRole('listitem')).toHaveLength(2)
    expect(within(journal).getByText('Не удалось исправить: проблема осталась в новой ревизии')).toBeInTheDocument()
    const banner = screen.getByText('Повторный аудит: 0 блокеров').closest('[role="status"]') as HTMLElement
    expect(within(banner).getByText(/ошибок 54 → 53 · исправлено 1/)).toBeInTheDocument()
    expect(within(banner).getByText(/не удалось 1/)).toBeInTheDocument()
    expect(within(banner).getByText(/Можно скачивать/)).toBeInTheDocument()
    expect(JSON.parse(window.localStorage.getItem('deckdna.evidence.v1') ?? '{}')[PROJECT_ID]).toMatchObject({ repaired: true, reaudited: true })
    expect(readOpenCounts(PROJECT_ID, RUN_ID)).toEqual({ blocker: 0, error: 53 })

    await user.click(screen.getByRole('tab', { name: /Проблемы/ }))
    await user.click(screen.getByRole('button', { name: /Ещё фильтры/ }))
    await user.click(screen.getByRole('radio', { name: 'Решённые' }))
    expect(screen.getByRole('button', { name: /Текст не помещается в рамку/, expanded: true })).toBeInTheDocument()
    expect(screen.getAllByRole('article')).toHaveLength(1)
    expect(screen.getByText('Исправлена')).toBeInTheDocument()
  })

  it('follows the new audit returned by repair even if the variant still points at the old one', async () => {
    repairCreatesAudit = true
    const user = userEvent.setup()
    renderAudit(routes.polish(PROJECT_ID, RUN_ID, fixtures.faithful.variant.id))

    await user.click(await screen.findByRole('tab', { name: 'Проблемы · 108' }))
    const overflow = await screen.findByRole('region', { name: 'Текст не помещается в рамку' })
    await user.click(within(overflow).getByRole('button', { expanded: false }))
    const [first, second] = within(overflow).getAllByRole('checkbox', { name: /^Выбрать для исправления/ })
    await user.click(first as HTMLElement)
    await user.click(second as HTMLElement)
    await user.click(screen.getByRole('button', { name: 'Исправить выбранное (2)' }))

    expect(await screen.findByText('Ревизия r2: исправлено 1, не удалось 1')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Журнал · 2' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: `Проблемы · ${deck.issues.length - 1}` })).toBeInTheDocument()
  })

  it('reports a failed re-audit instead of showing stale results', async () => {
    const user = userEvent.setup()
    renderAudit(routes.polish(PROJECT_ID, RUN_ID, VARIANT_ID))

    await screen.findByRole('tab', { name: 'Проблемы · 108' })
    auditRequestFails = true
    await user.click(screen.getByRole('button', { name: 'Повторный аудит' }))

    expect(await screen.findByText(/Повторный аудит не удался/)).toBeInTheDocument()
    expect(screen.queryByText(/Повторный аудит: \d+ блокер/)).not.toBeInTheDocument()
  })

  it('requires a reason to dismiss an issue', async () => {
    const user = userEvent.setup()
    renderAudit(routes.polish(PROJECT_ID, RUN_ID, VARIANT_ID))

    await user.click(await screen.findByRole('tab', { name: 'Проблемы · 108' }))
    const fontScale = await screen.findByRole('region', { name: 'Кегль не из шкалы шаблона' })
    await user.click(within(fontScale).getByRole('button', { expanded: false }))
    const [card] = within(fontScale).getAllByRole('article')
    await user.click(within(card as HTMLElement).getByRole('button', { expanded: false }))
    await user.click(within(card as HTMLElement).getByRole('button', { name: 'Отклонить…' }))
    const submit = within(card as HTMLElement).getByRole('button', { name: 'Отклонить' })
    expect(submit).toBeDisabled()

    await user.click(within(card as HTMLElement).getByRole('button', { name: 'Ложное срабатывание' }))
    await user.click(submit)

    await waitFor(() =>
      expect(postSpy).toHaveBeenCalledWith('/api/v1/issues/{issue_id}/dismiss', expect.objectContaining({ body: { reason: 'Ложное срабатывание' } })),
    )
    expect(await screen.findByRole('tab', { name: 'Журнал · 1' })).toBeInTheDocument()
  })

  it('explains next to a disabled button that contextual checks need a check service', async () => {
    renderAudit(routes.polish(PROJECT_ID, RUN_ID, VARIANT_ID))

    const button = await screen.findByRole('button', { name: /Проверить смысл/ })
    expect(button).toBeDisabled()
    expect(button).toHaveAccessibleDescription('нужен сервис проверки')
    expect(screen.getByText('нужен сервис проверки')).toBeVisible()
    expect(postSpy).not.toHaveBeenCalledWith('/api/v1/variants/{variant_id}/audits', expect.objectContaining({ body: expect.objectContaining({ provider_session_id: expect.any(String) }) }))
  })

  it('marks the preview as stale when the picture is older than the issues', async () => {
    const user = userEvent.setup()
    renderAudit(routes.polish(PROJECT_ID, RUN_ID, VARIANT_ID))

    await user.click(await screen.findByRole('tab', { name: 'Проблемы · 108' }))
    const overflow = await screen.findByRole('region', { name: 'Текст не помещается в рамку' })
    await user.click(within(overflow).getByRole('button', { expanded: false }))
    const [first] = within(overflow).getAllByRole('checkbox', { name: /^Выбрать для исправления/ })
    await user.click(first as HTMLElement)
    await user.click(screen.getByRole('button', { name: 'Исправить выбранное (1)' }))
    await screen.findByText('Ревизия r2: исправлено 1')
    expect(await screen.findByText('Превью устарело: картинка r1, проблемы r2')).toBeInTheDocument()
  })
})

describe('AuditPage slide tab', { timeout: 20_000 }, () => {
  it('offers to rework only the current slide and downloads in one place by default', async () => {
    renderAudit(`${routes.polish(PROJECT_ID, RUN_ID, VARIANT_ID)}?slide=2`)
    expect(await screen.findByRole('group', { name: 'Скачать презентацию' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Слайд' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('button', { name: 'Короче' })).not.toBeVisible()
    await userEvent.click(screen.getByText('О слайде · быстрые правки', { exact: true }))
    expect(screen.getByRole('heading', { name: 'Переделать слайд 2' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Короче' })).toBeInTheDocument()
    await userEvent.click(screen.getByText('Почему так и заметки докладчика', { exact: true }))
    expect(screen.getByRole('heading', { name: 'Почему так' })).toBeInTheDocument()
  })

  it('queues a slide edit, blocks downloads while pending, and cancels the queued change', async () => {
    const user = userEvent.setup()
    let queuedItems: Record<string, unknown>[] = []

    vi.spyOn(api, 'GET').mockImplementation(((path: string, init?: FakeInit) => {
      if (path === '/api/v1/variants/{variant_id}/changes') {
        return ok({
          variant_id: VARIANT_ID,
          applied_revision: 1,
          accepted_revision: queuedItems.some((item) => item.state === 'queued') ? 2 : 1,
          items: queuedItems,
        })
      }
      return fakeGet(path, init)
    }) as unknown as typeof api.GET)

    postSpy.mockImplementation(((path: string, init?: FakeInit) => {
      if (path === '/api/v1/variants/{variant_id}/changes') {
        const body = init?.body as { slide_id?: string; expected_revision?: number; action?: string } | undefined
        const created = {
          change_id: 'chg_slide_2',
          variant_id: VARIANT_ID,
          slide_id: body?.slide_id ?? 'slide-1',
          action: body?.action ?? 'shorter',
          state: 'queued',
          base_revision: body?.expected_revision ?? 1,
          target_revision: 2,
        }
        queuedItems = [created]
        return ok(created)
      }
      if (path === '/api/v1/variants/{variant_id}/changes/{change_id}/cancel') {
        const cancelled = {
          ...(queuedItems[0] ?? {}),
          change_id: 'chg_slide_2',
          state: 'canceled',
        }
        queuedItems = [cancelled]
        return ok(cancelled)
      }
      return fakePost(path, init)
    }) as unknown as typeof api.POST)

    renderAudit(`${routes.polish(PROJECT_ID, RUN_ID, VARIANT_ID)}?slide=2`)

    const downloads = await screen.findByRole('group', { name: 'Скачать презентацию' })
    expect(within(downloads).getByRole('link', { name: 'Скачать PPTX' })).toBeInTheDocument()

    await user.click(screen.getByText('О слайде · быстрые правки', { exact: true }))
    await user.click(screen.getByRole('button', { name: 'Короче' }))

    expect(await screen.findByText('Правка в очереди')).toBeInTheDocument()
    const cancelBtn = screen.getByRole('button', { name: 'Отменить' })
    expect(cancelBtn).toBeInTheDocument()

    expect(within(downloads).getByRole('button', { name: 'Скачать PPTX' })).toBeDisabled()
    expect(within(downloads).getByText('Дождитесь завершения правок')).toBeInTheDocument()

    await user.click(cancelBtn)

    await waitFor(() => expect(screen.queryByText('Правка в очереди')).not.toBeInTheDocument())
    expect(within(downloads).getByRole('link', { name: 'Скачать PPTX' })).toBeInTheDocument()
    expect(within(downloads).queryByText('Дождитесь завершения правок')).not.toBeInTheDocument()
  })

  it('falls back to legacy regenerate when /changes returns 404', async () => {
    const user = userEvent.setup()

    postSpy.mockImplementation(((path: string, init?: FakeInit) => {
      if (path === '/api/v1/variants/{variant_id}/slides/{slide_id}/regenerate') {
        return ok({ assistant_note: 'Сделал слайд короче.' })
      }
      return fakePost(path, init)
    }) as unknown as typeof api.POST)

    renderAudit(`${routes.polish(PROJECT_ID, RUN_ID, VARIANT_ID)}?slide=2`)
    await screen.findByText('О слайде · быстрые правки', { exact: true })

    await user.click(screen.getByText('О слайде · быстрые правки', { exact: true }))
    await user.click(screen.getByRole('button', { name: 'Короче' }))

    expect(await screen.findByText('Готово')).toBeInTheDocument()
    expect(screen.getByText('Сделал слайд короче.')).toBeInTheDocument()
  })
  it('preserves the selected slide by id after a structural reorder', async () => {
    const router = renderAudit(`${routes.polish(PROJECT_ID, RUN_ID, VARIANT_ID)}?slide=3`)
    await screen.findByText('03 / 12')
    const moved = deck.slides[2]
    if (!moved) throw new Error('Missing slide')
    slideOverride = [moved, ...deck.slides.filter((item) => item.id !== moved.id)].map((item, index) => ({ ...item, index: index + 1 }))
    await act(async () => { await activeClient.invalidateQueries({ queryKey: variantKeys.slides(VARIANT_ID) }) })
    await waitFor(() => expect(router.state.location.search).toBe('?slide=1'))
    expect(screen.getByText('01 / 12')).toBeInTheDocument()
  })

  it('selects the nearest remaining slide and reduces the count when the selected slide disappears', async () => {
    const router = renderAudit(`${routes.polish(PROJECT_ID, RUN_ID, VARIANT_ID)}?slide=12`)
    await screen.findByText('12 / 12')
    slideOverride = deck.slides.slice(0, -1)
    await act(async () => { await activeClient.invalidateQueries({ queryKey: variantKeys.slides(VARIANT_ID) }) })
    await waitFor(() => expect(router.state.location.search).toBe('?slide=11'))
    expect(screen.getByText('11 / 11')).toBeInTheDocument()
  })

  it('keeps the requested slide while the slide list temporarily becomes empty', async () => {
    const router = renderAudit(`${routes.polish(PROJECT_ID, RUN_ID, VARIANT_ID)}?slide=3`)
    await screen.findByText('03 / 12')
    await act(async () => { activeClient.setQueryData(variantKeys.slides(VARIANT_ID), []) })
    expect(router.state.location.search).toBe('?slide=3')
    await act(async () => { activeClient.setQueryData(variantKeys.slides(VARIANT_ID), deck.slides) })
    expect(await screen.findByText('03 / 12')).toBeInTheDocument()
    expect(router.state.location.search).toBe('?slide=3')
  })

})


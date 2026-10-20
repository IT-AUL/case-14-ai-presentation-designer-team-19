import {
  applyPlanEvent,
  EMPTY_PROGRESS,
  formatFactsByOriginText,
  formatShortageExplanationText,
  isUnitLocked,
  parseContentFill,
  parseResearch,
  parseSse,
  parseSufficiency,
  unitOrigin,
  unitSourceId,
} from './planExtras'

const slide = { id: 's1', index: 0, purpose: 'custom', title_intent: 'Рынок растёт', key_message: '', evidence_ids: ['p:meta'], content_units: [], density_budget: { level: 'medium' } }

describe('plan extras', () => {
  it('ignores sufficiency with an unknown status', () => {
    expect(parseSufficiency({ status: 'plenty' })).toBeNull()
    expect(parseSufficiency({ status: 'excess', supported_slides: 18, target_slide_count: 10 })).toEqual({ status: 'excess', contentSlides: 18, targetSlides: 10 })
  })

  it('parses content_fill and detects shortage from content_fill.before even when sufficiency status became ok after shrink', () => {
    const rawFill = {
      status: 'short',
      mode: 'shrink',
      before: { requested: 12, capacity: 3, status: 'short', total_facts: 2 },
      after: { requested: 3, capacity: 3, status: 'ok', total_facts: 6 },
      facts_by_origin: { user: 2, web: 4, llm: 0 },
      web: {
        queries: ['DNS выручка'],
        sources: [{ id: 'src-dns-1', title: 'DNS: итоги 2025 года', url: 'https://www.dns-shop.ru/about/', facts: 3 }],
      },
    }
    const contentFill = parseContentFill(rawFill)
    expect(contentFill).not.toBeNull()
    expect(contentFill?.capacity).toBe(3)
    expect(contentFill?.requested).toBe(12)
    expect(contentFill?.factsByOrigin).toEqual({ user: 2, web: 4, model: 0 })
    expect(contentFill?.webSources).toHaveLength(1)
    expect(contentFill?.webSources[0]?.site).toBe('dns-shop.ru')

    const sufficiency = parseSufficiency({ status: 'ok', content_slides: 3, target_slides: 3 }, contentFill)
    expect(sufficiency).toEqual({
      status: 'short',
      contentSlides: 3,
      targetSlides: 12,
    })

    const research = parseResearch(null, rawFill)
    expect(research?.queries).toEqual(['DNS выручка'])
    expect(research?.sources).toHaveLength(1)
  })

  it('formats facts_by_origin and shortage explanation with and without additive shortage_reason', () => {
    const withoutAdditive = parseContentFill({
      status: 'short',
      before: { requested: 12, capacity: 3, status: 'short' },
      after: { requested: 3, capacity: 3, status: 'ok' },
      facts_by_origin: { user: 2, web: 4, llm: 0 },
    })
    expect(formatFactsByOriginText(withoutAdditive)).toBe('Факты: из ваших материалов — 2, из открытых источников — 4.')
    expect(formatShortageExplanationText(withoutAdditive, 3)).toBe(
      'Собрал 3 из 12 запрошенных слайдов — по числу подтверждённых фактов в материалах.',
    )

    const withAdditive = parseContentFill({
      status: 'short',
      before: { requested: 12, capacity: 3, status: 'short' },
      shortage_reason: 'Собрал 3 слайда вместо 12: в исходном запросе не было таблиц и документов.',
      facts_by_origin: { user: 2, web: 0, llm: 0 },
    })
    expect(formatFactsByOriginText(withAdditive)).toBe('Факты: из ваших материалов — 2.')
    expect(formatShortageExplanationText(withAdditive, 3)).toBe(
      'Собрал 3 слайда вместо 12: в исходном запросе не было таблиц и документов.',
    )
  })

  it('maps unit origins to user, web and model', () => {
    expect(unitOrigin({ origin: 'user' })).toBe('user')
    expect(unitOrigin({ origin: { kind: 'web', source_id: 'src-1' } })).toBe('web')
    expect(unitOrigin({ origin: 'llm' })).toBe('model')
    expect(unitOrigin({})).toBeNull()
    expect(unitSourceId({ origin: { kind: 'web', source_id: 'src-1' } })).toBe('src-1')
    expect(unitSourceId({ source_id: 'src-2' })).toBe('src-2')
    expect(isUnitLocked({ locked: true })).toBe(true)
    expect(isUnitLocked({})).toBe(false)
  })

  it('accumulates queries, sources and slides from job events', () => {
    let progress = applyPlanEvent(EMPTY_PROGRESS, 'stage.started', { stage: 'research' })
    progress = applyPlanEvent(progress, 'research.query', { query: 'рынок' })
    progress = applyPlanEvent(progress, 'research.query', { query: 'рынок' })
    progress = applyPlanEvent(progress, 'research.source', { source: { id: 'a', title: 'Отчёт', url: 'https://example.com' } })
    progress = applyPlanEvent(progress, 'plan.slide', { slide })
    progress = applyPlanEvent(progress, 'plan.slide', { slide: { ...slide, title_intent: 'Рынок растёт на 30%' } })
    expect(progress.stage).toBe('research')
    expect(progress.queries).toEqual(['рынок'])
    expect(progress.sources.map((source) => source.site)).toEqual(['example.com'])
    expect(progress.slides.map((item) => item.title_intent)).toEqual(['Рынок растёт на 30%'])
    expect(applyPlanEvent(progress, 'plan.slide', { slide: { id: 'x' } })).toBe(progress)
  })

  it('parses a server-sent event stream', () => {
    const text = 'id: 1\nevent: research.query\ndata: {"query":"a"}\n\nid: 2\nevent: job.completed\ndata: {"state":"completed"}\n\n: keepalive\n\n'
    expect(parseSse(text)).toEqual([
      { id: 1, type: 'research.query', data: { query: 'a' } },
      { id: 2, type: 'job.completed', data: { state: 'completed' } },
    ])
  })
})

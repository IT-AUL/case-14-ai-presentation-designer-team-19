import { parsePassport } from '@/entities/passport'
import { passportFixture } from '@/shared/api/mocks'
import { buildPassportView } from './passportView'

const context = { budgetSeconds: 300, planner: 'story-director/deterministic-0.1.0' }

function recorded() {
  return parsePassport(passportFixture)
}

function indexBy<T extends { key: string }>(items: readonly T[]): (key: string) => T {
  return (key) => {
    const item = items.find((candidate) => candidate.key === key)
    if (!item) throw new Error(`missing ${key}`)
    return item
  }
}

describe('buildPassportView', () => {
  it('turns recorded editability and validity into proof cells', () => {
    const view = buildPassportView(recorded(), context)
    const byKey = indexBy(view.proof)

    expect(byKey('roundTrip')).toMatchObject({ value: '✓', label: 'Файл повторно открыт', tone: 'ok' })
    expect(byKey('ooxml')).toMatchObject({ value: '0', tone: 'neutral' })
    expect(byKey('nativeText').value).toBe('48%')
    expect(byKey('rasterSlides')).toMatchObject({ value: '0', hint: 'графики и таблицы — объекты' })
    expect(byKey('pei')).toMatchObject({ value: '3/5', hint: 'шкала 0–5; 3 — правится всё, нативных таблиц и графиков нет' })
  })

  it('never invents style fidelity or usage when the backend does not compute them', () => {
    const view = buildPassportView(recorded(), context)

    expect(view.style).toEqual({ status: 'soon' })
    expect(view.usage).toBeNull()
  })

  it('shows style fidelity in Russian and omits token counters', () => {
    const raw = structuredClone(passportFixture) as unknown as { metrics: Record<string, unknown> }
    raw.metrics.style_fidelity = { palette_compliance: 0.97, font_compliance: 1, custom_score: 0.5 }
    raw.metrics.usage = { total_tokens: 48200, model_calls: 27 }

    const view = buildPassportView(parsePassport(raw), context)

    expect(view.style).toEqual({
      status: 'ready',
      scores: [
        { key: 'palette_compliance', label: 'Палитра', value: '0.97', fraction: 0.97 },
        { key: 'font_compliance', label: 'Шрифты', value: '1.00', fraction: 1 },
        { key: 'custom_score', label: 'Доп. оценка', value: '0.50', fraction: 0.5 },
      ],
    })
    expect(view.usage).toBeNull()
  })

  it('keeps supported claims without faking the unsupported count', () => {
    const view = buildPassportView(recorded(), context)

    expect(view.sources).toEqual({ supported: 193, unsupported: null, total: null, numbersVerified: null, numbersFailed: null })
    expect(view.readability).toEqual({ contrastFailures: 18, overflowCount: 23, avgOccupancy: null })
    expect(view.issues).toEqual({
      counts: [
        { severity: 'blocker', count: 0 },
        { severity: 'error', count: 54 },
        { severity: 'warning', count: 54 },
        { severity: 'info', count: 0 },
      ],
      total: 108,
      unresolved: 108,
    })
  })

  it('compares total time with the five-minute budget and hides absent stage timings', () => {
    const view = buildPassportView(recorded(), context)

    expect(view.timing).toMatchObject({ total: '2,7 с', budget: '5:00', withinBudget: true, stages: [] })
    expect(view.timing.fraction).toBeCloseTo(2.66 / 300, 3)
  })

  it('maps per-stage timings when present', () => {
    const raw = structuredClone(passportFixture) as unknown as { metrics: { timings: Record<string, unknown> } }
    raw.metrics.timings.per_stage = { planning: 38, render: 9 }

    const view = buildPassportView(parsePassport(raw), context)

    expect(view.timing.stages.map(({ label, seconds }) => [label, seconds])).toEqual([
      ['План структуры', '38 с'],
      ['Рендер', '9,0 с'],
    ])
  })

  it('translates provenance planner and modes without exposing internal package names', () => {
    const view = buildPassportView(recorded(), { ...context, serverSkillVersion: '0.2.0' })
    const rows = indexBy(view.provenance)

    expect(view.skillVersion).toBe('0.1.0')
    expect(view.pipelineVersion).toBeNull()
    expect(rows('skill')).toMatchObject({ value: '0.1.0', note: 'сейчас на сервере 0.2.0 — прогон собран предыдущей версией' })
    expect(rows('planner').value).toBe('По правилам')
    expect(rows('mode').value).toBe('Стандартный (по правилам шаблона)')
    expect(rows('config:configs/generation.default.yaml')).toMatchObject({
      label: 'Конфигурация сборки',
      value: 'sha 80f8…6056',
    })
    expect(rows('config:configs/audit.default.yaml')).toMatchObject({
      label: 'Конфигурация проверки',
      value: 'sha 686b…3694',
    })

    const dupRaw = structuredClone(passportFixture) as unknown as { provenance: { config_hashes: Record<string, string> } }
    dupRaw.provenance.config_hashes = {
      'configs/generation.default.yaml': '80f89b7a7a3b8aae4c3ac5ac571b7e926abf4841ce9afbda9337a24b92426056',
      'configs/audit.default.yaml': '80f89b7a7a3b8aae4c3ac5ac571b7e926abf4841ce9afbda9337a24b92426056',
    }
    const dupView = buildPassportView(parsePassport(dupRaw), context)
    const configRows = dupView.provenance.filter((row) => row.key.startsWith('config:'))
    expect(configRows).toEqual([
      { key: 'config:configs/generation.default.yaml', label: 'Конфигурация', value: 'sha 80f8…6056' },
    ])
  })

  it('marks extended check mode when semantic profiles are present', () => {
    const raw = structuredClone(passportFixture) as unknown as { provenance: Record<string, unknown> }
    raw.provenance.prompt_versions = { plan: '2026.09.3', rewrite: '2026.09.1' }
    raw.provenance.model_profiles = [{ role: 'text', model_id: 'Qwen3-32B' }, { role: 'vision', model_id: 'Qwen2.5-VL-32B-Instruct' }]

    const view = buildPassportView(parsePassport(raw), { ...context, serverSkillVersion: '0.1.0' })
    const rows = indexBy(view.provenance)

    expect(rows('skill').note).toBeUndefined()
    expect(rows('mode').value).toBe('Расширенный (правила и смысл)')
  })

  it('localizes technical disclosures instead of printing raw english text', () => {
    const raw = structuredClone(passportFixture) as unknown as { fallbacks: unknown[] }
    raw.fallbacks = [
      {
        feature: 'metrics.content_support.numbers_verified',
        disclosure: 'numbers_verified/numbers_failed count only whether a numeric value appears anywhere in the source text',
      },
    ]

    const view = buildPassportView(parsePassport(raw), context)

    expect(view.fallbacks[0]?.label).toBe('Сверка чисел с источником')
    expect(view.fallbacks[0]?.detail).toBe('Подробности — в техническом приложении паспорта.')
  })

  it('falls back to variant PEI and marks unknown proof values', () => {
    const raw = structuredClone(passportFixture) as unknown as { metrics: Record<string, unknown> }
    raw.metrics.validity = {}
    raw.metrics.editability = {}

    const view = buildPassportView(parsePassport(raw), { ...context, fallbackPei: 5 })
    const byKey = indexBy(view.proof)

    expect(byKey('roundTrip')).toMatchObject({ value: '—', tone: 'unknown' })
    expect(byKey('nativeText')).toMatchObject({ value: '—', tone: 'unknown' })
    expect(byKey('pei')).toMatchObject({ value: '5/5', hint: 'шкала 0–5; 5 — всё нативное и привязано к макетам' })
  })
})

describe('researchView', () => {
  it('turns fact origins into shares and labels the text mode', () => {
    const passport = parsePassport({
      ...passportFixture,
      metrics: { ...passportFixture.metrics, content_origin: { user: 6, web: 3, model: 3 }, research: { sources_count: 2, content_fill: 'shrink' } },
    })
    const view = buildPassportView(passport, { budgetSeconds: 300 }).research
    expect(view?.origins.map((origin) => [origin.key, origin.fraction, origin.label])).toEqual([
      ['user', 0.5, 'ваш контент'],
      ['web', 0.25, 'интернет'],
      ['model', 0.25, 'дополнено по смыслу'],
    ])
    expect(view).toMatchObject({ sources: 2, mode: 'сохранить как есть', seconds: null })
    expect(buildPassportView(parsePassport(passportFixture), { budgetSeconds: 300 }).research).toBeNull()
  })
})

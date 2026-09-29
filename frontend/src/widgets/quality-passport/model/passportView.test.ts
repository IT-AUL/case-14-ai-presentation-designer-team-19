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

  it('shows style fidelity and usage once the backend sends them', () => {
    const raw = structuredClone(passportFixture) as unknown as { metrics: Record<string, unknown> }
    raw.metrics.style_fidelity = { palette_compliance: 0.97, font_compliance: 1, custom_score: 0.5 }
    raw.metrics.usage = { total_tokens: 48200, model_calls: 27 }

    const view = buildPassportView(parsePassport(raw), context)

    expect(view.style).toEqual({
      status: 'ready',
      scores: [
        { key: 'palette_compliance', label: 'Палитра', value: '0.97', fraction: 0.97 },
        { key: 'font_compliance', label: 'Шрифты', value: '1.00', fraction: 1 },
        { key: 'custom_score', label: 'Custom score', value: '0.50', fraction: 0.5 },
      ],
    })
    expect(view.usage?.map((item) => item.label)).toEqual(['Токенов всего', 'Вызовов модели'])
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

  it('exposes provenance for versioned skills, prompts, models and configs', () => {
    const view = buildPassportView(recorded(), { ...context, serverSkillVersion: '0.2.0' })
    const rows = indexBy(view.provenance)

    expect(view.skillVersion).toBe('0.1.0')
    expect(rows('skill')).toMatchObject({ value: '0.1.0', note: 'сейчас на сервере 0.2.0 — прогон собран предыдущей версией' })
    expect(rows('pipeline').value).toBe('deckdna-poc/0.1.0')
    expect(rows('planner').value).toBe('story-director/deterministic-0.1.0')
    expect(rows('prompts')).toMatchObject({ muted: true })
    expect(rows('models')).toMatchObject({ value: 'без модели', muted: true })
    expect(rows('config:configs/generation.default.yaml').value).toBe('configs/generation.default.yaml · sha 80f8…6056')
  })

  it('lists prompt versions and model profiles from an LLM run', () => {
    const raw = structuredClone(passportFixture) as unknown as { provenance: Record<string, unknown> }
    raw.provenance.prompt_versions = { plan: '2026.09.3', rewrite: '2026.09.1' }
    raw.provenance.model_profiles = [{ role: 'text', model_id: 'Qwen3-32B' }, { role: 'vision', model_id: 'Qwen2.5-VL-32B-Instruct' }]

    const view = buildPassportView(parsePassport(raw), { ...context, serverSkillVersion: '0.1.0' })
    const rows = indexBy(view.provenance)

    expect(rows('skill').note).toBeUndefined()
    expect(rows('prompts').value).toBe('plan 2026.09.3 · rewrite 2026.09.1')
    expect(rows('models').value).toBe('text: Qwen3-32B · vision: Qwen2.5-VL-32B-Instruct')
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

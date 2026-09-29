import type { DeckPlan, SlidePlan } from '@/entities/generation'
import { contentPackFixture, generationFixture } from '@/shared/api/mocks'
import { buildPlanView, deckPurposeLabel, formatSlideRange, parseEvidenceRef, plannedVisual, slideIdea, slideSources } from './plan'

const plan = generationFixture.deck_plan as DeckPlan
const headings = new Map(contentPackFixture.content_pack.sections.map((section) => [section.id, section.heading]))

function slide(changes: Partial<SlidePlan>): SlidePlan {
  const base = plan.slides[1]
  if (!base) throw new Error('fixture plan must contain slides')
  return { ...base, ...changes }
}

describe('deck plan view', () => {
  it('parses evidence references of the content pack', () => {
    expect(parseEvidenceRef('pack-7aa41ae9:sec-1:b1')).toEqual({ raw: 'pack-7aa41ae9:sec-1:b1', packId: 'pack-7aa41ae9', sectionId: 'sec-1', blockId: 'b1', meta: false })
    expect(parseEvidenceRef('pack-7aa41ae9:meta').meta).toBe(true)
    expect(parseEvidenceRef('orphan').sectionId).toBeNull()
  })

  it('groups evidence by content section and names it by heading', () => {
    const sources = slideSources(['pack-1:sec-0:b0', 'pack-1:sec-1:b0', 'pack-1:sec-1:b1', 'pack-1:meta', 'orphan'], headings)
    expect(sources.map((source) => [source.label, source.refs.length])).toEqual([
      ['DeckDNA: компилятор презентаций для хакатона «Лидеры цифровой трансформации»', 1],
      ['Проблема', 2],
      ['Заголовок контента', 1],
      ['orphan', 1],
    ])
    expect(slideSources(['pack-1:sec-9:b0'], new Map())[0]?.label).toBe('sec-9')
  })

  it('names the planned visualization', () => {
    expect(plannedVisual(slide({ desired_visual: 'chart' }))).toBe('График')
    expect(plannedVisual(slide({ desired_visual: 'none' }))).toBe('Текст')
    expect(plannedVisual(slide({ desired_visual: null, content_units: [] }))).toBeNull()
    expect(plannedVisual(slide({ desired_visual: 'none', content_units: [{ role: 'body', kind: 'table', table_ref: 't1' }] }))).toBe('Таблица')
  })

  it('hides a key message that only repeats the title or the objective', () => {
    expect(slideIdea(slide({ title_intent: 'Проблема', key_message: 'Проблема' }), 'product')).toBeNull()
    expect(slideIdea(slide({ title_intent: 'Титул', key_message: 'product' }), 'product')).toBeNull()
    expect(slideIdea(slide({ title_intent: 'Рынок растёт', key_message: 'На 30% в год' }), 'product')).toBe('На 30% в год')
  })

  it('formats slide ranges', () => {
    expect(formatSlideRange([3])).toBe('слайд 3')
    expect(formatSlideRange([4, 3])).toBe('слайды 3–4')
    expect(formatSlideRange([1, 2, 3, 5, 8, 9])).toBe('слайды 1–3, 5, 8–9')
    expect(formatSlideRange([])).toBe('нет слайдов')
  })

  it('translates the deck purpose', () => {
    expect(deckPurposeLabel('initiative')).toBe('инициатива')
    expect(deckPurposeLabel('pitch')).toBe('pitch')
  })

  it('builds rows, sections and stats from the recorded plan', () => {
    const view = buildPlanView(plan, headings)
    expect(view.rows).toHaveLength(12)
    const [first, second, third] = view.rows
    expect(first).toMatchObject({ number: '01', purpose: 'Титул', idea: null, visual: '', sectionId: null })
    expect(first?.sources.map((source) => source.label)).toEqual(['Заголовок контента'])
    expect(second).toMatchObject({ number: '02', purpose: 'Данные', visual: 'Текст', sectionId: 'sec-0' })
    expect(third).toMatchObject({ purpose: 'Проблема', sectionId: 'sec-1', sectionTitle: 'Проблема' })

    expect(view.sections.map((section) => [section.title, section.range])).toEqual([
      ['DeckDNA: компилятор презентаций для хакатона «Лидеры цифровой трансформации»', 'слайд 2'],
      ['Проблема', 'слайды 2–3'],
      ['Архитектура: семь стадий пайплайна', 'слайды 4–5'],
      ['Три варианта, а не один', 'слайды 6–7'],
      ['Аудит и качество', 'слайды 8–9'],
      ['Доказательства без хардкода', 'слайды 9–10'],
      ['Что дальше', 'слайды 10–11'],
      ['Вне разделов', 'слайды 1, 12'],
    ])
    expect(view.stats).toEqual({ slides: 12, withSources: 12, withVisual: 0, mandatory: 0 })
  })

  it('orders slides by their index and tolerates a plan without sections', () => {
    const [a, b] = plan.slides
    if (!a || !b) throw new Error('fixture plan must contain slides')
    const view = buildPlanView({ ...plan, sections: null, slides: [b, a] }, new Map())
    expect(view.rows.map((row) => row.id)).toEqual([a.id, b.id])
    expect(view.sections).toEqual([])
  })
})

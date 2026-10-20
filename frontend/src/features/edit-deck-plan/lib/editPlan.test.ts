import { generationFixture } from '@/shared/api/mocks'
import type { DeckPlan, SlidePlan } from '@/entities/generation'
import dataPlanResponse from '@/shared/api/mocks/fixtures/assistant/data-plan.json'
import {
  addSlide,
  addUnit,
  annotateKeyMessageSupport,
  applyQuickAction,
  canApplyQuickAction,
  changeSlideChart,
  editSummary,
  isPlanEdited,
  keepSlideOnTopic,
  moveSlide,
  moveUnit,
  parseSlideChart,
  prepareForBuild,
  removeSlide,
  removeUnit,
  updateKeyMessage,
  updateSlide,
  updateUnit,
} from './editPlan'

const plan = generationFixture.deck_plan as DeckPlan
const dataPlan = dataPlanResponse.body.plans[0]?.deck_plan as DeckPlan
const charted = dataPlan.slides.find((slide) => slide.chart) as SlidePlan
const ids = (target: DeckPlan) => [...target.slides].sort((a, b) => a.index - b.index).map((slide) => slide.id)
const [first = '', second = ''] = ids(plan)

describe('editPlan', () => {
  it('moves a slide and keeps indexes contiguous', () => {
    const moved = moveSlide(plan, first, 1)
    expect(ids(moved).slice(0, 2)).toEqual([second, first])
    expect(moved.slides.map((slide) => slide.index)).toEqual(moved.slides.map((_, index) => index))
    expect(moveSlide(plan, first, -1)).toBe(plan)
  })

  it('removes a slide from the plan and its sections', () => {
    const removed = removeSlide(plan, first)
    expect(ids(removed)).not.toContain(first)
    expect(removed.sections?.flatMap((section) => section.slide_ids)).not.toContain(first)
    expect(editSummary(plan, removed)).toMatchObject({ removed: 1, added: 0 })
  })

  it('never removes the last slide', () => {
    const single = { ...plan, slides: plan.slides.slice(0, 1) }
    expect(removeSlide(single, single.slides[0]?.id ?? '')).toBe(single)
  })

  it('edits titles and messages and reports the plan as edited', () => {
    const edited = updateSlide(plan, first, { title_intent: 'Рынок растёт на 30% в год' })
    expect(edited.slides.find((slide) => slide.id === first)?.title_intent).toBe('Рынок растёт на 30% в год')
    expect(isPlanEdited(plan, edited)).toBe(true)
    expect(editSummary(plan, edited).renamed).toBe(1)
    expect(updateSlide(plan, first, {})).toBe(plan)
  })

  it('adds a valid slide with a unique id at the end', () => {
    const added = addSlide(addSlide(plan))
    const extra = ids(added).slice(-2)
    expect(new Set(extra).size).toBe(2)
    expect(added.slides.find((slide) => slide.id === extra[1])).toMatchObject({ purpose: 'custom', density_budget: { level: 'medium' }, index: plan.slides.length + 1 })
    expect(isPlanEdited(plan, moveSlide(moveSlide(plan, first, 1), first, -1))).toBe(false)
  })

  it('edits, adds, reorders and removes points and locks what the user wrote', () => {
    const target = plan.slides.find((slide) => slide.content_units.some((unit) => unit.kind === 'bullet'))
    if (!target) throw new Error('fixture has no bullets')
    const position = target.content_units.findIndex((unit) => unit.kind === 'bullet')
    const edited = updateUnit(plan, target.id, position, 'Своя формулировка')
    const unit = edited.slides.find((slide) => slide.id === target.id)?.content_units[position]
    expect(unit).toMatchObject({ text: 'Своя формулировка', locked: true })
    expect(isPlanEdited(plan, edited)).toBe(true)
    expect(editSummary(plan, edited).renamed).toBe(1)

    const added = addUnit(edited, target.id, 'Новый пункт')
    const units = added.slides.find((slide) => slide.id === target.id)?.content_units ?? []
    expect(units.at(-1)).toMatchObject({ kind: 'bullet', text: 'Новый пункт', locked: true })

    const titleIndex = units.findIndex((item) => item.kind === 'title')
    expect(removeUnit(added, target.id, titleIndex)).toBe(added)
    expect(removeUnit(added, target.id, units.length - 1).slides.find((slide) => slide.id === target.id)?.content_units).toHaveLength(units.length - 1)

    const moved = moveUnit(added, target.id, units.length - 1, -1)
    const movedUnits = moved.slides.find((slide) => slide.id === target.id)?.content_units ?? []
    expect(movedUnits.at(-2)?.text).toBe('Новый пункт')
    expect(moveUnit(added, target.id, units.length - 1, 1)).toBe(added)
  })

  it('changes the desired visual', () => {
    const edited = updateSlide(plan, first, { desired_visual: 'table' })
    expect(edited.slides.find((slide) => slide.id === first)?.desired_visual).toBe('table')
    expect(isPlanEdited(plan, edited)).toBe(true)
  })

  it('prepares a valid plan for the service: evidence for new slides, no empty points, optional lock', () => {
    const withSlide = addUnit(addSlide(plan), `slide-user-${plan.slides.length + 1}`)
    const prepared = prepareForBuild(withSlide)
    const extra = prepared.slides.at(-1)
    expect(extra?.evidence_ids).toHaveLength(1)
    expect(extra?.evidence_ids[0]).toMatch(/:meta$/)
    expect(extra?.content_units).toEqual([])
    expect(prepared.slides.every((slide) => slide.evidence_ids.length > 0)).toBe(true)
    const locked = prepareForBuild(plan, { lockAll: true })
    const bullets = locked.slides.flatMap((slide) => slide.content_units).filter((unit) => unit.kind === 'bullet')
    expect(bullets.every((unit) => (unit as { locked?: boolean }).locked === true)).toBe(true)
    expect(locked.slides.flatMap((slide) => slide.content_units).find((unit) => unit.kind === 'title')).not.toHaveProperty('locked')
  })

  it('parses the chart contract and tolerates missing or malformed data', () => {
    const chart = parseSlideChart(charted.chart)
    expect(chart).toMatchObject({ type: 'line' })
    expect(chart?.alternatives.map((alt) => alt.type)).toEqual(['bar', 'table'])
    expect(chart?.why).toBeTruthy()
    expect(parseSlideChart(null)).toBeNull()
    expect(parseSlideChart({})).toBeNull()
    expect(parseSlideChart({ type: '  ' })).toBeNull()
  })

  it('switches the chart type optimistically and keeps the old type as an alternative', () => {
    const edited = changeSlideChart(dataPlan, charted.id, 'bar')
    const slide = edited.slides.find((item) => item.id === charted.id)
    const chart = parseSlideChart(slide?.chart)
    expect(chart?.type).toBe('bar')
    expect(chart?.alternatives.map((alt) => alt.type)).toEqual(['line', 'table'])
    expect(chart?.why).toContain('Столбцы')
    expect(slide?.desired_visual).toBe('chart')
    const chartUnit = slide?.content_units.find((unit) => unit.kind === 'chart')
    expect(chartUnit?.chart_spec?.chart_type).toBe('bar')
    expect(isPlanEdited(dataPlan, edited)).toBe(true)
  })

  it('turns a chart into a table when the table type is picked', () => {
    const edited = changeSlideChart(dataPlan, charted.id, 'table')
    const slide = edited.slides.find((item) => item.id === charted.id)
    expect(slide?.desired_visual).toBe('table')
    const unit = slide?.content_units.find((item) => item.chart_ref === 'chart-ds-95b1eb6e-trend')
    expect(unit?.kind).toBe('table')
    expect(unit?.chart_spec).toBeNull()
    expect(changeSlideChart(dataPlan, charted.id, 'line')).toBe(dataPlan)
    const custom = changeSlideChart(dataPlan, charted.id, 'sunburst')
    expect(parseSlideChart(custom.slides.find((slide) => slide.id === charted.id)?.chart)?.type).toBe('sunburst')
  })

  it('applies quick actions locally and guards what cannot run', () => {
    const title = dataPlan.slides.find((slide) => slide.purpose === 'title')?.id ?? ''
    const textSlide = dataPlan.slides.find((slide) => slide.id !== title && slide.content_units.filter((unit) => unit.kind === 'bullet').length >= 2) as SlidePlan
    const next = dataPlan.slides.find((slide) => slide.index === textSlide.index + 1) as SlidePlan

    expect(canApplyQuickAction(dataPlan, title, 'shorten')).toBe(false)
    expect(canApplyQuickAction(dataPlan, title, 'split')).toBe(false)
    expect(canApplyQuickAction(dataPlan, title, 'remove')).toBe(false)
    expect(canApplyQuickAction(dataPlan, textSlide.id, 'shorten')).toBe(true)

    const shortened = applyQuickAction(dataPlan, textSlide.id, 'shorten')
    const kept = shortened.slides.find((slide) => slide.id === textSlide.id)?.content_units.filter((unit) => unit.kind === 'bullet') ?? []
    expect(kept.length).toBeLessThan(textSlide.content_units.filter((unit) => unit.kind === 'bullet').length)

    const split = applyQuickAction(dataPlan, textSlide.id, 'split')
    const position = ids(split).indexOf(textSlide.id)
    const continuation = split.slides[position + 1]
    expect(continuation?.title_intent).toBe(`${textSlide.title_intent} (продолжение)`)
    expect(split.slides.map((slide) => slide.index)).toEqual(split.slides.map((_, index) => index))

    const merged = applyQuickAction(dataPlan, textSlide.id, 'merge_next')
    const mergedSlide = merged.slides.find((slide) => slide.id === textSlide.id)
    expect(ids(merged)).not.toContain(next.id)
    expect(mergedSlide?.content_units.length).toBeGreaterThan(textSlide.content_units.length)
    expect(mergedSlide?.evidence_ids).toEqual(expect.arrayContaining(next.evidence_ids))

    expect(applyQuickAction(dataPlan, textSlide.id, 'to_chart').slides.find((slide) => slide.id === textSlide.id)?.desired_visual).toBe('chart')
    expect(applyQuickAction(dataPlan, textSlide.id, 'to_diagram').slides.find((slide) => slide.id === textSlide.id)?.desired_visual).toBe('diagram')
    expect(ids(applyQuickAction(dataPlan, textSlide.id, 'remove'))).not.toContain(textSlide.id)
  })

  it('marks slides that do not support the key message', () => {
    const message = 'Выручка выросла вдвое за счёт московского сегмента'
    const annotated = annotateKeyMessageSupport(dataPlan, message)
    expect(annotated.brief.key_message).toBe(message)
    expect(annotated.slides[0]?.supports_key_message).toBe(true)
    const salesSlide = annotated.slides.find((slide) => slide.id === 'slide-2')
    expect(salesSlide?.supports_key_message).toBe(true)
    const unrelated = annotated.slides.find((slide) => slide.supports_key_message === false)
    expect(unrelated?.id).toBe('slide-6')

    const cleared = annotateKeyMessageSupport(annotated, '   ')
    expect(cleared.brief.key_message).toBeNull()
    expect(cleared.slides.every((slide) => slide.supports_key_message === null)).toBe(true)
  })

  it('writes the key message into the brief and the cover slide', () => {
    const message = 'Выручка выросла вдвое за счёт московских продаж'
    const edited = updateKeyMessage(dataPlan, message)
    expect(edited.brief.key_message).toBe(message)
    expect(edited.slides[0]?.key_message).toBe(message)
    expect(isPlanEdited(dataPlan, edited)).toBe(true)
  })

  it('restores a dismissed slide hint when the user keeps it', () => {
    const marked = {
      ...dataPlan,
      slides: dataPlan.slides.map((slide, index) => (index === 3 ? { ...slide, supports_key_message: false } : slide)),
    }
    const kept = keepSlideOnTopic(marked, marked.slides[3]?.id ?? '')
    expect(kept.slides[3]?.supports_key_message).toBe(true)
    expect(isPlanEdited(marked, kept)).toBe(true)
  })
  it('sends the edited headline instead of stale title copy to composition', () => {
    const base = plan.slides[0]
    if (!base) throw new Error('Missing fixture slide')
    const original = { ...plan, slides: [{ ...base, content_units: [{ role: 'title', kind: 'title' as const, text: 'Старый заголовок' }] }] }
    const edited = updateSlide(original, base.id, { title_intent: 'Новый вывод' })
    expect(edited.slides[0]?.content_units[0]).toMatchObject({ text: 'Новый вывод', locked: true })
    const restored = { ...original, slides: [{ ...base, content_units: original.slides[0]?.content_units ?? [], title_intent: 'Восстановленный вывод' }] }
    expect(prepareForBuild(restored).slides[0]?.content_units[0]).toMatchObject({ text: 'Восстановленный вывод', locked: true })
  })

})

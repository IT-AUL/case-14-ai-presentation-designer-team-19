import { createElement } from 'react'
import { render, screen } from '@testing-library/react'
import type { DeckPlan, SlidePlan } from '@/entities/generation'
import dataPlanResponse from '@/shared/api/mocks/fixtures/assistant/data-plan.json'
import { DeckPlanView } from '../ui/DeckPlanView'
import {
  availableQuickActions,
  buildPlanView,
  collectSlidePatches,
  contentPackSourceMap,
  EDITOR_PURPOSE_LABEL,
  EDITOR_PURPOSES,
  isMeaningfulKeyMessage,
  isUserAddedSlide,
  parseSlideChartView,
  resolveDeckKeyMessage,
  resolveEditorPurpose,
  resolveSufficiencyBanner,
  slideRoleLabel,
  slideSourceKinds,
  slideSources,
  syncPlanDraftToStorage,
} from './plan'

const dataPlan = dataPlanResponse.body.plans[0]?.deck_plan as DeckPlan
const slides = [...dataPlan.slides].sort((a, b) => a.index - b.index)
const charted = slides.find((slide) => slide.chart) as SlidePlan

describe('parseSlideChartView', () => {
  it('uses the backend explanation and lists the current type first', () => {
    const view = parseSlideChartView(charted)
    expect(view).not.toBeNull()
    expect(view?.type).toBe('line')
    expect(view?.why).toBe((charted.chart as { why?: string }).why)
    expect(view?.options.map((option) => option.type)).toEqual(['line', 'bar', 'table'])
    expect(view?.options[0]).toMatchObject({ active: true, label: 'Линия' })
  })

  it('falls back to reasons when why is missing, normalizes Russian case grammar, and guarantees 2-3 ranked alternatives', () => {
    const slide: SlidePlan = {
      ...charted,
      chart: {
        type: 'bar',
        reasons: ['три категории', 'в задаче сказано про динамика'],
        alternatives: [{ type: 'bar' }, { type: 'line', reasons: ['тренд', 'в задаче сказано про структура'] }],
      },
    }
    const view = parseSlideChartView(slide)
    expect(view?.why).toBe('Столбцы: три категории, в задаче сказано про динамику.')
    expect(view?.options.map((option) => option.type)).toEqual(['bar', 'line'])
    expect(view?.options[1]?.reasons).toEqual(['тренд', 'в задаче сказано про структуру'])
  })

  it('infers a chart explanation with 2-3 ranked alternatives for a data slide when slide.chart is null', () => {
    const unchartDataSlide: SlidePlan = {
      ...charted,
      purpose: 'data',
      chart: null,
      content_units: [
        {
          role: 'chart',
          kind: 'chart',
          text: 'Выручка по кварталам',
          chart_spec: { chart_type: 'line', series_count: 1, has_axis_labels: true, has_units: true },
        },
        {
          role: 'bullet',
          kind: 'bullet',
          text: 'Рост на 42% за 4 квартала',
        },
      ],
    }
    const view = parseSlideChartView(unchartDataSlide)
    expect(view).not.toBeNull()
    expect(view?.type).toBe('line')
    expect(view?.why).toContain('Линия:')
    expect(view?.options.map((option) => option.type)).toEqual(['line', 'bar', 'table'])
    expect(view?.options[1]?.reasons.length).toBeGreaterThan(0)
  })

  it('returns null without a chart or data signals', () => {
    expect(parseSlideChartView({ chart: null })).toBeNull()
    expect(parseSlideChartView({ chart: { type: '' } })).toBeNull()
  })
})

describe('availableQuickActions', () => {
  it('offers text actions only where they make sense', () => {
    const title = slides[0] as SlidePlan
    expect(availableQuickActions(dataPlan, title.id)).toEqual([])
    const overview = slides[1] as SlidePlan
    const actions = availableQuickActions(dataPlan, overview.id)
    expect(actions).toEqual(expect.arrayContaining(['shorten', 'split', 'merge_next', 'to_chart', 'to_diagram', 'remove']))
    const last = slides[slides.length - 1] as SlidePlan
    expect(availableQuickActions(dataPlan, last.id)).toEqual(['remove'])
  })

  it('hides merge on the last content slide and chart action on charted slides', () => {
    const custom = slides.find((slide) => slide.purpose === 'custom') as SlidePlan
    const actions = availableQuickActions(dataPlan, custom.id)
    expect(actions).not.toContain('merge_next')
    expect(availableQuickActions(dataPlan, charted.id)).not.toContain('to_chart')
  })
})

describe('resolveDeckKeyMessage', () => {
  it('prefers the explicit message, then the brief, then slide messages', () => {
    expect(resolveDeckKeyMessage(dataPlan, '  Своя мысль  ')).toBe('Своя мысль')
    const withBrief = { ...dataPlan, brief: { ...dataPlan.brief, key_message: 'Главная мысль из брифа' } }
    expect(resolveDeckKeyMessage(withBrief, null)).toBe('Главная мысль из брифа')
    expect(resolveDeckKeyMessage(dataPlan, null)).toBe('Выручка: рост в 2,1 раза за 24 месяца')
  })

  it('uses session.brief.key_message or session.brief.purpose when step 3 was skipped and ignores technical presentation code', () => {
    expect(isMeaningfulKeyMessage('presentation')).toBe(false)
    expect(isMeaningfulKeyMessage('Презентация')).toBe(false)
    expect(isMeaningfulKeyMessage('Рост выручки облачного бизнеса')).toBe(true)

    expect(
      resolveDeckKeyMessage(dataPlan, null, { key_message: 'Ускорение внедрения облака на 40%', purpose: 'presentation' }),
    ).toBe('Ускорение внедрения облака на 40%')

    expect(
      resolveDeckKeyMessage(dataPlan, null, { key_message: null, purpose: 'Итоги пилота и план масштабирования' }),
    ).toBe('Итоги пилота и план масштабирования')

    expect(
      resolveDeckKeyMessage(dataPlan, 'presentation', { key_message: '', purpose: 'presentation' }),
    ).toBe('Выручка: рост в 2,1 раза за 24 месяца')
  })

  it('falls back to the first key takeaway title when brief and overview messages are empty', () => {
    const strippedPlan: DeckPlan = {
      ...dataPlan,
      objective: 'presentation',
      brief: { ...dataPlan.brief, purpose: 'presentation', key_message: null },
      slides: [
        { ...(slides[0] as SlidePlan), purpose: 'title', title_intent: 'Презентация', key_message: 'presentation' },
        { ...(slides[1] as SlidePlan), purpose: 'custom', title_intent: 'Автоматизация снижает затраты на 32%', key_message: '' },
      ],
    }
    expect(resolveDeckKeyMessage(strippedPlan, null, { purpose: 'presentation' })).toBe('Автоматизация снижает затраты на 32%')
  })
})

describe('resolveSufficiencyBanner', () => {
  it('hides expand action and shows calm reserve message when estimated_slides >= targetCount', () => {
    const banner = resolveSufficiencyBanner({ status: 'short', contentSlides: 10, targetSlides: 8 }, 8)
    expect(banner).not.toBeNull()
    expect(banner?.canExpand).toBe(false)
    expect(banner?.text).toBe('Материала достаточно для 8 слайдов, запас — до 10.')

    const snakeCaseBanner = resolveSufficiencyBanner({ status: 'ok', estimated_slides: 10, target_slides: 8 }, 8)
    expect(snakeCaseBanner?.canExpand).toBe(false)
    expect(snakeCaseBanner?.text).toBe('Материала достаточно для 8 слайдов, запас — до 10.')
  })

  it('enables expand action only when estimated_slides < targetCount', () => {
    const shortBanner = resolveSufficiencyBanner({ status: 'short', contentSlides: 5, targetSlides: 8 }, 8)
    expect(shortBanner).not.toBeNull()
    expect(shortBanner?.canExpand).toBe(true)
    expect(shortBanner?.text).toBe('Материала хватает на 5 слайдов из 8.')
  })
})

describe('slideRoleLabel, isUserAddedSlide, and editor role mapping', () => {
  it('maps Russian editor roles (title, summary, data, comparison, roadmap, closing)', () => {
    expect(EDITOR_PURPOSES).toEqual(['title', 'summary', 'data', 'comparison', 'roadmap', 'closing'])
    expect(EDITOR_PURPOSE_LABEL).toEqual({
      title: 'Титул',
      summary: 'Вывод',
      data: 'Данные',
      comparison: 'Сравнение',
      roadmap: 'План',
      closing: 'Финал',
    })
    expect(resolveEditorPurpose('overview')).toBe('summary')
    expect(resolveEditorPurpose('agenda')).toBe('roadmap')
    expect(resolveEditorPurpose('thank_you')).toBe('closing')
    expect(resolveEditorPurpose('summary')).toBe('summary')
    expect(resolveEditorPurpose('roadmap')).toBe('roadmap')
    expect(resolveEditorPurpose('closing')).toBe('closing')
  })

  it('shows Свой слайд only for manually added user slides and Контент for markdown section slides', () => {
    const mdSectionSlide: SlidePlan = {
      ...(slides[0] as SlidePlan),
      id: 'slide-6',
      purpose: 'custom',
      evidence_ids: ['sec_01'],
    }
    expect(isUserAddedSlide(mdSectionSlide)).toBe(false)
    expect(slideRoleLabel(mdSectionSlide)).toBe('Контент')

    const packSectionSlide: SlidePlan = {
      ...(slides[0] as SlidePlan),
      id: 'slide-6',
      purpose: 'custom',
      evidence_ids: ['pack_52549aa7639543a8b68b55c5f60d42a8:sec-notes-1:b0'],
    }
    expect(isUserAddedSlide(packSectionSlide)).toBe(false)
    expect(slideRoleLabel(packSectionSlide)).toBe('Контент')

    const manualUserSlide: SlidePlan = {
      ...(slides[0] as SlidePlan),
      id: 'slide-user-m9abc-9',
      purpose: 'custom',
      evidence_ids: [],
      content_units: [{ role: 'bullet', kind: 'bullet', text: 'Свой тезис', origin: 'user', locked: false }],
    }
    expect(isUserAddedSlide(manualUserSlide)).toBe(true)
    expect(slideRoleLabel(manualUserSlide)).toBe('Свой слайд')

    const reassignedUserSlide = {
      ...manualUserSlide,
      purpose: 'summary',
    }
    expect(slideRoleLabel(reassignedUserSlide)).toBe('Вывод')
  })
})

describe('buildPlanView', () => {
  it('carries the chart view, key-message flag, and clean role labels into rows', () => {
    const marked: DeckPlan = {
      ...dataPlan,
      slides: dataPlan.slides.map((slide) => (slide.id === 'slide-6' ? { ...slide, supports_key_message: false } : slide)),
    }
    const view = buildPlanView(marked, new Map())
    const dataRow = view.rows.find((row) => row.id === charted.id)
    expect(dataRow?.chart?.type).toBe('line')
    expect(dataRow?.supportsKeyMessage).toBeNull()
    const sectionRow = view.rows.find((row) => row.id === 'slide-6')
    expect(sectionRow?.supportsKeyMessage).toBe(false)
    expect(sectionRow?.purpose).toBe('Контент')
  })
})

describe('slideSourceKinds', () => {
  const base = { evidence_ids: [], content_units: [] } as unknown as SlidePlan

  it('marks dataset evidence as a table and section evidence as text', () => {
    expect(slideSourceKinds({ ...base, evidence_ids: ['pack_1:ds-95b1eb6e-trend-i0'] })).toEqual(['table'])
    expect(slideSourceKinds({ ...base, evidence_ids: ['pack_1:sec-notes-1:b0'] })).toEqual(['text'])
  })

  it('ignores the meta evidence and keeps a stable order', () => {
    const slide = {
      ...base,
      evidence_ids: ['pack_1:meta', 'pack_1:sec-a:b0', 'pack_1:ds-1'],
      content_units: [{ kind: 'bullet', text: 'x', origin: 'web' }],
    } as unknown as SlidePlan
    expect(slideSourceKinds(slide)).toEqual(['table', 'text', 'web'])
    expect(slideSourceKinds({ ...base, evidence_ids: ['pack_1:meta'] })).toEqual([])
  })
})

describe('contentPackSourceMap and slideSources', () => {
  it('resolves ds-*, sec_* and pack:meta to human names from content pack', () => {
    const map = contentPackSourceMap({
      title_hint: 'Итоги Q1 2026',
      sections: [
        { id: 'sec-0', heading: 'Резюме для руководства' },
        { id: 'sec-1', heading: 'Региональная динамика' },
      ],
      datasets: [
        { id: 'ds-95b1eb6e', title: 'Продажи за 2025–2026', file_name: 'sales.xlsx' },
      ],
    })
    const sources = slideSources(
      [
        'pack_52549aa7639543a8b68b55c5f60d42a8:meta',
        'pack_52549aa7639543a8b68b55c5f60d42a8:ds-95b1eb6e-trend-i0',
        'sec_01',
      ],
      map,
    )
    expect(sources.map((item) => item.label)).toEqual([
      'Итоги Q1 2026',
      'Продажи за 2025–2026',
      'Региональная динамика',
    ])
  })

  it('falls back to ваши материалы for unknown technical IDs and never leaks raw IDs', () => {
    const sources = slideSources(
      ['ds-ac6da9594b45', 'sec_99', 'pack_52549aa7639543a8b68b55c5f60d42a8:meta'],
      new Map(),
    )
    expect(sources).toEqual([
      {
        key: 'materials',
        label: 'ваши материалы',
        refs: ['ds-ac6da9594b45', 'sec_99', 'pack_52549aa7639543a8b68b55c5f60d42a8:meta'],
      },
    ])
  })
})

describe('collectSlidePatches and syncPlanDraftToStorage (F-47)', () => {
  it('detects modified slide titles, bullets, purpose, and order for server sync before autopilot', () => {
    const editedPlan: DeckPlan = {
      ...dataPlan,
      slides: dataPlan.slides.map((slide, index) => {
        if (index === 1) {
          const firstBulletIndex = slide.content_units.findIndex((unit) => unit.kind === 'bullet')
          return {
            ...slide,
            title_intent: 'Обновлённый заголовок слайда 2',
            purpose: 'summary' as SlidePlan['purpose'],
            content_units: slide.content_units.map((unit, unitIndex) =>
              unitIndex === firstBulletIndex ? { ...unit, text: 'Обновлённый первый тезис' } : unit,
            ),
          }
        }
        return slide
      }),
    }

    const patches = collectSlidePatches(dataPlan, editedPlan)
    expect(patches).toHaveLength(1)
    expect(patches[0]).toMatchObject({
      slideId: 'slide-1',
      body: {
        title: 'Обновлённый заголовок слайда 2',
        title_intent: 'Обновлённый заголовок слайда 2',
        purpose: 'summary',
      },
    })
    expect((patches[0]?.body.bullets as string[])[0]).toBe('Обновлённый первый тезис')
  })

  it('persists the active draft into localStorage under draftKey', () => {
    syncPlanDraftToStorage('prj_test:plan_1:balanced', dataPlan, dataPlan)
    const raw = window.localStorage.getItem('deckdna.plan-draft.v1:prj_test:plan_1:balanced')
    expect(raw).toBeTruthy()
    const parsed = JSON.parse(raw ?? '{}') as { baseId: string; plan: DeckPlan }
    expect(parsed.baseId).toBe(dataPlan.slides.map((slide) => slide.id).join('|'))
    expect(parsed.plan.slides).toHaveLength(dataPlan.slides.length)
  })
})

describe('DeckPlanView sufficiency banner and role labels (F-67)', () => {
  it('renders calm reserve banner without Дополнить when estimated_slides >= targetSlides', () => {
    render(
      createElement(DeckPlanView, {
        plan: dataPlan,
        headings: new Map(),
        sufficiency: { status: 'short', contentSlides: 10, targetSlides: 8 },
        onExpand: () => undefined,
      }),
    )

    expect(screen.getByText('Материала достаточно для 8 слайдов, запас — до 10.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Дополнить' })).toBeNull()
  })

  it('renders Дополнить button when estimated_slides < targetSlides', () => {
    render(
      createElement(DeckPlanView, {
        plan: dataPlan,
        headings: new Map(),
        sufficiency: { status: 'short', contentSlides: 5, targetSlides: 8 },
        onExpand: () => undefined,
      }),
    )

    expect(screen.getByText('Материала хватает на 5 слайдов из 8.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Дополнить' })).toBeInTheDocument()
  })
})


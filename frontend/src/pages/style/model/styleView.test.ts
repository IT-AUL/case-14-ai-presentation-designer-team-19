import { normalizeStylePreview, type JobEvent } from '@/entities/assistant'
import type { DeckPlan } from '@/entities/generation'
import planFixture from '@/shared/api/mocks/fixtures/assistant/data-plan.json'
import stylePreviewFixture from '@/shared/api/mocks/fixtures/assistant/data-style-preview.json'
import {
  buildGenerationPayload,
  buildStyleColumns,
  extractPreviewReadyMap,
  fallbackStyleTitle,
  formatExemplarLabel,
  pickKeySlideIds,
  resolveBuildModeSummary,
  resolveRecommendedStrategy,
  resolveStyleAssistantNote,
  resolveStyleTitle,
} from './styleView'

const deckPlan = (planFixture.body.plans[0]?.deck_plan ?? null) as unknown as DeckPlan
const normalizedPreview = normalizeStylePreview(stylePreviewFixture.body)

function makePreviewEvent(sequence: number, strategy: string, slideId: string, artifactId: string): JobEvent {
  return {
    sequence,
    type: 'preview.ready',
    timestamp: null,
    stage: 'style_preview',
    progress: 0,
    message: null,
    data: {
      strategy,
      slide_id: slideId,
      image_artifact_id: artifactId,
    },
  }
}

describe('styleView model', () => {
  it('picks cover, first data slide and text slide from the deck plan', () => {
    const ids = pickKeySlideIds(deckPlan)
    expect(ids).toEqual(['slide-0', 'slide-2', 'slide-1'])
  })

  it('formats exemplar paths into human labels', () => {
    expect(formatExemplarLabel('ppt/slides/slide24.xml')).toBe('Образец: слайд #24')
    expect(formatExemplarLabel('slide7')).toBe('Образец: слайд #7')
    expect(formatExemplarLabel(null)).toBeNull()
  })

  it('maps strategy codes and legacy backend titles to unified Russian labels', () => {
    expect(fallbackStyleTitle('faithful')).toBe('Близко к шаблону')
    expect(fallbackStyleTitle('strict')).toBe('Близко к шаблону')
    expect(fallbackStyleTitle('balanced')).toBe('Сбалансированный')
    expect(fallbackStyleTitle('visual')).toBe('Визуальный')
    expect(fallbackStyleTitle('creative')).toBe('Визуальный')

    expect(resolveStyleTitle('faithful', 'Строгий')).toBe('Близко к шаблону')
    expect(resolveStyleTitle('strict', 'Строгий')).toBe('Близко к шаблону')
    expect(resolveStyleTitle('balanced', 'Сбалансированный')).toBe('Сбалансированный')
    expect(resolveStyleTitle('visual', 'Визуальный')).toBe('Визуальный')
    expect(resolveStyleTitle('creative', null)).toBe('Визуальный')
  })

  it('resolves build mode summary copy for fast and checked modes', () => {
    expect(resolveBuildModeSummary('fast')).toBe('Быстро — соберёт колоду за минуту без промежуточной проверки')
    expect(resolveBuildModeSummary('checked')).toBe('С проверкой — проверит числа, источники и плотность каждого слайда')
  })

  it('builds loading skeletons before preview.ready events arrive, then populates ready images progressively', () => {
    const initial = buildStyleColumns({
      preview: null,
      events: [],
      isStreaming: true,
      plan: deckPlan,
    })
    expect(initial).toHaveLength(3)
    expect(initial[2]?.slots.map((slot) => [slot.slideId, slot.state, slot.loadingCaption])).toEqual([
      ['slide-0', 'loading', 'Верстаю обложку…'],
      ['slide-1', 'loading', 'Верстаю текстовый слайд…'],
      ['slide-2', 'loading', 'Верстаю слайд с графиком…'],
    ])

    const streaming = buildStyleColumns({
      preview: normalizedPreview,
      events: [makePreviewEvent(1, 'visual', 'slide-0', 'art_cover_visual')],
      isStreaming: true,
      plan: deckPlan,
    })
    const visualSlots = streaming.find((col) => col.strategy === 'visual')?.slots
    expect(visualSlots?.[0]?.state).toBe('ready')
    expect(visualSlots?.[0]?.imageUrl).toContain('art_cover_visual')
    expect(visualSlots?.[1]?.state).toBe('loading')
  })

  it('uses unified titles, differences, recommendation and final preview images once settled', () => {
    const settled = buildStyleColumns({
      preview: normalizedPreview,
      events: [],
      isStreaming: false,
      plan: deckPlan,
    })
    expect(settled.map((col) => [col.strategy, col.title, col.recommended])).toEqual([
      ['faithful', 'Близко к шаблону', false],
      ['balanced', 'Сбалансированный', false],
      ['visual', 'Визуальный', true],
    ])
    expect(settled[0]?.difference).toBe(
      'Ближе всего к вашему шаблону: его привычные раскладки, спокойная плотность текста.',
    )
    expect(resolveRecommendedStrategy(settled)).toBe('visual')
    expect(settled[2]?.slots.every((slot) => slot.state === 'ready' && Boolean(slot.imageUrl))).toBe(true)
  })

  it('extracts preview.ready map from job events', () => {
    const map = extractPreviewReadyMap([
      makePreviewEvent(1, 'faithful', 'slide-0', 'art_1'),
      makePreviewEvent(2, 'visual', 'slide-2', 'art_2'),
    ])
    expect(map.get('faithful:slide-0')).toBe('art_1')
    expect(map.get('visual:slide-2')).toBe('art_2')
  })

  it('resolves assistant note from backend preview, streaming progress or fallback', () => {
    expect(
      resolveStyleAssistantNote({
        preview: normalizedPreview,
        events: [],
        isStreaming: false,
        hasPlan: true,
      }),
    ).toBe('Советую визуальный: на 4 из 6 слайдов графики и схемы.')

    expect(
      resolveStyleAssistantNote({
        preview: null,
        events: [makePreviewEvent(1, 'visual', 'slide-0', 'art_1')],
        isStreaming: true,
        hasPlan: true,
      }),
    ).toContain('Примерил 1 из 9 пробных слайдов')
  })

  it('ignores a stale intake session note on the style step', () => {
    const fallback = 'Примерил три стиля вёрстки на ваших ключевых слайдах — сравните плотность текста и подачу графиков.'
    expect(
      resolveStyleAssistantNote({
        preview: null,
        events: [],
        sessionNote: 'Добавьте шаблон и материалы, начну с них',
        sessionStep: 1,
        isStreaming: false,
        hasPlan: true,
      }),
    ).toBe(fallback)
    expect(
      resolveStyleAssistantNote({
        preview: null,
        events: [],
        sessionNote: 'Материалы принял — добавьте корпоративный шаблон .pptx или .potx, чтобы я знал правила оформления.',
        sessionStep: 5,
        isStreaming: false,
        hasPlan: true,
      }),
    ).toBe(fallback)
    expect(
      resolveStyleAssistantNote({
        preview: null,
        events: [],
        sessionNote: 'План готов — выберите стиль вёрстки.',
        sessionStep: 5,
        isStreaming: false,
        hasPlan: true,
      }),
    ).toBe('План готов — выберите стиль вёрстки.')
  })

  it('keeps the project brief as the source of truth so the service accepts the inline plan', () => {
    const plan = { ...deckPlan, brief: { ...deckPlan.brief, purpose: 'presentation', key_message: null, call_to_action: null } }
    const payload = buildGenerationPayload({
      stored: null,
      plan,
      session: {
        step: 5,
        completed_steps: [1, 2, 3, 4],
        skipped_steps: [],
        artifacts: { template_id: 'tpl_1', content_pack_id: 'pack_1', brandbook_id: null, plan_id: 'plan_1', preview_id: null, generation_id: null },
        brief: { purpose: 'Итоги года', audience: 'Совет директоров', language: 'ru', key_message: 'подтвердить рост выручки', call_to_action: 'утвердить бюджет' },
        assistant_note: '',
        job_id: null,
      },
      project: undefined,
      previewId: null,
      strategies: ['balanced'],
    })

    expect(payload?.deck_plan?.brief).toMatchObject({
      purpose: 'Итоги года',
      audience: 'Совет директоров',
      key_message: 'подтвердить рост выручки',
      call_to_action: 'утвердить бюджет',
    })
  })

  it('builds POST /generations payload with inline deck_plan, preview_id and chosen variants', () => {
    const single = buildGenerationPayload({
      stored: null,
      plan: deckPlan,
      session: {
        step: 5,
        completed_steps: [1, 2, 3, 4],
        skipped_steps: [],
        artifacts: {
          template_id: 'tpl_1',
          content_pack_id: 'pack_1',
          brandbook_id: 'brand_1',
          plan_id: 'plan_1',
          preview_id: 'prev_1',
          generation_id: null,
        },
        brief: { purpose: 'Итоги года', audience: 'Совет директоров', language: 'ru', target_slide_count: 8 },
        assistant_note: '',
        job_id: null,
      },
      project: undefined,
      previewId: 'prev_1',
      strategies: ['visual'],
    })

    expect(single).toMatchObject({
      template_id: 'tpl_1',
      content_pack_id: 'pack_52549aa7639543a8b68b55c5f60d42a8',
      preview_id: 'prev_1',
      brandbook_id: 'brand_1',
      variants: [{ strategy: 'visual' }],
      use_llm: false,
    })
    expect(single?.deck_plan?.id).toBe(deckPlan.id)
    expect(single).not.toHaveProperty('deck_plan_id')

    const fastPayload = buildGenerationPayload({
      stored: {
        request: {
          template_id: 'tpl_1',
          content_pack_id: 'pack_1',
          brief: { purpose: 'Итоги', audience: '', language: 'ru', target_slide_count: 8 },
          use_llm: true,
        },
        result: { planId: 'plan_1', proposals: [], sufficiency: null, research: null },
        createdAt: '2026-10-18T10:00:00Z',
      },
      plan: deckPlan,
      session: null,
      project: undefined,
      previewId: 'prev_1',
      strategies: ['balanced'],
      executionMode: 'fast',
    })
    expect(fastPayload?.execution_mode).toBe('fast')
    expect(fastPayload?.use_llm).toBe(false)
  })
  it('keeps checked generation enabled when a restored request had no provider flag', () => {
    const options = {
      stored: null,
      plan: deckPlan,
      session: null,
      project: { template_id: 'tpl_1', content_pack_id: 'pack_1' } as never,
      previewId: null,
      strategies: ['balanced'] as const,
      modelAvailable: true,
    }
    expect(buildGenerationPayload({ ...options, executionMode: 'checked' })?.use_llm).toBe(true)
    expect(buildGenerationPayload({ ...options, executionMode: 'fast' })?.use_llm).toBe(false)
  })

})

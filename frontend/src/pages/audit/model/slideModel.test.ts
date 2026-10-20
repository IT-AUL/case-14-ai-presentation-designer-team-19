import { describe, expect, it } from 'vitest'
import type { RegenerateResult, SlideExplain } from '@/entities/assistant'
import type { DeckPlan, ResearchSource } from '@/entities/generation'
import type { SlideInfo, VariantSummary } from '@/entities/variant'
import {
  buildExplainView,
  buildReworkChips,
  buildSlideChartViewFromExplain,
  buildSlideNotesView,
  chartTypeLabel,
  currentRevisionOf,
  defaultPolishVariant,
  exportFileName,
  findPlanSlide,
  findSlideAt,
  formatAuditMode,
  formatAuditStatus,
  formatExemplarLabel,
  formatPlannerName,
  openCriticalSummary,
  parseSlideParam,
  resolvePolishNote,
  taskLabel,
} from './slideModel'

const makeVariant = (id: string, strategy: VariantSummary['strategy'], status: VariantSummary['status'] = 'completed'): VariantSummary => ({
  id,
  run_id: 'run-1',
  strategy,
  status,
  rationale: null,
  deck_artifact_id: 'art-deck',
  montage_artifact_id: null,
  metrics: { validity: 1, editability_pei: 5, issues_total: 2, style_fidelity: 0.94 },
  audit_status: 'completed',
  export_ids: [],
  planner: null,
  contextual_issues: null,
  deck_plan_id: 'plan-1',
})

describe('polishModel', () => {
  it('picks the balanced completed variant by default', () => {
    const variants = [
      makeVariant('v-faith', 'faithful'),
      makeVariant('v-bal', 'balanced'),
      makeVariant('v-vis', 'visual'),
    ]
    expect(defaultPolishVariant(variants)?.id).toBe('v-bal')
  })

  it('parses valid slide query parameters', () => {
    expect(parseSlideParam('3')).toBe(3)
    expect(parseSlideParam('0')).toBeNull()
    expect(parseSlideParam('-1')).toBeNull()
    expect(parseSlideParam('abc')).toBeNull()
    expect(parseSlideParam(null)).toBeNull()
  })

  it('finds slides for both 0-based and 1-based indices and merges regenerated state', () => {
    const zeroBased: SlideInfo[] = [
      { id: 's0', variant_id: 'v1', index: 0, slide_plan_id: 'slide-0', purpose: 'title', title: 'Обложка', revision: 1, preview_artifact_id: null },
      { id: 's1', variant_id: 'v1', index: 1, slide_plan_id: 'slide-1', purpose: 'data', title: 'Выручка', revision: 1, preview_artifact_id: 'art-1' },
    ]
    expect(findSlideAt(zeroBased, 1)?.id).toBe('s0')
    expect(findSlideAt(zeroBased, 2)?.id).toBe('s1')

    const oneBased: SlideInfo[] = [
      { id: 's10', variant_id: 'v1', index: 1, slide_plan_id: 'slide-0', purpose: 'title', title: 'Обложка', revision: 1, preview_artifact_id: 'art-a' },
      { id: 's20', variant_id: 'v1', index: 2, slide_plan_id: 'slide-1', purpose: 'data', title: 'Выручка', revision: 1, preview_artifact_id: 'art-b' },
    ]
    const regenMap = new Map<string, RegenerateResult>([
      [
        's20',
        {
          variant_id: 'v1',
          slide: { id: 's20', variant_id: 'v1', index: 2, slide_plan_id: 'slide-1', purpose: 'data', title: 'Выручка (короче)', revision: 3, preview_artifact_id: 'art-new' },
          deck_revision: 3,
          deck_artifact_id: 'art-deck-3',
          exemplar: 'ppt/slides/slide24.xml',
          preview_artifact_id: 'art-new',
          issues: [],
          assistant_note: 'Сделал слайд короче.',
        },
      ],
    ])
    expect(findSlideAt(oneBased, 1, regenMap)?.id).toBe('s10')
    const merged = findSlideAt(oneBased, 2, regenMap)
    expect(merged?.revision).toBe(3)
    expect(merged?.preview_artifact_id).toBe('art-new')
    expect(merged?.title).toBe('Выручка (короче)')
  })

  it('formats exemplar labels and chart/task labels in Russian', () => {
    expect(formatExemplarLabel('ppt/slides/slide24.xml')).toBe('Образец шаблона: слайд #24')
    expect(formatExemplarLabel('slide-12')).toBe('Образец шаблона: слайд #12')
    expect(formatExemplarLabel(null)).toBeNull()
    expect(chartTypeLabel('line')).toBe('Линия')
    expect(chartTypeLabel('bar_horizontal')).toBe('Горизонтальные столбцы')
    expect(taskLabel('trend', 'динамика')).toBe('Динамика во времени')
    expect(taskLabel('custom_task', 'структура каналов')).toBe('Структура каналов')
  })

  it('builds explain view from GET /slides/{id}/explain for a chart slide', () => {
    const explain: SlideExplain = {
      slide_id: 's2',
      slide_plan_id: 'slide-2',
      title: 'Выручка по месяцам',
      dataset: { id: 'ds-1', title: 'Purchases', file: 'purchases.csv', rows: 8755 },
      profile_summary: '8755 строк; 6 колонок; период 24 мес.',
      task: 'trend',
      task_ru: 'динамика',
      aggregation: 'сумма по месяцам',
      chosen: { type: 'line', reasons: ['24 точки по месяцам', 'одна метрика'] },
      alternatives: [
        { type: 'bar', reasons: ['24 периода столбцами'] },
        { type: 'table', reasons: ['точные числа'] },
      ],
      rejected: [{ type: 'pie', reasons: ['доли не для ряда времени'] }],
      insights: [{ id: 'i1', fact: 'Выручка выросла в 2,1 раза' }],
      source: 'Источник: purchases.csv',
      assistant_note: 'Линия: 24 точки по месяцам.',
    }

    const view = buildExplainView(explain, undefined, undefined, 'ppt/slides/slide24.xml')
    expect(view.hasChart).toBe(true)
    expect(view.datasetLabel).toContain('purchases.csv')
    expect(view.chosenLabel).toBe('Линия')
    expect(view.taskText).toBe('Динамика во времени')
    expect(view.alternatives.map((item) => item.type)).toEqual(['bar', 'table'])
    expect(view.rejected[0]?.label).toBe('Круговая')
    expect(view.insights[0]?.text).toBe('Выручка выросла в 2,1 раза')
    expect(view.exemplarLabel).toBe('Образец шаблона: слайд #24')
  })

  it('falls back to planSlide.chart when explain endpoint is unavailable', () => {
    const plan = {
      schema_version: '1.0',
      id: 'plan-1',
      brief: { purpose: 'product', audience: '', language: 'ru', target_slide_count: 4 },
      evidence_graph_id: 'eg-1',
      objective: 'product',
      audience: '',
      language: 'ru',
      sections: [],
      provenance: { planner: 'deterministic', model_id: null, prompt_version: '1.0' },
      slides: [
        {
          id: 'slide-1',
          index: 1,
          purpose: 'data',
          title_intent: 'Категории',
          key_message: 'Электроника — 61%',
          evidence_ids: ['pack:ds-1'],
          content_units: [],
          chart: {
            type: 'bar_horizontal',
            reasons: ['категории по убыванию'],
            alternatives: [{ type: 'pie', reasons: ['доли одного целого'] }],
            why: 'Горизонтальные столбцы для ранжирования.',
          },
        },
      ],
    } as unknown as DeckPlan

    const planSlide = findPlanSlide(plan, undefined, 2)
    const view = buildExplainView(null, planSlide, undefined, null)
    expect(view.hasChart).toBe(true)
    expect(view.chosenLabel).toBe('Горизонтальные столбцы')
    expect(view.alternatives.map((alt) => alt.label)).toEqual(['Круговая'])

    const chartView = buildSlideChartViewFromExplain(view, planSlide)
    expect(chartView).not.toBeNull()
    expect(chartView?.type).toBe('bar_horizontal')
    expect(chartView?.options.map((opt) => opt.type)).toEqual(['bar_horizontal', 'pie', 'bar'])
  })

  it('infers chart explanation for a data slide when both explain endpoint and planSlide.chart are missing', () => {
    const planSlide = {
      id: 'slide-4',
      index: 3,
      purpose: 'data',
      title_intent: 'География поставок',
      key_message: 'Москва — 52%',
      evidence_ids: ['pack:ds-1'],
      content_units: [
        { role: 'chart', kind: 'chart', text: 'Регионы', chart_spec: { chart_type: 'bar', series_count: 1 } },
      ],
      chart: null,
    } as unknown as DeckPlan['slides'][number]

    const view = buildExplainView(null, planSlide, undefined, null)
    expect(view.hasChart).toBe(true)
    expect(view.chosenType).toBe('bar')
    expect(view.chosenLabel).toBe('Столбцы')
    expect(view.alternatives.length).toBeGreaterThanOrEqual(2)
  })

  it('builds rework chips including chart alternatives and non-current styles', () => {
    const chips = buildReworkChips(
      'balanced',
      [
        { type: 'bar', label: 'Столбцы', reason: '24 периода' },
        { type: 'table', label: 'Таблица', reason: 'точные значения' },
      ],
      true,
    )
    expect(chips.map((chip) => chip.key)).toEqual([
      'other_layout',
      'shorter',
      'other_chart:bar',
      'other_chart:table',
      'style:faithful',
      'style:visual',
    ])
  })

  it('builds speaker notes and provenance badges for data, web, and locked units', () => {
    const planSlide = {
      id: 'slide-2',
      index: 2,
      purpose: 'data',
      title_intent: 'Выручка',
      key_message: 'Рост в 2,1 раза',
      speaker_note: 'Пояснение для совета директоров по 24 месяцам.',
      evidence_ids: ['pack_1:ds-1', 'pack_1:sec-1'],
      content_units: [
        { role: 'bullet', kind: 'bullet', text: 'Первый пункт', evidence_ids: [], locked: true, origin: 'user' },
        { role: 'bullet', kind: 'bullet', text: 'Факт из отчёта', evidence_ids: [], locked: false, origin: 'web', source_id: 'src-1' },
      ],
    } as unknown as DeckPlan['slides'][number]

    const explainView = buildExplainView(
      {
        slide_id: 's2',
        slide_plan_id: 'slide-2',
        title: 'Выручка',
        dataset: { id: 'ds-1', title: 'Purchases', file: 'purchases.csv', rows: 240 },
        profile_summary: null,
        task: 'trend',
        chosen: { type: 'line', reasons: [] },
        alternatives: [],
        rejected: [],
        insights: [],
        assistant_note: null,
      },
      planSlide,
      undefined,
      null,
    )

    const headings = new Map([['sec-1', 'Финансовый обзор']])
    const sources: ResearchSource[] = [
      { id: 'src-1', title: 'Обзор рынка 2025', url: 'https://example.com/report', site: 'example.com', accessedAt: null, facts: 2 },
    ]
    const notes = buildSlideNotesView(planSlide, explainView, headings, sources)
    expect(notes.speakerNote).toBe('Пояснение для совета директоров по 24 месяцам.')
    expect(notes.origins.map((item) => item.kind)).toEqual(['data', 'web', 'locked'])
    expect(notes.sources.map((item) => item.label)).toContain('Финансовый обзор')
  })

  it('drops technical dump sentences from speaker notes and hides artifact ids in sources', () => {
    const planSlide = {
      id: 'slide-2',
      index: 1,
      purpose: 'data',
      title_intent: 'Выручка',
      key_message: 'Рост в 2,1 раза',
      speaker_note: 'Данные: sales.csv (24 строки; 3 колонки). Задача: динамика продаж. Почему линия: тренд по месяцам. Альтернативы: bar. Скажите про рост вдвое за год.',
      evidence_ids: ['pack_1:sec-9'],
      content_units: [],
    } as unknown as DeckPlan['slides'][number]
    const headings = new Map([['sec-9', 'Art 747cd24355d54ffdbfc765e98296e5a9']])
    const notes = buildSlideNotesView(planSlide, buildExplainView(null, planSlide, undefined, null), headings, [])
    expect(notes.speakerNote).toBe('Скажите про рост вдвое за год.')
    expect(notes.sources.map((item) => item.label)).toEqual(['ваши материалы'])
  })

  it('returns no speaker note when the note is only a technical dump', () => {
    const planSlide = {
      id: 'slide-3',
      index: 2,
      purpose: 'data',
      title_intent: 'Метрики',
      key_message: 'Три вывода',
      speaker_note: 'Данные: metrics.csv (12 строк; 2 колонки). Задача: сравнение. Почему bar: категории.',
      evidence_ids: [],
      content_units: [],
    } as unknown as DeckPlan['slides'][number]
    const notes = buildSlideNotesView(planSlide, buildExplainView(null, planSlide, undefined, null), new Map(), [])
    expect(notes.speakerNote).toBeNull()
  })

  it('resolves assistant note by priority and filters out forbidden words', () => {
    expect(
      resolvePolishNote({
        currentSlide: 3,
        regeneratingSlideNumber: 3,
        repairPending: false,
        autopilotPending: false,
        actionNote: 'Предыдущая заметка',
        explainNote: null,
        hasChart: false,
        sessionNote: null,
        slideOpenIssues: 1,
        totalOpenIssues: 2,
      }),
    ).toBe('Пересобираю только слайд №03 — остальные слайды не трогаю…')

    expect(
      resolvePolishNote({
        currentSlide: 2,
        regeneratingSlideNumber: null,
        repairPending: false,
        autopilotPending: false,
        actionNote: 'Нейросеть сгенерировала слайд',
        explainNote: null,
        hasChart: false,
        sessionNote: null,
        slideOpenIssues: 0,
        totalOpenIssues: 0,
      }),
    ).toBe('Колода готова: все графики редактируемые, критичных ошибок вёрстки нет — можно скачивать или точечно поправить любой слайд.')
  })

  it('computes export file names from project name and style, revisions, and open critical counts', () => {
    expect(exportFileName('balanced', 'pptx', 1, null, 'Квартальный отчёт')).toBe('квартальный-отчёт-сбалансированный.pptx')
    expect(exportFileName('balanced', 'pptx', 2)).toBe('презентация-сбалансированный-r2.pptx')
    expect(exportFileName('visual', 'html', 1, 'text/html', 'Закупки Q3')).toBe('закупки-q3-визуальный.html')
    expect(exportFileName('visual', 'html', 1, 'application/zip', 'Закупки Q3')).toBe('закупки-q3-визуальный-html.zip')
    expect(formatAuditMode('standard')).toBe('Стандартный')
    expect(formatAuditStatus('passed')).toBe('Пройдено')
    expect(formatPlannerName('story-director/deterministic-0.1.0')).toBe('По правилам')
    expect(currentRevisionOf(1, [{ format: 'pdf', artifactId: 'a1', exportId: 'e1', deckRevision: 2, sizeBytes: 100, sha256: null, mimeType: null }], 3)).toBe(3)
    expect(
      openCriticalSummary(undefined, {
        summary_by_severity: { blocker: 1, error: 4, warning: 2, info: 0 },
      }),
    ).toEqual({ blockers: 1, errors: 4 })
  })
})

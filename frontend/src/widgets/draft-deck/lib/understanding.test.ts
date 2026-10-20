import type { Insight, Understanding, UnderstandingBlock, UnderstandingDataset } from '@/entities/assistant'
import type { ContentPack } from '@/entities/content-pack'
import {
  applyOptimisticBlockOps,
  applyOptimisticInsightPatch,
  applyOptimisticNotes,
  blockOrigin,
  buildSparkGeometry,
  chartTypeLabel,
  deduplicateTextBlocks,
  extractContextBadges,
  extractFigure,
  fallbackUnderstandingFromContentPack,
  formatCapacitySlides,
  formatSourceDetails,
  isInsightDefaultIncluded,
  isInsightRecommendation,
  normalizeBlockKind,
  resolveUnderstandingNote,
  sanitizeAssistantCopy,
} from './understanding'

function block(patch: Partial<UnderstandingBlock> = {}): UnderstandingBlock {
  return {
    id: 'b:s1:0',
    kind: 'fact',
    title: null,
    text: 'Текст блока',
    source_ref: null,
    dataset_id: null,
    preview: null,
    ...patch,
  }
}

function insight(patch: Partial<Insight> = {}): Insight {
  return {
    id: 'i-1',
    dataset_id: 'ds-1',
    kind: 'fact',
    fact: 'Выручка выросла в 2,1 раза',
    headline: null,
    meaning: null,
    implication: null,
    strength: 0.9,
    confirmed: false,
    needs_confirmation: false,
    evidence: [],
    numbers: [],
    ...patch,
  }
}

function dataset(patch: Partial<UnderstandingDataset> = {}): UnderstandingDataset {
  return {
    id: 'ds-1',
    title: 'Продажи',
    file: 'purchases.csv',
    profile_summary: '240 строк, 6 колонок',
    task: 'trend',
    task_ru: 'Динамика',
    recommendations: [],
    why: null,
    insights: [insight()],
    ...patch,
  }
}

function understanding(patch: Partial<Understanding> = {}): Understanding {
  return {
    content_pack_id: 'pack-1',
    blocks: [block()],
    summary: { capacity_slides: 6, main_points: ['Рост выручки'], assistant_note: null },
    datasets: [dataset()],
    brief: {},
    context: null,
    interpretation: null,
    assistant_note: null,
    ...patch,
  }
}

describe('sanitizeAssistantCopy', () => {
  it('rewrites forbidden wording in backend copy', () => {
    expect(sanitizeAssistantCopy('Дополнено из знаний модели и генерацией текста')).toBe('Дополнено общими сведениями и сборкой текста')
  })

  it('keeps regular copy untouched', () => {
    expect(sanitizeAssistantCopy('Москва даёт 41% выручки')).toBe('Москва даёт 41% выручки')
  })
})

describe('normalizeBlockKind', () => {
  it('passes known kinds through and buckets the rest as other', () => {
    expect(normalizeBlockKind('topic')).toBe('topic')
    expect(normalizeBlockKind('number')).toBe('number')
    expect(normalizeBlockKind('chart')).toBe('other')
    expect(normalizeBlockKind(null)).toBe('other')
  })
})

describe('chartTypeLabel', () => {
  it('maps known chart types to Russian labels', () => {
    expect(chartTypeLabel('bar_stacked')).toBe('Накопительные столбцы')
    expect(chartTypeLabel('line')).toBe('Линейный график')
    expect(chartTypeLabel('something_new')).toBe('something_new')
    expect(chartTypeLabel(null)).toBe('График')
  })
})

describe('formatCapacitySlides', () => {
  it('describes capacity with pluralized slides', () => {
    expect(formatCapacitySlides(1)).toBe('Материала примерно на 1 слайд')
    expect(formatCapacitySlides(6)).toBe('Материала примерно на 6 слайдов')
  })

  it('admits unknown capacity', () => {
    expect(formatCapacitySlides(0)).toBe('Объём определим на следующем шаге')
    expect(formatCapacitySlides(null)).toBe('Объём определим на следующем шаге')
  })
})

describe('blockOrigin', () => {
  const datasets = [dataset()]

  it('marks dataset blocks as user data with the file name', () => {
    const origin = blockOrigin(block({ kind: 'data', dataset_id: 'ds-1' }), datasets)
    expect(origin.kind).toBe('data')
    expect(origin.label).toBe('ваши данные')
    expect(origin.detail).toBe('purchases.csv')
  })

  it('marks web sources with their site and url', () => {
    const origin = blockOrigin(block({ source_ref: { origin: 'web', url: 'https://rbc.ru/x', site: 'rbc.ru' } }))
    expect(origin.kind).toBe('web')
    expect(origin.label).toBe('rbc.ru')
    expect(origin.url).toBe('https://rbc.ru/x')
  })

  it('marks model-filled content as unverified general info', () => {
    const origin = blockOrigin(block({ source_ref: { origin: 'model' } }))
    expect(origin.kind).toBe('model')
    expect(origin.label).toBe('общие сведения')
    expect(origin.tone).toBe('warn')
  })

  it('marks author notes as user thesis', () => {
    const origin = blockOrigin(block({ source_ref: { artifact_id: 'user:notes' } }))
    expect(origin.label).toBe('ваш тезис')
  })

  it('defaults to user text with a heading path when present', () => {
    const origin = blockOrigin(block({ source_ref: { heading_path: ['Отчёт', 'Итоги'] } }))
    expect(origin.kind).toBe('user')
    expect(origin.detail).toBe('Отчёт / Итоги')
  })
})

describe('formatSourceDetails', () => {
  it('collects dataset file and quote details', () => {
    const lines = formatSourceDetails(
      block({ dataset_id: 'ds-1', source_ref: { quote: 'рост вдвое', sheet: 'Лист1' } }),
      [dataset()],
    )
    expect(lines).toContain('Файл: purchases.csv')
    expect(lines).toContain('Лист: Лист1')
    expect(lines).toContain('Цитата: «рост вдвое»')
  })

  it('falls back to the block text when no structured source exists', () => {
    const lines = formatSourceDetails(block({ text: 'Исходный текст' }))
    expect(lines).toEqual(['Исходный фрагмент: Исходный текст'])
  })
})

describe('extractContextBadges', () => {
  it('maps known context fields to labelled badges', () => {
    const badges = extractContextBadges({
      fields: [
        { field: 'audience', value: 'совет директоров', quote: 'для совета директоров' },
        { field: 'tone', value: 'деловой' },
        { field: 'custom', value: 'х' },
        { field: 'empty', value: ' ' },
      ],
    })
    expect(badges).toHaveLength(3)
    expect(badges[0]).toMatchObject({ field: 'audience', label: 'Аудитория', value: 'совет директоров', quote: 'для совета директоров' })
    expect(badges[2]?.label).toBe('custom')
  })

  it('returns empty list for missing context', () => {
    expect(extractContextBadges(null)).toEqual([])
    expect(extractContextBadges({})).toEqual([])
  })
})

describe('insight inclusion rules', () => {
  it('includes strong plain facts by default', () => {
    expect(isInsightDefaultIncluded(insight({ strength: 0.8 }))).toBe(true)
  })

  it('does not include weak facts automatically', () => {
    expect(isInsightDefaultIncluded(insight({ strength: 0.4 }))).toBe(false)
  })

  it('never auto-includes recommendations or implications', () => {
    expect(isInsightDefaultIncluded(insight({ strength: 0.95, implication: 'Увеличить бюджет' }))).toBe(false)
    expect(isInsightDefaultIncluded(insight({ strength: 0.95, kind: 'recommendation' }))).toBe(false)
    expect(isInsightDefaultIncluded(insight({ strength: 0.95, needs_confirmation: true }))).toBe(false)
  })

  it('treats needs_confirmation as a recommendation signal', () => {
    expect(isInsightRecommendation(insight({ needs_confirmation: true }))).toBe(true)
    expect(isInsightRecommendation(insight({ implication: 'x' }))).toBe(true)
    expect(isInsightRecommendation(insight())).toBe(false)
  })
})

describe('applyOptimisticBlockOps', () => {
  const base = understanding({
    blocks: [
      block({ id: 's:a', kind: 'topic', title: 'Раздел А' }),
      block({ id: 'b:a:0', text: 'Первый' }),
      block({ id: 'b:a:1', text: 'Второй' }),
    ],
  })

  it('removes a block', () => {
    const next = applyOptimisticBlockOps(base, [{ op: 'remove', block_id: 'b:a:0' }])
    expect(next.blocks.map((item) => item.id)).toEqual(['s:a', 'b:a:1'])
  })

  it('moves a block to a clamped target index', () => {
    const next = applyOptimisticBlockOps(base, [{ op: 'move', block_id: 's:a', index: 99 }])
    expect(next.blocks.map((item) => item.id)).toEqual(['b:a:0', 'b:a:1', 's:a'])
  })

  it('edits a block title and text', () => {
    const next = applyOptimisticBlockOps(base, [{ op: 'edit', block_id: 's:a', title: 'Новый раздел' }])
    expect(next.blocks[0]?.title).toBe('Новый раздел')
  })

  it('merges a block into another one', () => {
    const next = applyOptimisticBlockOps(base, [{ op: 'merge', block_id: 'b:a:1', into: 'b:a:0' }])
    expect(next.blocks.map((item) => item.id)).toEqual(['s:a', 'b:a:0'])
    expect(next.blocks[1]?.text).toBe('Первый\nВторой')
  })

  it('ignores operations on unknown blocks', () => {
    const next = applyOptimisticBlockOps(base, [{ op: 'remove', block_id: 'nope' }])
    expect(next.blocks).toHaveLength(3)
  })
})

describe('applyOptimisticInsightPatch', () => {
  it('confirms a recommendation and clears the pending flag', () => {
    const state = understanding({
      datasets: [dataset({ insights: [insight({ implication: 'Сместить фокус', needs_confirmation: true })] })],
    })
    const next = applyOptimisticInsightPatch(state, 'i-1', { confirmed: true })
    const patched = next.datasets[0]?.insights[0]
    expect(patched?.confirmed).toBe(true)
    expect(patched?.needs_confirmation).toBe(false)
  })

  it('applies an edited wording as the headline and marks it confirmed', () => {
    const state = understanding()
    const next = applyOptimisticInsightPatch(state, 'i-1', { text: '  Новая формулировка  ', meaning: 'Смысл' })
    const patched = next.datasets[0]?.insights[0]
    expect(patched?.headline).toBe('Новая формулировка')
    expect(patched?.meaning).toBe('Смысл')
    expect(patched?.confirmed).toBe(true)
  })
})

describe('applyOptimisticNotes', () => {
  it('appends author thesis blocks and a summary point', () => {
    const next = applyOptimisticNotes(understanding(), ['  Первый тезис ', ''])
    const added = next.blocks.slice(1)
    expect(added.map((item) => item.kind)).toEqual(['topic', 'fact'])
    expect(added[1]?.text).toBe('Первый тезис')
    expect(next.summary.main_points).toContain('Тезисы автора')
  })

  it('returns the same understanding when notes are empty', () => {
    const state = understanding()
    expect(applyOptimisticNotes(state, [' ', ''])).toBe(state)
  })
})

describe('resolveUnderstandingNote', () => {
  it('prefers the local feedback note', () => {
    const state = understanding({ assistant_note: 'Заметка от сервера' })
    expect(resolveUnderstandingNote(state, 'Заметка сессии', 'Убрал блок')).toBe('Убрал блок')
  })

  it('explains pending interpretation', () => {
    const state = understanding({ interpretation: { status: 'pending' }, assistant_note: 'Сервер' })
    expect(resolveUnderstandingNote(state, null, null)).toContain('дописываю краткие пояснения')
  })

  it('falls back to the backend assistant note', () => {
    const state = understanding({ assistant_note: 'Нашёл 4 сюжета' })
    expect(resolveUnderstandingNote(state, null, null)).toBe('Нашёл 4 сюжета')
  })

  it('warns about scarce material', () => {
    const state = understanding({ datasets: [], summary: { capacity_slides: 2, main_points: [], assistant_note: null } })
    expect(resolveUnderstandingNote(state, null, null)).toContain('Материала пока на пару слайдов')
  })
  it('replaces stale Материалы в 1 части note with honest counts', () => {
    const state = understanding({ assistant_note: 'Материалы в 1 части.' })
    const note = resolveUnderstandingNote(state, null, null)
    expect(note).toContain('1 таблица')
    expect(note).toContain('1 текстовый блок')
    expect(note).toContain('1 числовой показатель')
  })
})

describe('buildSparkGeometry', () => {
  it('builds a line path with a delta caption', () => {
    const geo = buildSparkGeometry({
      chart_type: 'line',
      categories: ['Янв', 'Фев', 'Мар'],
      series: [{ name: 'Выручка', values: [10, 15, 30] }],
      unit: 'млн ₽',
    })
    expect(geo.chartType).toBe('line')
    expect(geo.linePath).toMatch(/^M/)
    expect(geo.points).toHaveLength(3)
    expect(geo.deltaText).toBe('×3,0')
    expect(geo.peakPoint?.value).toBe(30)
  })

  it('builds bars for bar charts', () => {
    const geo = buildSparkGeometry({
      chart_type: 'bar',
      categories: ['А', 'Б'],
      series: [{ name: null, values: [5, 10] }],
      unit: null,
    })
    expect(geo.chartType).toBe('bar')
    expect(geo.bars).toHaveLength(2)
    expect(geo.bars[1]?.isPeak).toBe(true)
    expect(geo.deltaText).toBe('×2,0')
  })

  it('builds scatter geometry with points and a trend line path', () => {
    const geo = buildSparkGeometry({
      chart_type: 'scatter',
      categories: ['100', '200', '300', '400'],
      series: [{ name: 'Продажи', values: [120, 230, 340, 460] }],
      unit: 'тыс. ₽',
    })
    expect(geo.chartType).toBe('scatter')
    expect(geo.points).toHaveLength(4)
    expect(geo.trendLinePath).toMatch(/^M[\d.]+,[\d.]+ L[\d.]+,[\d.]+$/)
  })

  it('builds histogram geometry with vertical bin bars', () => {
    const geo = buildSparkGeometry({
      chart_type: 'histogram',
      categories: ['0–200', '200–400', '400–600', '600–800'],
      series: [{ name: 'Города', values: [4, 18, 12, 6] }],
      unit: null,
    })
    expect(geo.chartType).toBe('histogram')
    expect(geo.bars).toHaveLength(4)
    expect(geo.bars[1]?.isPeak).toBe(true)
  })

  it('returns an empty geometry without values', () => {
    const geo = buildSparkGeometry({ chart_type: 'line', categories: [], series: [], unit: null })
    expect(geo.values).toEqual([])
    expect(geo.linePath).toBe('')
    expect(geo.trendLinePath).toBe('')
    expect(geo.points).toEqual([])
  })
})

describe('extractFigure', () => {
  it('keeps every thousands group when extracting a currency amount', () => {
    expect(extractFigure('Медиана 1\u00a0140 ₽')).toBe('1\u00a0140 ₽')
    expect(extractFigure('Максимум 6\u202f860 ₽')).toBe('6\u202f860 ₽')
    expect(extractFigure('Объём 1 234 567,89 €')).toBe('1 234 567,89 €')
  })

  it('preserves currency symbols after Russian abbreviations', () => {
    expect(extractFigure('Инвестиции составили 280 млн ₽ за год')).toBe('280 млн ₽')
    expect(extractFigure('Объём рынка достиг 1,2 млрд $')).toBe('1,2 млрд $')
    expect(extractFigure('Средний чек вырос до 45 тыс. €')).toBe('45 тыс. €')
    expect(extractFigure('Расходы на рекламу: типичное значение 421 тыс. ₽')).toBe('421 тыс. ₽')
  })

  it('extracts multipliers and percentage shares', () => {
    expect(extractFigure('Рост выручки в 2,1 раза')).toBe('×2,1')
    expect(extractFigure('Доля канала 61% выручки')).toBe('61%')
    expect(extractFigure('Без числовых показателей')).toBeNull()
  })
})

describe('deduplicateTextBlocks', () => {
  it('deduplicates twin blocks by normalized (heading, summary) ignoring case and extra whitespace', () => {
    const blocks = [
      block({ id: 's:sec-1', kind: 'topic', title: 'Итоги маркетинга', text: 'Рост продаж на  280 млн ₽\nза квартал' }),
      block({ id: 'b:sec-1:0', kind: 'number', title: '  итоги   маркетинга ', text: 'рост продаж на 280 млн ₽ за квартал' }),
      block({ id: 'b:sec-2:0', kind: 'fact', title: 'Планы Q4', text: 'Перераспределить бюджет в пользу 12 городов' }),
    ]
    const deduped = deduplicateTextBlocks(blocks)
    expect(deduped).toHaveLength(2)
    expect(deduped[0]?.id).toBe('b:sec-1:0')
    expect(deduped[1]?.id).toBe('b:sec-2:0')
  })

  it('merges same-heading blocks whose summaries overlap, keeping the fuller text', () => {
    const blocks = [
      block({ id: 'b:1', kind: 'fact', title: 'Проблема', text: 'Продажи падают' }),
      block({ id: 'b:2', kind: 'fact', title: 'Проблема', text: 'Продажи падают второй квартал подряд: -18% к прошлому году' }),
      block({ id: 'b:3', kind: 'fact', title: 'Проблема', text: 'Продажи падают' }),
      block({ id: 'b:4', kind: 'fact', title: 'Решение', text: 'Запустить программу удержания в Q1' }),
    ]
    const deduped = deduplicateTextBlocks(blocks)
    expect(deduped).toHaveLength(2)
    expect(deduped[0]?.id).toBe('b:2')
    expect(deduped[1]?.id).toBe('b:4')
  })

  it('keeps a section summary card next to its detail cards', () => {
    const blocks = [
      block({ id: 's:sec-1', kind: 'topic', title: 'Итоги', text: 'Рост на 20% Пункт без цифр' }),
      block({ id: 'b:sec-1:0', kind: 'number', title: 'Итоги', text: 'Рост на 20%' }),
    ]
    const deduped = deduplicateTextBlocks(blocks)
    expect(deduped).toHaveLength(2)
    expect(deduped[0]?.id).toBe('s:sec-1')
  })

  it('keeps same-heading blocks with unrelated summaries', () => {
    const blocks = [
      block({ id: 'b:1', title: 'Выводы', text: 'Выручка выросла вдвое за год' }),
      block({ id: 'b:2', title: 'Выводы', text: 'Отток клиентов снизился на треть' }),
    ]
    expect(deduplicateTextBlocks(blocks)).toHaveLength(2)
  })
})

describe('fallbackUnderstandingFromContentPack', () => {
  it('builds understanding blocks and honest counts from pack sections and datasets', () => {
    const pack = {
      id: 'pack-x',
      datasets: [{ id: 'ds-1', title: 'Продажи', file_name: 'sales.xlsx', row_count: 120, columns: [{}, {}] }],
      sections: [
        {
          id: 'sec-1',
          heading: 'Итоги',
          blocks: [
            { text: 'Рост на 20%', kind: 'text', items: null, source_ref: { artifact_id: 'a-1' } },
            { items: ['Пункт без цифр'], kind: 'list', text: null, source_ref: null },
          ],
        },
      ],
    } as unknown as ContentPack
    const state = fallbackUnderstandingFromContentPack(pack)
    expect(state.content_pack_id).toBe('pack-x')
    expect(state.blocks[0]?.id).toBe('s:sec-1')
    expect(state.blocks[0]?.kind).toBe('topic')
    expect(state.blocks.some((item) => item.kind === 'number')).toBe(true)
    expect(state.summary.main_points).toEqual(['Итоги'])
    expect(state.assistant_note).toContain('1 таблица')
    expect(state.assistant_note).toContain('3 текстовых блока')
    expect(state.assistant_note).toContain('2 числовых показателя')
  })
})

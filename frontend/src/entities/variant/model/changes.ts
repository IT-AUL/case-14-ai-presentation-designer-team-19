export type SlideChangeState = 'queued' | 'running' | 'completed' | 'failed' | 'canceled' | 'cancelled'

export type SlideChangeAction =
  | 'shorter'
  | 'other_layout'
  | 'other_chart'
  | 'style:faithful'
  | 'style:balanced'
  | 'style:visual'
  | 'replace_title'
  | 'replace_text'
  | 'larger_chart'
  | 'punchier'
  | 'add_figure'
  | 'to_chart'
  | 'to_table'
  | 'split_slide'

export type SlideChangeChartType = 'bar' | 'column' | 'line' | 'pie'

export interface SlideChangeRequest {
  slide_id: string
  expected_revision: number
  action: SlideChangeAction | string
  chart_type?: SlideChangeChartType | string | null
  text?: string | null
}

export interface SlideChangeResult {
  slide?: Record<string, unknown> | null
  assistant_note?: string | null
  deck_revision?: number | null
  preview_artifact_id?: string | null
  audit?: Record<string, unknown> | null
  metrics?: Record<string, unknown> | null
}

export interface SlideChangeErrorObject {
  code?: string
  message?: string
}

export interface SlideChangeItem {
  id?: string
  change_id: string
  job_id?: string | null
  variant_id?: string
  slide_id: string
  slide_index?: number | null
  action: string
  chart_type?: string | null
  text?: string | null
  state: SlideChangeState
  expected_revision?: number
  base_revision: number
  target_revision: number
  created_at?: string | null
  started_at?: string | null
  completed_at?: string | null
  result?: SlideChangeResult | null
  error?: string | SlideChangeErrorObject | null
  events_url?: string | null
}

export interface VariantChangesSnapshot {
  variant_id: string
  revision?: number
  applied_revision: number
  accepted_revision: number
  items: SlideChangeItem[]
}

const ACTION_FALLBACK_NOTE: Record<string, string> = {
  shorter: 'Сделал слайд короче.',
  other_layout: 'Поменял раскладку слайда.',
  other_chart: 'Поменял график на слайде.',
  'style:faithful': 'Пересобрал слайд в строгом стиле.',
  'style:balanced': 'Пересобрал слайд в сбалансированном стиле.',
  'style:visual': 'Пересобрал слайд в визуальном стиле.',
  replace_title: 'Обновил заголовок слайда.',
  replace_text: 'Обновил текст слайда.',
  larger_chart: 'Увеличил график на слайде.',
  punchier: 'Усилил главный вывод.',
  add_figure: 'Добавил цифру из текста.',
  to_chart: 'Перестроил слайд в график.',
  to_table: 'Собрал данные в таблицу.',
  split_slide: 'Разделил слайд на два.',
}

export function isSlideChangeCanceled(state: SlideChangeState | string | null | undefined): boolean {
  return state === 'canceled' || state === 'cancelled'
}

export function isSlideChangeActive(change: SlideChangeItem | null | undefined): boolean {
  return Boolean(change && (change.state === 'queued' || change.state === 'running'))
}

export function hasPendingVariantChanges(snapshot: VariantChangesSnapshot | null | undefined): boolean {
  return Boolean(snapshot?.items.some((item) => isSlideChangeActive(item)))
}

export function normalizeSlideChangeItem(raw: Record<string, unknown>): SlideChangeItem {
  const id = typeof raw.change_id === 'string' && raw.change_id ? raw.change_id : typeof raw.id === 'string' ? raw.id : ''
  const baseRevision =
    typeof raw.base_revision === 'number'
      ? raw.base_revision
      : typeof raw.expected_revision === 'number'
        ? raw.expected_revision
        : 1
  const targetRevision = typeof raw.target_revision === 'number' ? raw.target_revision : baseRevision + 1
  const state = (typeof raw.state === 'string' ? raw.state : 'queued') as SlideChangeState

  return {
    ...(raw as unknown as SlideChangeItem),
    id,
    change_id: id,
    slide_id: typeof raw.slide_id === 'string' ? raw.slide_id : '',
    action: typeof raw.action === 'string' ? raw.action : 'shorter',
    state,
    expected_revision: baseRevision,
    base_revision: baseRevision,
    target_revision: targetRevision,
  }
}

export function normalizeVariantChangesSnapshot(
  raw: Record<string, unknown>,
  fallbackVariantId = '',
): VariantChangesSnapshot {
  const applied =
    typeof raw.applied_revision === 'number'
      ? raw.applied_revision
      : typeof raw.revision === 'number'
        ? raw.revision
        : 1
  const accepted = typeof raw.accepted_revision === 'number' ? raw.accepted_revision : applied
  const rawItems = Array.isArray(raw.items) ? raw.items : []
  const items = rawItems
    .filter((entry): entry is Record<string, unknown> => typeof entry === 'object' && entry !== null)
    .map(normalizeSlideChangeItem)

  return {
    variant_id: typeof raw.variant_id === 'string' && raw.variant_id ? raw.variant_id : fallbackVariantId,
    revision: applied,
    applied_revision: applied,
    accepted_revision: accepted,
    items,
  }
}

export function latestSlideChange(
  snapshot: VariantChangesSnapshot | null | undefined,
  slideIds: readonly (string | null | undefined)[],
): SlideChangeItem | null {
  if (!snapshot?.items.length) return null
  const validIds = new Set(slideIds.filter((id): id is string => Boolean(id && id.trim())))
  if (!validIds.size) return null

  const matching = snapshot.items
    .map((item, idx) => ({ item: normalizeSlideChangeItem(item as unknown as Record<string, unknown>), idx }))
    .filter(({ item }) => validIds.has(item.slide_id))
    .sort((a, b) => {
      if (a.item.target_revision !== b.item.target_revision) {
        return a.item.target_revision - b.item.target_revision
      }
      return a.idx - b.idx
    })
    .map(({ item }) => item)

  if (!matching.length) return null

  for (let i = matching.length - 1; i >= 0; i -= 1) {
    const candidate = matching[i]
    if (candidate && isSlideChangeActive(candidate)) return candidate
  }

  const latest = matching[matching.length - 1]
  if (!latest || isSlideChangeCanceled(latest.state)) return null
  return latest
}

export function slideChangeNote(change: SlideChangeItem | null | undefined): string | null {
  if (!change || change.state !== 'completed') return null
  const note = change.result?.assistant_note?.trim()
  if (note) return note
  return ACTION_FALLBACK_NOTE[change.action] ?? 'Пересобрал слайд в выбранном стиле.'
}

export function slideChangeErrorMessage(change: SlideChangeItem | null | undefined): string | null {
  if (!change || change.state !== 'failed') return null
  if (typeof change.error === 'string' && change.error.trim()) return change.error.trim()
  if (change.error && typeof change.error === 'object' && typeof change.error.message === 'string' && change.error.message.trim()) {
    return change.error.message.trim()
  }
  return 'Не удалось применить правку.'
}

import type { AssistantCommandResult, CommandOption, CommandStatus } from '@/entities/assistant'

export const MAX_COMMAND_HISTORY = 3

export interface CommandSlideTarget {
  id: string
  slide_plan_id?: string | null
  index?: number | null
}

export interface CommandHistoryEntry {
  id: string
  text: string
  status: CommandStatus
  summary: string
  jobId: string | null
}

const SLIDE_AFTER_WORD = /слайд[аеуоы]?\s*№?\s*(\d{1,2})/i
const SLIDE_BEFORE_WORD = /(?:на|в|со)\s+(\d{1,2})(?:-?[ймгое]+)?\s+слайд/i
const SLIDE_AT_START = /^(\d{1,2})(?:-?[ймгое]+)?\s+слайд\b/i

export function extractSlideNumber(text: string): number | null {
  const normalized = text.trim()
  if (!normalized) return null
  const match =
    SLIDE_AFTER_WORD.exec(normalized) ??
    SLIDE_BEFORE_WORD.exec(normalized) ??
    SLIDE_AT_START.exec(normalized)
  if (!match) return null
  const num = Number(match[1])
  return Number.isInteger(num) && num >= 1 && num <= 99 ? num : null
}

export function resolveCommandSlideId(
  text: string,
  slides?: readonly CommandSlideTarget[] | null,
  fallbackSlideId?: string | null,
): string | null {
  const slideNumber = extractSlideNumber(text)
  if (slideNumber !== null && slides && slides.length > 0) {
    const zeroBased = slides.some((slide) => slide.index === 0)
    const byIndex = slides.find(
      (slide) => typeof slide.index === 'number' && (zeroBased ? slide.index + 1 === slideNumber : slide.index === slideNumber),
    )
    const target = byIndex ?? slides[slideNumber - 1]
    if (target) return target.slide_plan_id || target.id
  }
  return fallbackSlideId || null
}

export function formatAcceptedSummary(result: AssistantCommandResult): string {
  const rawNote = result.assistant_note?.trim() ?? ''
  if (rawNote) {
    if (/^принял\s*:/i.test(rawNote)) return rawNote
    return `Принял: ${rawNote.charAt(0).toLowerCase()}${rawNote.slice(1)}`
  }
  const briefLabels = (result.command?.brief ?? [])
    .map((item) => item.label?.trim() || item.field.trim())
    .filter(Boolean)
  if (briefLabels.length > 0) {
    return `Принял: ${briefLabels.join('; ')}.`
  }
  const cmdText = result.command?.text?.trim()
  if (cmdText) {
    return `Принял: ${cmdText}.`
  }
  return 'Принял: изменения сохранены.'
}

export function formatClarificationQuestion(result: AssistantCommandResult): string {
  return (
    result.assistant_note?.trim() ||
    result.command?.question?.trim() ||
    'Уточните, что именно нужно поменять.'
  )
}

export function resolveClarificationOptions(
  result: AssistantCommandResult,
  context: { hasVariant: boolean; hasPlan: boolean },
): CommandOption[] {
  if (result.options.length > 0) return result.options
  const question = formatClarificationQuestion(result).toLowerCase()
  const baseText = result.command?.text?.trim() || 'Короче'
  if (/какому\s+слайду|выберите\s+слайд|укажите\s+слайд/.test(question)) {
    return [
      { value: `${baseText} на слайде 1`, label: 'Слайд 1' },
      { value: `${baseText} на слайде 2`, label: 'Слайд 2' },
      { value: `${baseText} на слайде 3`, label: 'Слайд 3' },
    ]
  }
  if (context.hasVariant) {
    return [
      { value: 'Сделай короче', label: 'Сделать короче' },
      { value: 'Другой макет', label: 'Другой макет' },
      { value: 'Переключи на таблицу', label: 'Показать таблицей' },
    ]
  }
  if (context.hasPlan) {
    return [
      { value: 'Короче на слайде 2', label: 'Короче на слайде 2' },
      { value: 'Преврати в схему слайд 2', label: 'Схема на слайде 2' },
      { value: 'Раздели слайд 2', label: 'Разделить слайд 2' },
    ]
  }
  return [
    { value: 'Аудитория — руководство', label: 'Для руководства' },
    { value: 'Тон строгий деловой', label: 'Строгий тон' },
    { value: 'Сжато и тезисно', label: 'Короче и тезиснее' },
  ]
}

export function pushCommandHistory(
  history: readonly CommandHistoryEntry[],
  entry: CommandHistoryEntry,
): CommandHistoryEntry[] {
  return [entry, ...history.filter((item) => item.id !== entry.id)].slice(0, MAX_COMMAND_HISTORY)
}

const memoryHistory = new Map<string, CommandHistoryEntry[]>()

function storageKey(projectId: string): string {
  return `deckdna:assistant-commands:${projectId}`
}

export function loadCommandHistory(projectId: string): CommandHistoryEntry[] {
  if (!projectId) return []
  try {
    const raw = typeof sessionStorage !== 'undefined' ? sessionStorage.getItem(storageKey(projectId)) : null
    if (raw) {
      const parsed: unknown = JSON.parse(raw)
      if (Array.isArray(parsed)) {
        return parsed
          .filter(
            (item): item is CommandHistoryEntry =>
              typeof item === 'object' &&
              item !== null &&
              typeof (item as CommandHistoryEntry).id === 'string' &&
              typeof (item as CommandHistoryEntry).text === 'string' &&
              typeof (item as CommandHistoryEntry).summary === 'string',
          )
          .slice(0, MAX_COMMAND_HISTORY)
      }
    }
  } catch {
    return memoryHistory.get(projectId) ?? []
  }
  return memoryHistory.get(projectId) ?? []
}

export function saveCommandHistory(projectId: string, entries: readonly CommandHistoryEntry[]): void {
  if (!projectId) return
  const trimmed = entries.slice(0, MAX_COMMAND_HISTORY)
  memoryHistory.set(projectId, trimmed)
  try {
    if (typeof sessionStorage !== 'undefined') {
      sessionStorage.setItem(storageKey(projectId), JSON.stringify(trimmed))
    }
  } catch {
    return
  }
}

export function clearCommandHistoryCache(): void {
  memoryHistory.clear()
}

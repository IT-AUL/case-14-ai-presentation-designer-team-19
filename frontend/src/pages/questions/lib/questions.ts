import type { JsonRecord, Question, QuestionOption, QuestionsResult, Understanding } from '@/entities/assistant'
import type { ContentFillMode, GenerationBrief } from '@/entities/generation'
import { pluralize } from '@/shared/lib/format'

export const MAX_QUESTIONS_PER_TURN = 2

export interface QuestionSelection {
  mode: 'option' | 'custom'
  value: string
  customText: string
}

export interface ContentShortOptionMeta {
  value: 'shrink' | 'notes' | 'expand_web' | 'expand_model'
  title: string
  description: string
  badge?: string
}

export type SpecSectionId = 'audience' | 'slides' | 'focus'

export interface DeckSpecItem {
  id: SpecSectionId
  label: string
  value: string
  note: string
  reason: string | null
  badge: string | null
  changed: boolean
  questionId: string | null
}

export interface DeckSpecOutlineRow {
  slot: string
  role: string
  title: string
  detail: string
}

export interface DeckSpecInput {
  questions: readonly Question[]
  selections: Readonly<Record<string, QuestionSelection>>
  skippedIds: ReadonlySet<string>
  suggestedBrief?: JsonRecord | null
  understanding?: Understanding | null
  thesisNotes?: readonly string[]
  fallbackSlideCount?: number
}

export interface DeckSpecModel {
  topic: string
  slideCount: number
  slideCountLabel: string
  fillModeLabel: string
  capacityLabel: string | null
  datasetLabel: string | null
  items: readonly [DeckSpecItem, DeckSpecItem, DeckSpecItem]
  outline: DeckSpecOutlineRow[]
}

export const QUESTION_TITLE: Record<string, string> = {
  audience: 'Для кого эта презентация?',
  key_message: 'Какую главную мысль вынести на обложку и держать через всю презентацию?',
  call_to_action: 'Что сделать слушателям после выступления?',
  length: 'Какой объём презентации подойдёт?',
  tone: 'Какой тон подачи выбрать?',
  content_short: 'Как поступим с объёмом материала?',
}

export const CONTENT_SHORT_META: Record<ContentShortOptionMeta['value'], ContentShortOptionMeta> = {
  shrink: {
    value: 'shrink',
    title: 'Сделать короче по материалу',
    description: 'Честно соберу презентацию только по вашим данным и фактам, без воды.',
    badge: 'советую',
  },
  notes: {
    value: 'notes',
    title: 'Я добавлю пару тезисов',
    description: 'Запишите 1–3 коротких тезиса своими словами — разверну их в слайды.',
  },
  expand_web: {
    value: 'expand_web',
    title: 'Поискать в интернете',
    description: 'Подтяну недостающие факты и статистику с прямыми ссылками на источники.',
  },
  expand_model: {
    value: 'expand_model',
    title: 'Дополнить общими знаниями',
    description: 'Помечу такие пункты плашкой «не подтверждено источником», чтобы вы проверили.',
    badge: 'не подтверждено источником',
  },
}

const AUDIENCE_OPTION_NOTES: Record<string, string> = {
  '': 'Факты и выводы по материалу без привязки к роли.',
  руководство: 'Итоги, ключевые метрики, риски и выводы.',
  'совет директоров': 'Финансовый эффект, стратегия и решения.',
  команда: 'Задачи, показатели по направлениям и шаги.',
  клиенты: 'Ценность предложения, польза и следующий шаг.',
}

const CTA_OPTION_NOTES: Record<string, string> = {
  'принять решение': 'Подведёт к выбору варианта и следующим шагам.',
  'утвердить бюджет': 'Акцент на расчёте эффекта и защите ресурсов.',
  'просто узнать': 'Главные выводы и ориентиры без запроса решения.',
}

const TONE_OPTION_NOTES: Record<string, string> = {
  деловой: 'Сдержанная подача: факты, цифры и аргументы.',
  business: 'Сдержанная подача: факты, цифры и аргументы.',
  вдохновляющий: 'Акцент на масштаб, потенциал и результат.',
  inspiring: 'Акцент на масштаб, потенциал и результат.',
  нейтральный: 'Ровный тон без оценок — только тезисы.',
  neutral: 'Ровный тон без оценок — только тезисы.',
}

const PURPOSE_LABELS: Record<string, string> = {
  presentation: 'Презентация по материалам',
  product: 'Продуктовая презентация',
  feature: 'Презентация новой функции',
  project: 'Отчёт по проекту',
  initiative: 'Защита инициативы',
}

export function sanitizeQuestionCopy(raw: string): string {
  return raw
    .replace(/из знаний модели/gi, 'общими знаниями')
    .replace(/знания модели/gi, 'общие знания')
    .replace(/нейросеть/gi, 'сервис')
    .replace(/генерацией/gi, 'сборкой')
    .replace(/генерацию/gi, 'сборку')
    .replace(/генерации/gi, 'сборки')
    .replace(/генерация/gi, 'сборка')
}

export function humanizePurpose(raw: unknown): string | null {
  if (typeof raw !== 'string') return null
  const trimmed = raw.trim()
  if (!trimmed) return null
  const mapped = PURPOSE_LABELS[trimmed.toLowerCase()]
  if (mapped) return mapped
  return sanitizeQuestionCopy(trimmed)
}

export function questionTitle(question: Pick<Question, 'id' | 'kind'>): string {
  const key = question.kind ?? question.id
  return QUESTION_TITLE[key] ?? 'Уточните параметр презентации'
}

export function isContentShortQuestion(question: Pick<Question, 'id' | 'kind'>): boolean {
  return question.kind === 'content_short' || question.id === 'content_short'
}

export function resolveOptionNote(question: Pick<Question, 'id' | 'kind'>, option: QuestionOption): string | null {
  if (option.note && option.note.trim()) {
    return sanitizeQuestionCopy(option.note.trim())
  }
  if (isContentShortQuestion(question)) {
    const meta = CONTENT_SHORT_META[option.value as ContentShortOptionMeta['value']]
    if (meta?.description) return meta.description
  }
  const kind = question.kind ?? question.id
  const key = option.value.trim().toLowerCase()
  if (kind === 'audience') {
    return AUDIENCE_OPTION_NOTES[key] ?? 'Подстрою тон, аргументы и выводы под аудиторию.'
  }
  if (kind === 'call_to_action') {
    return CTA_OPTION_NOTES[key] ?? 'Сформулирую финал вокруг этого целевого действия.'
  }
  if (kind === 'tone') {
    return TONE_OPTION_NOTES[key] ?? 'Выдержу этот тон в заголовках и тезисах слайдов.'
  }
  if (kind === 'key_message') {
    return option.recommended
      ? 'Сильнейший тезис из материалов — на обложку и в выводы.'
      : 'Сделаю эту мысль центральной осью обложки и разделов.'
  }
  if (kind === 'length') {
    return option.recommended
      ? 'Оптимально под объём исходных материалов.'
      : 'Подстрою плотность тезисов под этот объём.'
  }
  return null
}

export function normalizeContentShortOptions(options: readonly QuestionOption[]): QuestionOption[] {
  const byValue = new Map(options.map((opt) => [opt.value, opt]))
  const hasBackendRecommendation = options.some((opt) => opt.recommended)
  const order: readonly ContentShortOptionMeta['value'][] = ['shrink', 'notes', 'expand_web', 'expand_model']
  return order.map((key, idx) => {
    const existing = byValue.get(key)
    const meta = CONTENT_SHORT_META[key]
    return {
      value: key,
      label: existing ? sanitizeQuestionCopy(existing.label) : meta.title,
      recommended: existing ? existing.recommended : idx === 0 && !hasBackendRecommendation,
      note: existing?.note ? sanitizeQuestionCopy(existing.note) : meta.description,
    }
  })
}

export function visibleQuestions(questions: readonly Question[]): Question[] {
  return questions.slice(0, MAX_QUESTIONS_PER_TURN).map((question) => ({
    ...question,
    reason: question.reason ? sanitizeQuestionCopy(question.reason) : null,
    options: isContentShortQuestion(question)
      ? normalizeContentShortOptions(question.options)
      : question.options.map((opt) => ({
          ...opt,
          label: sanitizeQuestionCopy(opt.label),
          note: opt.note ? sanitizeQuestionCopy(opt.note) : null,
        })),
  }))
}

export function defaultOptionValue(question: Question): string {
  const recommended = question.options.find((option) => option.recommended)
  return recommended?.value ?? question.options[0]?.value ?? ''
}

export function initialSelections(questions: readonly Question[]): Record<string, QuestionSelection> {
  const result: Record<string, QuestionSelection> = {}
  for (const question of visibleQuestions(questions)) {
    result[question.id] = {
      mode: 'option',
      value: defaultOptionValue(question),
      customText: '',
    }
  }
  return result
}

export function buildAnswersPayload(
  questions: readonly Question[],
  selections: Readonly<Record<string, QuestionSelection>>,
  skippedIds: ReadonlySet<string>,
): Record<string, string | number> {
  const answers: Record<string, string | number> = {}
  for (const question of visibleQuestions(questions)) {
    if (skippedIds.has(question.id)) continue
    const sel = selections[question.id]
    const rawValue = sel ? (sel.mode === 'custom' ? sel.customText.trim() : sel.value) : defaultOptionValue(question)
    if (question.kind === 'length' || question.id === 'length') {
      const parsed = Number(rawValue)
      if (Number.isFinite(parsed) && parsed > 0) {
        answers[question.id] = parsed
        continue
      }
    }
    answers[question.id] = rawValue
  }
  return answers
}

export function cleanThesisNotes(notes: readonly string[]): string[] {
  return notes.map((item) => item.trim()).filter(Boolean)
}

export function normalizeAnsweredBrief(
  answeredBrief: JsonRecord | null | undefined,
  suggestedBrief: JsonRecord | null | undefined,
  fallbackLanguage = 'ru',
  fallbackSlideCount = 8,
): GenerationBrief & Record<string, unknown> {
  const merged: JsonRecord = { ...(suggestedBrief ?? {}), ...(answeredBrief ?? {}) }
  const rawCount = Number(merged.target_slide_count)
  const targetSlideCount = Number.isFinite(rawCount) && rawCount >= 3 && rawCount <= 40 ? Math.round(rawCount) : Math.max(3, Math.min(40, fallbackSlideCount))
  const rawFill = merged.content_fill
  const contentFill: 'expand' | 'shrink' | 'ask' | undefined =
    rawFill === 'shrink' || rawFill === 'expand' || rawFill === 'ask' ? rawFill : undefined

  return {
    purpose: typeof merged.purpose === 'string' && merged.purpose.trim() ? merged.purpose.trim() : 'presentation',
    audience: typeof merged.audience === 'string' ? merged.audience : '',
    language: typeof merged.language === 'string' && merged.language.trim() ? merged.language.trim() : fallbackLanguage,
    target_slide_count: targetSlideCount,
    ...(typeof merged.tone === 'string' && merged.tone.trim() ? { tone: merged.tone.trim() } : {}),
    ...(contentFill ? { content_fill: contentFill } : {}),
    ...(typeof merged.key_message === 'string' && merged.key_message.trim() ? { key_message: merged.key_message.trim() } : {}),
    ...(typeof merged.call_to_action === 'string' && merged.call_to_action.trim() ? { call_to_action: merged.call_to_action.trim() } : {}),
  }
}

export function mapShortChoiceToFillMode(choice: string | undefined): ContentFillMode | null {
  if (choice === 'shrink' || choice === 'notes') return 'keep'
  if (choice === 'expand_web' || choice === 'expand_model' || choice === 'expand') return 'expand'
  return null
}

export function formatSuggestedSummary(suggestedBrief: JsonRecord | null | undefined): string | null {
  if (!suggestedBrief) return null
  const parts: string[] = []
  const count = Number(suggestedBrief.target_slide_count)
  if (Number.isFinite(count) && count > 0) {
    parts.push(`Предложенный объём: ${count} ${pluralize(count, ['слайд', 'слайда', 'слайдов'])}`)
  }
  if (typeof suggestedBrief.audience === 'string' && suggestedBrief.audience.trim()) {
    parts.push(`Аудитория: ${suggestedBrief.audience.trim()}`)
  }
  const purpose = humanizePurpose(suggestedBrief.purpose)
  if (purpose) {
    parts.push(`Тема: ${purpose}`)
  }
  return parts.length > 0 ? parts.join(' · ') : null
}

export function resolveQuestionsNote(
  questionsResult: QuestionsResult | null | undefined,
  sessionNote: string | null | undefined,
  selectedShortValue: string | null,
  feedbackNote: string | null,
): string {
  if (feedbackNote) return sanitizeQuestionCopy(feedbackNote)
  if (selectedShortValue === 'notes') {
    return 'Запишите 1–3 коротких тезиса своими словами — я встрою их в ключевые слайды плана.'
  }
  if (questionsResult && questionsResult.questions.length === 0) {
    return 'Аудитория, объём и главная мысль уже ясны из материалов — перехожу сразу к плану.'
  }
  if (questionsResult?.assistant_note && questionsResult.assistant_note.trim()) {
    return sanitizeQuestionCopy(questionsResult.assistant_note.trim())
  }
  if (sessionNote && sessionNote.trim()) {
    return sanitizeQuestionCopy(sessionNote.trim())
  }
  return 'Пара быстрых уточнений, чтобы попасть в тон и фокус, — и сразу составлю план.'
}

function findQuestionByKind(questions: readonly Question[], kind: string): Question | undefined {
  return questions.find((q) => (q.kind ?? q.id) === kind || q.id === kind)
}

function parseFirstPositiveInt(raw: string | null | undefined): number | null {
  if (!raw) return null
  const match = /(\d+)/.exec(raw)
  if (!match) return null
  const num = Number(match[1])
  return Number.isFinite(num) && num > 0 ? num : null
}

function slidesPhrase(count: number): string {
  return `${count} ${pluralize(count, ['слайд', 'слайда', 'слайдов'])}`
}

export function buildDeckSpec({
  questions,
  selections,
  skippedIds,
  suggestedBrief,
  understanding,
  thesisNotes = [],
  fallbackSlideCount = 8,
}: DeckSpecInput): DeckSpecModel {
  const topic =
    humanizePurpose(suggestedBrief?.purpose) ??
    humanizePurpose(understanding?.brief?.purpose) ??
    'Презентация по вашим материалам'

  const audienceQ = findQuestionByKind(questions, 'audience')
  const lengthQ = findQuestionByKind(questions, 'length')
  const shortQ = questions.find(isContentShortQuestion)
  const keyMessageQ = findQuestionByKind(questions, 'key_message')
  const ctaQ = findQuestionByKind(questions, 'call_to_action')

  let audienceValue = 'Деловая аудитория'
  let audienceNote = 'Спокойная деловая подача по фактам из материалов.'
  let audienceBadge: string | null = null
  let audienceChanged = false
  let audienceRoleKey = ''

  if (audienceQ) {
    if (skippedIds.has(audienceQ.id)) {
      audienceValue = 'Нейтральный черновик'
      audienceNote = 'Вопрос пропущен — соберу универсальную подачу без привязки к роли.'
      audienceBadge = 'по умолчанию'
      audienceChanged = true
    } else {
      const sel = selections[audienceQ.id]
      const defVal = defaultOptionValue(audienceQ)
      if (sel?.mode === 'custom') {
        const customText = sel.customText.trim()
        audienceValue = customText || 'Свой вариант аудитории'
        audienceNote = customText
          ? `Подстрою аргументацию, тон и плотность выводов под аудиторию «${customText}».`
          : 'Напишите аудиторию в поле слева — подстрою тон и акценты.'
        audienceBadge = 'свой вариант'
        audienceChanged = true
        audienceRoleKey = 'custom'
      } else {
        const chosenVal = sel ? sel.value : defVal
        const opt = audienceQ.options.find((o) => o.value === chosenVal) ?? audienceQ.options[0]
        audienceValue = opt?.label ?? (chosenVal || 'Нейтральный черновик')
        audienceNote =
          (opt ? resolveOptionNote(audienceQ, opt) : null) ??
          audienceQ.reason ??
          'Определяет тон подачи и глубину деталей на слайдах.'
        audienceBadge = opt?.recommended ? 'советую' : 'выбрано'
        audienceChanged = chosenVal !== defVal
        audienceRoleKey = chosenVal.trim().toLowerCase()
      }
    }
  } else if (typeof suggestedBrief?.audience === 'string' && suggestedBrief.audience.trim()) {
    audienceValue = suggestedBrief.audience.trim()
    audienceNote = 'Аудитория определена из ваших исходных материалов.'
    audienceRoleKey = audienceValue.toLowerCase()
  }

  const rawCapacity = understanding?.summary?.capacity_slides
  const reasonCapacity = parseFirstPositiveInt(shortQ?.reason)
  const capacity =
    typeof rawCapacity === 'number' && rawCapacity > 0
      ? rawCapacity
      : reasonCapacity

  const briefSlideCount = Number(suggestedBrief?.target_slide_count)
  const lengthAnswered = Boolean(lengthQ && !skippedIds.has(lengthQ.id) && selections[lengthQ.id])
  const preferCapacity =
    !lengthAnswered &&
    typeof rawCapacity === 'number' &&
    rawCapacity >= 3 &&
    rawCapacity <= 40 &&
    Number.isFinite(briefSlideCount) &&
    rawCapacity > Math.round(briefSlideCount)
  let baseSlideCount =
    preferCapacity && rawCapacity
      ? Math.round(rawCapacity)
      : Number.isFinite(briefSlideCount) && briefSlideCount >= 3 && briefSlideCount <= 40
        ? Math.round(briefSlideCount)
        : capacity && capacity >= 3
          ? capacity
          : Math.max(3, Math.min(40, fallbackSlideCount))

  if (lengthQ && !skippedIds.has(lengthQ.id)) {
    const lenSel = selections[lengthQ.id]
    const rawLen = lenSel ? (lenSel.mode === 'custom' ? lenSel.customText.trim() : lenSel.value) : defaultOptionValue(lengthQ)
    const directNum = Number(rawLen)
    const parsedLen = Number.isFinite(directNum) && directNum > 0 ? directNum : parseFirstPositiveInt(rawLen)
    if (typeof parsedLen === 'number' && parsedLen >= 3 && parsedLen <= 40) {
      baseSlideCount = Math.round(parsedLen)
    }
  }

  const cleanNotes = cleanThesisNotes(thesisNotes)
  let effectiveSlideCount = baseSlideCount
  let slidesValue = slidesPhrase(baseSlideCount)
  let slidesNote =
    capacity && capacity > 0
      ? `В материалах опоры примерно на ${slidesPhrase(capacity)}: обложка, ключевые разделы и выводы.`
      : 'Обложка, ключевые выводы по разделам и итоговый слайд с решением.'
  let slidesBadge: string | null = null
  let slidesChanged = false
  let fillModeLabel = 'По фактам и данным из ваших материалов'

  if (shortQ && !skippedIds.has(shortQ.id)) {
    const shortSel = selections[shortQ.id]
    const defShort = defaultOptionValue(shortQ)
    const shortVal = shortSel ? shortSel.value : defShort
    const shortOpt = shortQ.options.find((o) => o.value === shortVal) ?? shortQ.options[0]
    slidesNote =
      (shortOpt ? resolveOptionNote(shortQ, shortOpt) : null) ??
      shortQ.reason ??
      slidesNote
    slidesBadge = shortOpt?.recommended ? 'советую' : 'выбрано'
    slidesChanged = shortVal !== defShort

    if (shortVal === 'shrink') {
      const shrinkCount = capacity ?? parseFirstPositiveInt(shortOpt?.label) ?? baseSlideCount
      effectiveSlideCount = shrinkCount
      slidesValue = `${slidesPhrase(shrinkCount)} · по материалу`
      fillModeLabel = 'Только подтверждённые факты из ваших файлов, без добора'
    } else if (shortVal === 'notes') {
      slidesValue =
        cleanNotes.length > 0
          ? `${slidesPhrase(baseSlideCount)} · +${cleanNotes.length} ${pluralize(cleanNotes.length, ['тезис', 'тезиса', 'тезисов'])}`
          : `${slidesPhrase(baseSlideCount)} · с вашими тезисами`
      fillModeLabel =
        cleanNotes.length > 0
          ? `Ваши материалы + ${cleanNotes.length} ${pluralize(cleanNotes.length, ['тезис', 'тезиса', 'тезисов'])} автора`
          : 'Ваши материалы + тезисы, которые вы добавите слева'
    } else if (shortVal === 'expand_web') {
      slidesValue = `${slidesPhrase(baseSlideCount)} · с поиском фактов`
      fillModeLabel = 'Ваши материалы + добор фактов из интернета со ссылками'
    } else if (shortVal === 'expand_model') {
      slidesValue = `${slidesPhrase(baseSlideCount)} · с общими знаниями`
      fillModeLabel = 'Ваши материалы + общие знания с пометкой «не подтверждено источником»'
    }
  } else if (lengthQ && !skippedIds.has(lengthQ.id)) {
    const lenSel = selections[lengthQ.id]
    const defLen = defaultOptionValue(lengthQ)
    const lenVal = lenSel ? (lenSel.mode === 'custom' ? lenSel.customText.trim() : lenSel.value) : defLen
    const lenOpt = lengthQ.options.find((o) => o.value === lenVal)
    slidesValue = slidesPhrase(effectiveSlideCount)
    slidesNote =
      (lenOpt ? resolveOptionNote(lengthQ, lenOpt) : null) ??
      lengthQ.reason ??
      slidesNote
    slidesBadge = lenSel?.mode === 'custom' ? 'свой вариант' : lenOpt?.recommended ? 'советую' : 'выбрано'
    slidesChanged = lenSel?.mode === 'custom' || lenVal !== defLen
  } else if (audienceChanged) {
    if (audienceRoleKey === 'совет директоров' || audienceRoleKey === 'руководство') {
      slidesNote = `${slidesPhrase(effectiveSlideCount)}: плотная выжимка для быстрого чтения без второстепенных слайдов.`
    } else if (audienceRoleKey === 'команда') {
      slidesNote = `${slidesPhrase(effectiveSlideCount)}: подробный разбор показателей, задач и шагов по направлениям.`
    } else if (audienceRoleKey === 'клиенты') {
      slidesNote = `${slidesPhrase(effectiveSlideCount)}: фокус на пользе, подтверждённых цифрах и сценарии работы.`
    }
  }

  const mainPoints = (understanding?.summary?.main_points ?? []).filter(
    (point) => point.trim() && point.trim().toLowerCase() !== 'идея',
  )

  let focusValue = 'Факты и выводы из исходных материалов'
  let focusNote = mainPoints[0] ? `Опора на данные: ${mainPoints[0]}.` : `Тема колоды: ${topic}.`
  let focusBadge: string | null = null
  let focusChanged = false

  if (keyMessageQ && !skippedIds.has(keyMessageQ.id)) {
    const sel = selections[keyMessageQ.id]
    const defVal = defaultOptionValue(keyMessageQ)
    if (sel?.mode === 'custom') {
      const customText = sel.customText.trim()
      focusValue = customText || 'Своя главная мысль'
      focusNote = customText
        ? 'Вынесу эту мысль на обложку и свяжу с ней выводы каждого раздела.'
        : 'Напишите главную мысль в поле слева — сделаю её осью презентации.'
      focusBadge = 'свой вариант'
      focusChanged = true
    } else {
      const chosenVal = sel ? sel.value : defVal
      const opt = keyMessageQ.options.find((o) => o.value === chosenVal) ?? keyMessageQ.options[0]
      focusValue = opt?.label ?? chosenVal
      focusNote =
        (opt ? resolveOptionNote(keyMessageQ, opt) : null) ??
        keyMessageQ.reason ??
        'Вынесу на обложку и свяжу с выводами каждого раздела.'
      focusBadge = opt?.recommended ? 'советую' : 'выбрано'
      focusChanged = chosenVal !== defVal
    }
  } else if (ctaQ && !skippedIds.has(ctaQ.id)) {
    const sel = selections[ctaQ.id]
    const defVal = defaultOptionValue(ctaQ)
    if (sel?.mode === 'custom') {
      const customText = sel.customText.trim()
      focusValue = customText || 'Своя цель выступления'
      focusNote = 'Подведу структуру и финальный слайд к этому целевому действию.'
      focusBadge = 'свой вариант'
      focusChanged = true
    } else {
      const chosenVal = sel ? sel.value : defVal
      const opt = ctaQ.options.find((o) => o.value === chosenVal) ?? ctaQ.options[0]
      focusValue = opt?.label ?? chosenVal
      focusNote =
        (opt ? resolveOptionNote(ctaQ, opt) : null) ??
        ctaQ.reason ??
        'Определяет акценты в выводах и финальный слайд.'
      focusBadge = opt?.recommended ? 'советую' : 'выбрано'
      focusChanged = chosenVal !== defVal
    }
  } else {
    if (audienceRoleKey === 'совет директоров') {
      focusValue = 'Финансовый эффект, ключевые драйверы и стратегические решения'
      focusNote = mainPoints[0]
        ? `Главный вывод из данных: ${mainPoints[0]}. Фокус на решениях и влиянии на бизнес.`
        : 'Крупный план: итоги, влияние на показатели и решения для согласования.'
      focusBadge = 'под аудиторию'
      focusChanged = true
    } else if (audienceRoleKey === 'руководство') {
      focusValue = 'Выполнение показателей, узкие места и управленческие выводы'
      focusNote = mainPoints[0]
        ? `Главный вывод из данных: ${mainPoints[0]}. Коротко по метрикам, рискам и действиям.`
        : 'Сводка результатов, причин отклонений и предлагаемых решений.'
      focusBadge = 'под аудиторию'
      focusChanged = true
    } else if (audienceRoleKey === 'команда') {
      focusValue = 'Разбор метрик по направлениям, вклад в результат и план действий'
      focusNote = mainPoints[0]
        ? `Опора на данные: ${mainPoints[0]}. Прикладной разбор задач и шагов.`
        : 'Прикладная структура: что сработало, где узкие места и что делаем дальше.'
      focusBadge = 'под аудиторию'
      focusChanged = true
    } else if (audienceRoleKey === 'клиенты') {
      focusValue = 'Практическая польза, доказанные результаты и следующие шаги'
      focusNote = mainPoints[0]
        ? `Подтверждение цифрами: ${mainPoints[0]}. Акцент на надёжности и выгоде.`
        : 'Фокус на ценности предложения, кейсах и понятном сценарии работы.'
      focusBadge = 'под аудиторию'
      focusChanged = true
    } else if (audienceRoleKey === 'custom') {
      focusValue = `Аргументы и выводы под аудиторию «${audienceValue}»`
      focusNote = mainPoints[0]
        ? `Опора на факты из материалов: ${mainPoints[0]}.`
        : `Структура и акценты будут собраны по теме «${topic}».`
      focusBadge = 'под аудиторию'
      focusChanged = true
    } else if (typeof suggestedBrief?.key_message === 'string' && suggestedBrief.key_message.trim()) {
      focusValue = sanitizeQuestionCopy(suggestedBrief.key_message.trim())
    } else if (cleanNotes.length > 0) {
      focusValue = cleanNotes[0] ?? focusValue
      focusNote =
        cleanNotes.length > 1
          ? `Ваши тезисы в основе плана: ${cleanNotes.join(' · ')}`
          : 'Ваш тезис станет опорным акцентом ключевых слайдов.'
      focusBadge = 'ваш тезис'
      focusChanged = true
    } else if (mainPoints.length > 0) {
      focusValue = mainPoints[0] ?? focusValue
      focusNote =
        mainPoints.length > 1
          ? `Второй опорный вывод: ${mainPoints.slice(1).join(' · ')}`
          : 'Нейтральная подача ключевых фактов и цифр из ваших материалов.'
    } else {
      focusValue = 'Факты и выводы из материалов без перекоса в роль'
      focusNote = `Структура строится вокруг темы «${topic}».`
    }
  }

  const items: readonly [DeckSpecItem, DeckSpecItem, DeckSpecItem] = [
    {
      id: 'audience',
      label: 'Для кого',
      value: audienceValue,
      note: audienceNote,
      reason: audienceQ?.reason ?? null,
      badge: audienceBadge,
      changed: audienceChanged,
      questionId: audienceQ?.id ?? null,
    },
    {
      id: 'slides',
      label: 'Сколько слайдов',
      value: slidesValue,
      note: slidesNote,
      reason: shortQ?.reason ?? lengthQ?.reason ?? null,
      badge: slidesBadge,
      changed: slidesChanged,
      questionId: shortQ?.id ?? lengthQ?.id ?? null,
    },
    {
      id: 'focus',
      label: 'На чём акцент',
      value: focusValue,
      note: focusNote,
      reason: keyMessageQ?.reason ?? ctaQ?.reason ?? audienceQ?.reason ?? null,
      badge: focusBadge,
      changed: focusChanged,
      questionId: keyMessageQ?.id ?? ctaQ?.id ?? audienceQ?.id ?? null,
    },
  ]

  const midEnd = Math.max(2, effectiveSlideCount - 1)
  const midSlot = effectiveSlideCount <= 3 ? 'Слайд 2' : `Слайды 2–${midEnd}`
  const evidenceTitle =
    cleanNotes.length > 0
      ? `Тезисы автора: ${cleanNotes.join(' · ')}`
      : mainPoints.length > 0
        ? mainPoints.join(' · ')
        : 'Ключевые факты, метрики и аргументы по теме'

  const closingTitle =
    audienceRoleKey === 'совет директоров'
      ? 'Стратегические выводы и решения на согласование'
      : audienceRoleKey === 'руководство'
        ? 'Управленческие итоги, риски и приоритеты'
        : audienceRoleKey === 'команда'
          ? 'План действий, задачи и следующие шаги команды'
          : audienceRoleKey === 'клиенты'
            ? 'Итоговая ценность и следующие шаги сотрудничества'
            : 'Ключевые выводы и рекомендуемые шаги'

  const outline: DeckSpecOutlineRow[] = [
    {
      slot: 'Слайд 1',
      role: 'Обложка и рамка',
      title: 'Титульный слайд и контекст',
      detail: `Подача под аудиторию: ${audienceValue}.`,
    },
    {
      slot: midSlot,
      role: 'Факты и разбор',
      title: evidenceTitle,
      detail: fillModeLabel,
    },
    {
      slot: `Слайд ${effectiveSlideCount}`,
      role: 'Финал и решение',
      title: closingTitle,
      detail: 'Выводы по разделам и ориентиры для следующего шага.',
    },
  ]

  const firstDataset = understanding?.datasets?.[0]
  const datasetLabel = firstDataset
    ? `${firstDataset.file ?? firstDataset.title ?? 'Таблица данных'}${firstDataset.task_ru ? ` · ${firstDataset.task_ru}` : ''}`
    : null

  const capacityLabel =
    capacity && capacity > 0
      ? `Опора из материалов: ~${slidesPhrase(capacity)}`
      : null

  return {
    topic,
    slideCount: effectiveSlideCount,
    slideCountLabel: slidesPhrase(effectiveSlideCount),
    fillModeLabel,
    capacityLabel,
    datasetLabel,
    items,
    outline,
  }
}

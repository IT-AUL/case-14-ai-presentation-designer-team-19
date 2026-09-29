export type RuleCategory = 'brand' | 'layout' | 'text' | 'density' | 'integrity' | 'meaning'

export const CATEGORY_LABEL: Record<RuleCategory, string> = {
  brand: 'Шаблон и бренд',
  layout: 'Вёрстка',
  text: 'Текст и читаемость',
  density: 'Плотность',
  integrity: 'Целостность и редактируемость',
  meaning: 'Смысл (модель)',
}

export const CATEGORY_ORDER: readonly RuleCategory[] = ['brand', 'layout', 'text', 'density', 'integrity', 'meaning']

export interface RuleMeta {
  name: string
  category: RuleCategory
  autoFix?: string
  deterministic?: boolean
  repairable?: boolean
}

export interface RuleCatalogEntry {
  code: string
  title_ru?: string | null
  category?: string | null
  deterministic?: boolean | null
  default_severity?: string | null
  repairable?: boolean | null
  fix_title_ru?: string | null
  threshold?: unknown
}

export const RULES: Record<string, RuleMeta> = {
  'layout.out_of_bounds': { name: 'Элемент выходит за границы слайда', category: 'layout', autoFix: 'Вернуть фигуру в границы слайда' },
  'layout.unintended_overlap': { name: 'Блоки накладываются друг на друга', category: 'layout' },
  'layout.edge_margin': { name: 'Контент заходит в поля у края', category: 'layout', autoFix: 'Сдвинуть блок в поля шаблона' },
  'image.aspect_ratio': { name: 'Картинка растянута', category: 'layout', autoFix: 'Восстановить пропорции картинки' },
  'text.overflow': { name: 'Текст не помещается в рамку', category: 'text', autoFix: 'Сократить текст или увеличить рамку' },
  'text.slide_clip': { name: 'Текст обрезан краем слайда', category: 'text', autoFix: 'Сдвинуть текст внутрь слайда' },
  'text.font_floor': { name: 'Слишком мелкий кегль', category: 'text' },
  'accessibility.contrast': { name: 'Контраст текста ниже 4.5:1', category: 'text', autoFix: 'Заменить цвет текста на контрастный из палитры' },
  'template.font_family': { name: 'Шрифт не из шаблона', category: 'brand', autoFix: 'Заменить шрифт на шрифт шаблона' },
  'template.font_scale': { name: 'Кегль не из шкалы шаблона', category: 'brand' },
  'template.color_palette': { name: 'Цвет не из палитры шаблона', category: 'brand', autoFix: 'Заменить на ближайший цвет палитры' },
  'template.anchor_position': { name: 'Логотип или колонтитул сдвинут', category: 'brand', autoFix: 'Вернуть элемент в якорь шаблона' },
  'template.layout_origin': { name: 'Слайд собран не на макете шаблона', category: 'brand' },
  'density.bullet_count': { name: 'Больше 6 буллетов на слайде', category: 'density' },
  'density.bullet_length': { name: 'Буллет длиннее 15 слов', category: 'density' },
  'density.table_size': { name: 'Таблица больше 7 × 5', category: 'density' },
  'density.chart_series': { name: 'Больше 5 серий на диаграмме', category: 'density' },
  'density.occupancy': { name: 'Слайд заполнен меньше ¼ или больше ¾', category: 'density' },
  'integrity.empty_slide': { name: 'Пустой слайд или только заголовок', category: 'integrity', autoFix: 'Объединить с соседним слайдом' },
  'integrity.duplicate_slide': { name: 'Слайды дублируют друг друга', category: 'integrity', autoFix: 'Убрать дубль' },
  'integrity.placeholder_text': { name: 'Остался текст-заглушка', category: 'integrity', autoFix: 'Убрать пустой плейсхолдер' },
  'integrity.package': { name: 'Файл повреждён', category: 'integrity' },
  'editability.raster_only': { name: 'Слайд — картинка, а не объекты', category: 'integrity', autoFix: 'Пересобрать слайд нативными объектами' },
  'chart.metadata': { name: 'У диаграммы нет подписей или легенды', category: 'integrity' },
  'content.conclusion_title': { name: 'Заголовок называет тему, а не вывод', category: 'meaning' },
  'content.title_alignment': { name: 'Содержимое не соответствует заголовку', category: 'meaning' },
  'content.single_message': { name: 'Слайд не пересказывается одной фразой', category: 'meaning' },
  'content.source_support': { name: 'Факт не найден в исходных материалах', category: 'meaning' },
  'content.nonempty': { name: 'На слайде только заголовок', category: 'meaning' },
  'content.visual_relevance': { name: 'Картинки не по теме слайда', category: 'meaning' },
  'content.prompt_leakage': { name: 'Служебный мусор на слайде', category: 'meaning' },
  'content.spelling': { name: 'Опечатки', category: 'meaning' },
  'content.data_relevance': { name: 'Данные не работают на мысль слайда', category: 'meaning' },
  'deck.logical_flow': { name: 'Соседние слайды не связаны', category: 'meaning' },
}

const CATEGORY_SET = new Set<string>(CATEGORY_ORDER)

let catalog: ReadonlyMap<string, RuleMeta> | null = null

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function guessCategory(ruleCode: string): RuleCategory {
  return ruleCode.startsWith('content.') || ruleCode.startsWith('deck.') ? 'meaning' : 'integrity'
}

function catalogItems(payload: unknown): unknown[] {
  if (Array.isArray(payload)) return payload
  if (!isRecord(payload)) return []
  const list = payload.items ?? payload.rules
  return Array.isArray(list) ? list : []
}

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() !== '' ? value.trim() : null
}

export function parseRuleCatalog(payload: unknown): RuleCatalogEntry[] {
  return catalogItems(payload).flatMap((item): RuleCatalogEntry[] => {
    if (!isRecord(item)) return []
    const code = text(item.code)
    return code ? [{ ...item, code } as RuleCatalogEntry] : []
  })
}

function toMeta(entry: RuleCatalogEntry): RuleMeta {
  const local = RULES[entry.code]
  const category = entry.category && CATEGORY_SET.has(entry.category) ? (entry.category as RuleCategory) : (local?.category ?? guessCategory(entry.code))
  const repairable = typeof entry.repairable === 'boolean' ? entry.repairable : Boolean(text(entry.fix_title_ru))
  const autoFix = text(entry.fix_title_ru) ?? (repairable ? local?.autoFix : undefined)
  return {
    name: text(entry.title_ru) ?? local?.name ?? entry.code,
    category,
    ...(autoFix ? { autoFix } : {}),
    ...(typeof entry.deterministic === 'boolean' ? { deterministic: entry.deterministic } : {}),
    repairable,
  }
}

export function applyRuleCatalog(entries: readonly RuleCatalogEntry[] | null): void {
  catalog = entries && entries.length > 0 ? new Map(entries.map((entry) => [entry.code, toMeta(entry)])) : null
}

export function hasRuleCatalog(): boolean {
  return catalog !== null
}

export function ruleMeta(ruleCode: string): RuleMeta {
  return catalog?.get(ruleCode) ?? RULES[ruleCode] ?? { name: ruleCode, category: guessCategory(ruleCode) }
}

export function isAutoFixable(ruleCode: string): boolean {
  if (catalog) return catalog.get(ruleCode)?.repairable === true
  return Boolean(RULES[ruleCode]?.autoFix)
}

export function listRules(): [string, RuleMeta][] {
  return catalog ? [...catalog.entries()] : Object.entries(RULES)
}

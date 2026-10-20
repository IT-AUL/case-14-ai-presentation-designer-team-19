import neutralTemplateUrl from './neutral-template.pptx?url'

export type Stage2PresetId = 'revenue-channels' | 'analytics-gaps' | 'reference-240'

export interface Stage2PresetFileSpec {
  name: string
  mimeType: string
  content: string
}

export const PRESET_TEMPLATE_NAME = 'neutral-template.pptx'

const PPTX_MIME = 'application/vnd.openxmlformats-officedocument.presentationml.presentation'

export interface Stage2Preset {
  id: Stage2PresetId
  title: string
  chip: string
  chipLabel: string
  summary: string
  topic: string
  files: readonly Stage2PresetFileSpec[]
}

const PRESET_LAST_MODIFIED = 1_760_745_600_000

const SALES_MONTHLY_CSV = [
  'Месяц,"Выручка, млн ₽","Заказы, шт."',
  '2024-01,11.40,2650',
  '2024-02,11.75,2730',
  '2024-03,12.10,2815',
  '2024-04,12.48,2902',
  '2024-05,12.85,2988',
  '2024-06,13.20,3070',
  '2024-07,13.55,3151',
  '2024-08,13.92,3237',
  '2024-09,14.35,3337',
  '2024-10,14.80,3442',
  '2024-11,15.30,3558',
  '2024-12,16.80,3907',
  '2025-01,15.65,3640',
  '2025-02,16.10,3744',
  '2025-03,16.58,3856',
  '2025-04,17.05,3965',
  '2025-05,17.52,4074',
  '2025-06,18.04,4195',
  '2025-07,18.48,4298',
  '2025-08,18.95,4407',
  '2025-09,19.42,4516',
  '2025-10,19.86,4619',
  '2025-11,20.18,4693',
  '2025-12,20.66,4805',
].join('\n')

const REGIONS_COMPARISON_CSV = [
  'Регион,"Выручка, млн ₽","Маржинальность, %"',
  'Москва,412.5,34.2',
  'Санкт-Петербург,238.4,31.8',
  'Казань,146.0,29.5',
  'Екатеринбург,132.8,28.4',
  'Новосибирск,118.6,27.9',
  'Краснодар,94.2,26.5',
].join('\n')

const CHANNELS_SHARE_JSON = JSON.stringify(
  {
    title: 'Структура продаж по каналам, 2025',
    columns: ['Канал', 'Доля, %'],
    data: [
      ['Сайт', 42.0],
      ['Приложение', 31.0],
      ['Маркетплейсы', 18.5],
      ['Партнёры', 8.5],
    ],
  },
  null,
  2,
)

const NPS_WITH_GAPS_CSV = [
  'Месяц,NPS',
  '2025-01,31.2',
  '2025-02,32.5',
  '2025-03,34.1',
  '2025-04,35.8',
  '2025-05,',
  '2025-06,38.4',
  '2025-07,40.2',
  '2025-08,41.9',
  '2025-09,',
  '2025-10,44.6',
  '2025-11,46.8',
  '2025-12,49.5',
].join('\n')

const ORDER_VALUES_LIST = [
  1420, 1580, 1640, 1710, 1790, 1850, 1920, 1980, 2040, 2110,
  2180, 2240, 2290, 2350, 2410, 2480, 2530, 2590, 2660, 2720,
  2780, 2840, 2910, 2970, 3050, 3120, 3190, 3260, 3340, 3420,
  3510, 3600, 3690, 3780, 3890, 4010, 4150, 4290, 4450, 4620,
  4850, 5120, 5380, 5690, 5980, 6320, 6710, 7140, 7590, 8080,
  8640, 9250, 9890, 10540, 11280, 12050, 12890, 13640, 14220, 14850,
]

const ORDER_VALUES_CSV = ['"Сумма заказа, ₽"', ...ORDER_VALUES_LIST.map((val) => val.toFixed(2))].join('\n')

const REPORT_WITH_TABLE_MD = [
  '# Заметка аналитика по удержанию клиентов за 2025 год',
  '',
  'Цель — показать трёхкратный рост удержания в 4 квартале после перезапуска программы лояльности и обновления мобильного приложения.',
  '',
  '| Квартал | Новые клиенты | Удержание, % | Повторные заказы |',
  '| --- | --- | --- | --- |',
  '| 1 кв. 2025 | 1240 | 14.2 | 310 |',
  '| 2 кв. 2025 | 1520 | 21.5 | 490 |',
  '| 3 кв. 2025 | 1980 | 31.8 | 780 |',
  '| 4 кв. 2025 | 2610 | 43.0 | 1340 |',
  '',
  'Отток стабилизировался со второго квартала, а доля повторных заказов выросла за счёт быстрого оформления в приложении.',
].join('\n')

const REFERENCE_CATEGORIES = [
  { name: 'Вычислительные узлы', unit: 'узел/мес', base: 4200 },
  { name: 'Хранение данных', unit: 'ТБ/мес', base: 1800 },
  { name: 'Корпоративные коммуникации', unit: 'учётная запись/мес', base: 450 },
  { name: 'Информационная безопасность', unit: 'лицензия/мес', base: 6900 },
  { name: 'Аналитика и витрины', unit: 'кластер/мес', base: 12400 },
  { name: 'Сетевые сервисы', unit: 'канал/мес', base: 3100 },
] as const

const REFERENCE_STATUSES = ['Действует', 'Действует', 'Действует', 'Обновлён', 'Архив'] as const

function buildProductsReferenceCsv(): string {
  const rows = ['Артикул,Категория,Наименование,Единица,Статус,Базовый тариф']
  for (let index = 0; index < 240; index += 1) {
    const category = REFERENCE_CATEGORIES[index % REFERENCE_CATEGORIES.length] ?? REFERENCE_CATEGORIES[0]
    const status = REFERENCE_STATUSES[index % REFERENCE_STATUSES.length] ?? 'Действует'
    const sku = `SKU-${String(1001 + index)}`
    const tier = Math.floor(index / REFERENCE_CATEGORIES.length) + 1
    const itemName = `${category.name} · конфигурация ${tier}`
    const tariff = category.base + ((index * 13) % 15) * 350
    rows.push(`${sku},${category.name},${itemName},${category.unit},${status},${tariff}`)
  }
  return rows.join('\n')
}

const PRODUCTS_REFERENCE_CSV = buildProductsReferenceCsv()

export const STAGE2_PRESETS: readonly Stage2Preset[] = [
  {
    id: 'revenue-channels',
    title: 'Выручка и каналы продаж',
    chip: 'Выручка и каналы',
    chipLabel: 'Выручка и каналы',
    summary: '24 месяца продаж, 6 регионов и 4 канала',
    topic: 'Итоги продаж за 24 месяца для совета директоров: динамика выручки, лидеры по регионам и структура каналов привлечения',
    files: [
      { name: 'sales_monthly.csv', mimeType: 'text/csv', content: SALES_MONTHLY_CSV },
      { name: 'regions_comparison.csv', mimeType: 'text/csv', content: REGIONS_COMPARISON_CSV },
      { name: 'channels_share.json', mimeType: 'application/json', content: CHANNELS_SHARE_JSON },
    ],
  },
  {
    id: 'analytics-gaps',
    title: 'Продуктовая аналитика и пропуски',
    chip: 'Аналитика и пропуски',
    chipLabel: 'Аналитика и пропуски',
    summary: 'NPS с пропусками, 60 чеков и заметка с таблицей',
    topic: 'Аудит клиентского опыта и распределения чеков: где теряем лояльность и как устроен средний чек',
    files: [
      { name: 'nps_with_gaps.csv', mimeType: 'text/csv', content: NPS_WITH_GAPS_CSV },
      { name: 'order_values.csv', mimeType: 'text/csv', content: ORDER_VALUES_CSV },
      { name: 'report_with_table.md', mimeType: 'text/markdown', content: REPORT_WITH_TABLE_MD },
    ],
  },
  {
    id: 'reference-240',
    title: 'Справочник 240 позиций (стресс-тест таблиц)',
    chip: 'Справочник 240 строк',
    chipLabel: 'Справочник 240 строк',
    summary: 'Номенклатура из 240 позиций по 6 категориям',
    topic: 'Каталог тарифных позиций и номенклатуры на 2026 год: сводка по категориям и полный перечень',
    files: [
      { name: 'products_reference.csv', mimeType: 'text/csv', content: PRODUCTS_REFERENCE_CSV },
    ],
  },
]

const PRESET_FILE_NAMES = new Set(STAGE2_PRESETS.flatMap((preset) => preset.files.map((file) => file.name)))
const PRESET_TOPICS = new Set(STAGE2_PRESETS.map((preset) => preset.topic))

export function isPresetFileName(name: string): boolean {
  return PRESET_FILE_NAMES.has(name)
}

export function isPresetTopic(topic: string): boolean {
  return PRESET_TOPICS.has(topic.trim())
}

export function getStage2Preset(id: Stage2PresetId): Stage2Preset | undefined {
  return STAGE2_PRESETS.find((preset) => preset.id === id)
}

export function buildPresetFiles(id: Stage2PresetId): File[] {
  const preset = getStage2Preset(id)
  if (!preset) return []
  return preset.files.map(
    (spec) => new File([spec.content], spec.name, { type: spec.mimeType, lastModified: PRESET_LAST_MODIFIED }),
  )
}

export function detectActivePresetId(fileNames: readonly string[]): Stage2PresetId | null {
  const present = new Set(fileNames)
  for (const preset of STAGE2_PRESETS) {
    if (preset.files.every((file) => present.has(file.name))) {
      return preset.id
    }
  }
  return null
}

export async function buildPresetTemplateFile(): Promise<File | null> {
  try {
    const response = await fetch(neutralTemplateUrl)
    if (!response.ok) return null
    const blob = await response.blob()
    if (!blob.size) return null
    return new File([blob], PRESET_TEMPLATE_NAME, { type: PPTX_MIME, lastModified: PRESET_LAST_MODIFIED })
  } catch {
    return null
  }
}

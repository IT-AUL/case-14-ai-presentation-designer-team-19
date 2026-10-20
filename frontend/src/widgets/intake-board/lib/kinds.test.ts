import { fileExtension, guessKind, isContentFile, isImageFile, isIntakeFile, mergeIntakeFiles, roleLabel } from './kinds'
import { buildPresetFiles, detectActivePresetId, getStage2Preset, isPresetFileName, isPresetTopic, STAGE2_PRESETS } from './presets'

describe('fileExtension', () => {
  it('returns the lowercase extension', () => {
    expect(fileExtension('Deck.PPTX')).toBe('.pptx')
    expect(fileExtension('archive.tar.gz')).toBe('.gz')
    expect(fileExtension('no-extension')).toBe('')
  })
})

describe('isIntakeFile', () => {
  it('accepts all supported formats including .png, .jpg and .webp', () => {
    for (const name of ['a.pptx', 'a.potx', 'a.md', 'a.txt', 'a.docx', 'a.pdf', 'a.csv', 'a.xlsx', 'a.json', 'a.png', 'a.jpg', 'a.jpeg', 'a.webp', 'a.svg']) {
      expect(isIntakeFile(name)).toBe(true)
    }
  })

  it('rejects unsupported formats', () => {
    for (const name of ['a.exe', 'a.zip', 'a.doc', 'a.xls', 'a', '.pptx']) {
      expect(isIntakeFile(name)).toBe(false)
    }
  })
})

describe('guessKind', () => {
  it('maps presentations to template', () => {
    expect(guessKind('corp.pptx')).toBe('template')
    expect(guessKind('corp.potx')).toBe('template')
  })

  it('maps tables and non-brand json to data', () => {
    expect(guessKind('sales.csv')).toBe('data')
    expect(guessKind('sales.xlsx')).toBe('data')
    expect(guessKind('channels_share.json')).toBe('data')
  })

  it('maps brand files and images (.png, .jpg, .webp) to brandbook', () => {
    expect(guessKind('tokens.json')).toBe('brandbook')
    expect(guessKind('brand-guide.pdf')).toBe('brandbook')
    expect(guessKind('logo.png')).toBe('brandbook')
    expect(guessKind('palette.jpg')).toBe('brandbook')
    expect(guessKind('slide-page.webp')).toBe('brandbook')
    expect(isImageFile('slide-page.webp')).toBe(true)
  })

  it('maps everything else to content', () => {
    expect(guessKind('notes.md')).toBe('content')
    expect(guessKind('report.docx')).toBe('content')
  })
})

describe('isContentFile', () => {
  const file = (kind: 'template' | 'content' | 'data' | 'brandbook' | 'unknown', accepted = true) => ({ name: 'a', kind, detected_kind: kind, accepted, reason: null, size_bytes: 1 })

  it('counts accepted content and data files', () => {
    expect(isContentFile(file('content'))).toBe(true)
    expect(isContentFile(file('data'))).toBe(true)
  })

  it('ignores templates, brandbooks and rejected files', () => {
    expect(isContentFile(file('template'))).toBe(false)
    expect(isContentFile(file('brandbook'))).toBe(false)
    expect(isContentFile(file('content', false))).toBe(false)
  })
})

describe('mergeIntakeFiles', () => {
  const file = (name: string, kind: 'template' | 'content' | 'data' | 'brandbook' | 'unknown' = 'content') => ({ name, kind, detected_kind: kind, accepted: true, reason: null, size_bytes: 1 })

  it('appends new files and keeps existing order', () => {
    expect(mergeIntakeFiles([file('a')], [file('b')]).map((f) => f.name)).toEqual(['a', 'b'])
  })

  it('replaces a file with the same name', () => {
    expect(mergeIntakeFiles([file('a'), file('b')], [file('a', 'data')])).toEqual([file('b'), file('a', 'data')])
  })
})

describe('roleLabel', () => {
  it('translates known slide roles', () => {
    expect(roleLabel('title')).toBe('Обложки')
    expect(roleLabel('section')).toBe('Разделы')
    expect(roleLabel('content')).toBe('Рабочие')
    expect(roleLabel('closing')).toBe('Финал')
  })

  it('keeps unknown roles as-is', () => {
    expect(roleLabel('gallery')).toBe('gallery')
  })
})

describe('STAGE2_PRESETS and buildPresetFiles', () => {
  it('provides three Stage 2 presets and generates valid File objects', () => {
    expect(STAGE2_PRESETS.map((p) => p.id)).toEqual(['revenue-channels', 'analytics-gaps', 'reference-240'])

    const revenuePreset = getStage2Preset('revenue-channels')
    const revenueFiles = buildPresetFiles('revenue-channels')
    expect(revenueFiles.map((f) => f.name)).toEqual(['sales_monthly.csv', 'regions_comparison.csv', 'channels_share.json'])
    expect(revenueFiles.every((f) => f instanceof File && f.size > 0)).toBe(true)
    const salesLines = (revenuePreset?.files[0]?.content ?? '').trim().split('\n')
    expect(salesLines).toHaveLength(25)
    const channelsJson = JSON.parse(revenuePreset?.files[2]?.content ?? '{}') as { data?: unknown[] }
    expect(channelsJson.data).toHaveLength(4)

    const analyticsPreset = getStage2Preset('analytics-gaps')
    const analyticsFiles = buildPresetFiles('analytics-gaps')
    expect(analyticsFiles.map((f) => f.name)).toEqual(['nps_with_gaps.csv', 'order_values.csv', 'report_with_table.md'])
    expect(analyticsFiles.every((f) => f instanceof File && f.size > 0)).toBe(true)
    const npsText = analyticsPreset?.files[0]?.content ?? ''
    expect(npsText).toContain('2025-05,')
    const orderLines = (analyticsPreset?.files[1]?.content ?? '').trim().split('\n')
    expect(orderLines).toHaveLength(61)
    const reportMd = analyticsPreset?.files[2]?.content ?? ''
    expect(reportMd).toContain('Цель — показать трёхкратный рост удержания в 4 квартале')

    const refPreset = getStage2Preset('reference-240')
    const refFiles = buildPresetFiles('reference-240')
    expect(refFiles.map((f) => f.name)).toEqual(['products_reference.csv'])
    expect(refFiles[0]?.size).toBeGreaterThan(1000)
    const refLines = (refPreset?.files[0]?.content ?? '').trim().split('\n')
    expect(refLines[0]).toBe('Артикул,Категория,Наименование,Единица,Статус,Базовый тариф')
    expect(refLines).toHaveLength(241)

    expect(detectActivePresetId(['brand.pptx', 'sales_monthly.csv', 'regions_comparison.csv', 'channels_share.json'])).toBe('revenue-channels')
    expect(isPresetFileName('products_reference.csv')).toBe(true)
    expect(isPresetFileName('brand.pptx')).toBe(false)
    expect(isPresetTopic(refPreset?.topic ?? '')).toBe(true)
  })
})

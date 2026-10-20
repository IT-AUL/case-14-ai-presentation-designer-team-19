import { describe, expect, it } from 'vitest'
import type { BrandbookSummary, TemplateSummary } from '@/entities/assistant'
import { buildDesignSystem } from '@/entities/template'
import { designDnaFixture, templateDetailFixture } from '@/shared/api/mocks'
import {
  cleanLayoutName,
  colorName,
  dnaFileName,
  extractBrandPage,
  extractBrandQuote,
  normalizeHex,
  passportBrandDetails,
  passportColors,
  passportConflicts,
  passportFonts,
  passportRoles,
  previewIds,
} from './passport'

const summary: TemplateSummary = {
  template_id: 'tpl',
  filename: 'VK Tech шаблон.pptx',
  slides: 54,
  roles: { content: 47, title: 2, closing: 3, section: 2, unresolved: 9 },
  palette: ['#0077FF', '#FFFFFF'],
  fonts: ['Arial'],
  heading_font: 'Arial',
  body_font: 'Calibri',
  preview_artifact_ids: [],
  assistant_note: null,
}

describe('colorName', () => {
  it.each([
    ['#FFFFFF', 'белый'],
    ['#0A0A0A', 'чёрный'],
    ['#0077FF', 'синий'],
    ['#FFD91D', 'жёлтый'],
    ['#E4572E', 'красный'],
    ['#8A8A8A', 'серый'],
    ['#9FC5FF', 'голубой'],
    ['#0F2A12', 'тёмно-зелёный'],
  ])('names %s as %s', (hex, name) => {
    expect(colorName(hex)).toBe(name)
  })

  it('tolerates malformed input', () => {
    expect(colorName('nope')).toBe('цвет')
    expect(normalizeHex('0077ff')).toBe('#0077FF')
    expect(normalizeHex(null)).toBeNull()
  })
})

describe('passport model on the recorded analysis', () => {
  const system = buildDesignSystem(templateDetailFixture, designDnaFixture)

  it('puts the colors really used on slides first, with shares', () => {
    const colors = passportColors(summary, system)
    expect(colors.length).toBeGreaterThan(0)
    expect(colors.length).toBeLessThanOrEqual(6)
    expect(colors.every((color) => color.share !== null && color.name.length > 0)).toBe(true)
    expect(colors[0]?.share).toBeGreaterThanOrEqual(colors[colors.length - 1]?.share ?? 0)
  })

  it('falls back to the summary palette without the analysis', () => {
    expect(passportColors(summary, null)).toEqual([
      { hex: '#0077FF', name: 'синий', share: null },
      { hex: '#FFFFFF', name: 'белый', share: null },
    ])
  })

  it('sets declared fonts against observed ones and flags the mismatch', () => {
    const fonts = passportFonts(summary, system)
    expect(fonts.declared).toEqual(['Arial'])
    expect(fonts.observed.map((font) => font.name)).toEqual(['Play', 'Calibri', 'Consolas'])
    expect(fonts.conflict).toBe(true)
  })

  it('uses heading and body fonts from the summary when there is no analysis', () => {
    expect(passportFonts(summary, null)).toEqual({ declared: ['Arial', 'Calibri'], observed: [], conflict: false })
  })
})

describe('passportRoles', () => {
  it('orders roles for people and drops technical ones', () => {
    expect(passportRoles(summary)).toEqual([
      { role: 'title', label: 'Обложки', slides: 2 },
      { role: 'section', label: 'Разделы', slides: 2 },
      { role: 'content', label: 'Рабочие', slides: 47 },
      { role: 'closing', label: 'Финал', slides: 3 },
    ])
  })
})

describe('previewIds', () => {
  it('takes template previews from the summary and the analysis without duplicates, at most four', () => {
    const [base] = designDnaFixture.exemplars
    const dna = { ...designDnaFixture, exemplars: ['a', 'b', 'c', null, 'a'].map((id, index) => ({ ...base, slide_index: index, preview_artifact_id: id })) as typeof designDnaFixture.exemplars }
    expect(previewIds({ ...summary, preview_artifact_ids: ['x', 'a'] }, dna)).toEqual(['x', 'a', 'b', 'c'])
    expect(previewIds(summary, undefined)).toEqual([])
  })
})

describe('passportConflicts', () => {
  const brandbook = {
    conflicts: [
      { field: 'color.primary', kind: 'color', template: '#0077FF', brandbook: '#E4572E', resolved: 'brandbook', note: null },
      { field: 'font.heading', kind: 'font', template: 'Arial', brandbook: 'DejaVu Serif', resolved: 'brandbook', note: null },
      { field: 'font.body', kind: 'font', template: null, brandbook: 'Liberation Sans', resolved: null, note: null },
    ],
  } as unknown as BrandbookSummary

  it('labels conflicts in plain words and follows the chosen priority', () => {
    const conflicts = passportConflicts(brandbook, 'template')
    expect(conflicts.map((conflict) => [conflict.label, conflict.kind, conflict.chosen])).toEqual([
      ['Главный цвет', 'color', 'template'],
      ['Шрифт заголовков', 'font', 'template'],
    ])
  })

  it('is empty without a brandbook', () => {
    expect(passportConflicts(null, 'brandbook')).toEqual([])
  })
})

describe('passportBrandDetails', () => {
  it('extracts colors and fonts with page and clean quote without hex codes', () => {
    const brandbook = {
      palette: [
        { hex: '#E4572E', name: 'Медный', role: 'primary', source: 'brandbook.pdf:page=3:vision: Основной цвет бренда — медный #E4572E' },
      ],
      fonts: [
        { family: 'DejaVu Serif', role: 'heading', source: 'brandbook.pdf:page=7:vision: Заголовки набираются гарнитурой DejaVu Serif', fallback: 'Georgia' },
      ],
    } as unknown as BrandbookSummary
    const details = passportBrandDetails(brandbook)
    expect(details.colors).toEqual([
      {
        hex: '#E4572E',
        name: 'красный',
        role: 'Главный цвет',
        quote: 'Основной цвет бренда — медный',
        page: 'стр. 3',
      },
    ])
    expect(details.fonts).toEqual([
      {
        family: 'DejaVu Serif',
        role: 'заголовки',
        quote: 'Заголовки набираются гарнитурой DejaVu Serif',
        page: 'стр. 7',
      },
    ])
    expect(extractBrandPage({ source: 'brand.pdf:page=12:vision:test' })).toBe('стр. 12')
    expect(extractBrandQuote({ quote: '«Фирменный синий #0077FF»' })).toBe('Фирменный синий')
  })
})

describe('cleanLayoutName', () => {
  it('strips numeric and technical prefixes from layout names', () => {
    expect(cleanLayoutName('1_Заголовочный слайд')).toBe('Заголовочный слайд')
    expect(cleanLayoutName('02 - Титульный слайд')).toBe('Титульный слайд')
    expect(cleanLayoutName('layout_3_Рабочий слайд')).toBe('Рабочий слайд')
    expect(cleanLayoutName('11')).toBe('')
    expect(cleanLayoutName('unresolved')).toBe('')
    expect(cleanLayoutName(null)).toBe('')
  })
})

describe('dnaFileName', () => {
  it('builds a safe file name from the template name', () => {
    expect(dnaFileName('VK Tech шаблон.pptx')).toBe('VK-Tech-шаблон-design-dna.json')
    expect(dnaFileName(null)).toBe('template-design-dna.json')
  })
})

import { createBriefForm } from '../model/form'
import type { BriefForm, ParsedContent } from '../model/form'
import {
  fileContentKey,
  partitionContentFiles,
  resolveContent,
  textContentKey,
  textToContentFile,
  toBrief,
  toContentBrief,
  toGenerationBody,
  validateBrief,
} from './brief'

function form(patch: Partial<BriefForm> = {}): BriefForm {
  return { ...createBriefForm({ language: 'ru', targetSlideCount: 12 }), purpose: 'product', ...patch }
}

function readText(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(reader.error)
    reader.readAsText(blob)
  })
}

function file(name: string, content = 'x', lastModified = 1): File {
  return new File([content], name, { lastModified })
}

describe('brief body mapping', () => {
  it('maps the form state to the backend brief', () => {
    const brief = toBrief(
      form({
        purpose: 'initiative',
        audience: '  Команда платформы ',
        tone: ' деловой ',
        language: 'en',
        slideCount: 8,
        mandatorySections: ['Проблема', ' Проблема ', 'Решение', ''],
        forbiddenClaims: ['Сравнения по цене'],
      }),
    )
    expect(brief).toEqual({
      purpose: 'initiative',
      audience: 'Команда платформы',
      language: 'en',
      target_slide_count: 8,
      tone: 'деловой',
      mandatory_sections: ['Проблема', 'Решение'],
      forbidden_claims: ['Сравнения по цене'],
    })
  })

  it('fills optional fields with defaults the backend accepts', () => {
    const brief = toBrief(form({ audience: ' ', tone: '', slideCount: 99 }))
    expect(brief.audience).toBe('Руководство')
    expect(brief.tone).toBeNull()
    expect(brief.forbidden_claims).toBeNull()
    expect(brief.target_slide_count).toBe(40)
  })

  it('sends a custom purpose for «другое»', () => {
    expect(toBrief(form({ purpose: 'other', customPurpose: ' Отчёт для совета ' })).purpose).toBe('Отчёт для совета')
    expect(toBrief(form({ purpose: 'other', customPurpose: '' })).purpose).toBe('other')
  })

  it('refuses to build a brief without a purpose', () => {
    expect(() => toBrief(form({ purpose: null }))).toThrow()
    expect(toContentBrief(form({ purpose: null }))).not.toHaveProperty('purpose')
    expect(toContentBrief(form())).toHaveProperty('purpose', 'product')
  })

  it('requests all three strategies and the provider session only when it exists', () => {
    const brief = toBrief(form())
    const body = toGenerationBody({ brief, templateId: 'tpl_1', contentPackId: 'pack_1', useLlm: true, providerSessionId: 'ps_1' })
    expect(body).toEqual({
      template_id: 'tpl_1',
      content_pack_id: 'pack_1',
      brief,
      variants: [{ strategy: 'faithful' }, { strategy: 'balanced' }, { strategy: 'visual' }],
      use_llm: true,
      provider_session_id: 'ps_1',
    })
    expect(toGenerationBody({ brief, templateId: 'tpl_1', contentPackId: 'pack_1', useLlm: false, providerSessionId: 'ps_1' })).not.toHaveProperty(
      'provider_session_id',
    )
    expect(toGenerationBody({ brief, templateId: 'tpl_1', contentPackId: 'pack_1', useLlm: false, providerSessionId: null })).not.toHaveProperty(
      'provider_session_id',
    )
  })

  it('validates the required purpose and content', () => {
    expect(validateBrief(form({ purpose: null }), { kind: 'missing' })).toEqual({
      purpose: 'Выберите назначение презентации',
      content: 'Загрузите файл с контентом',
    })
    expect(validateBrief(form({ contentMode: 'text' }), { kind: 'missing' }).content).toBe('Вставьте текст контента')
    expect(validateBrief(form(), { kind: 'ready', packId: 'p', label: 'x' })).toEqual({})
  })
})

describe('content files', () => {
  it('turns pasted text into a markdown file named brief.md', async () => {
    const result = textToContentFile('# DeckDNA\n\nТекст')
    expect(result.name).toBe('brief.md')
    expect(result.type).toBe('text/markdown')
    expect(await readText(result)).toBe('# DeckDNA\n\nТекст')
  })

  it('uses only the first supported file and reports the rest', () => {
    const docx = file('brief.DOCX')
    const xlsx = file('metrics.xlsx')
    const png = file('logo.png')
    expect(partitionContentFiles([png, docx, xlsx])).toEqual({ primary: docx, ignored: [xlsx], unsupported: [png] })
    expect(partitionContentFiles([png]).primary).toBeNull()
  })
})

describe('content resolution', () => {
  const parsed = (sourceKey: string): ParsedContent => ({ packId: 'pack_1', sourceKey, label: 'brief.docx', sizeBytes: 10 })

  it('uploads a new file and reuses one that was already parsed', () => {
    const docx = file('brief.docx')
    expect(resolveContent({ mode: 'file', files: [docx], text: '', parsed: null })).toMatchObject({ kind: 'upload', file: docx, label: 'brief.docx' })
    expect(resolveContent({ mode: 'file', files: [docx], text: '', parsed: parsed(fileContentKey(docx)) })).toEqual({
      kind: 'ready',
      packId: 'pack_1',
      label: 'brief.docx',
    })
  })

  it('re-uploads pasted text only after it changes', () => {
    const plan = resolveContent({ mode: 'text', files: [], text: ' Текст ', parsed: null })
    expect(plan).toMatchObject({ kind: 'upload', key: textContentKey('Текст') })
    expect(resolveContent({ mode: 'text', files: [], text: 'Текст', parsed: parsed(textContentKey('Текст')) }).kind).toBe('ready')
    expect(resolveContent({ mode: 'text', files: [], text: 'Другой', parsed: parsed(textContentKey('Текст')) }).kind).toBe('upload')
  })

  it('falls back to previously parsed content and reports missing content', () => {
    expect(resolveContent({ mode: 'text', files: [], text: '  ', parsed: parsed('project') }).kind).toBe('ready')
    expect(resolveContent({ mode: 'file', files: [file('logo.png')], text: '', parsed: null })).toEqual({ kind: 'missing' })
  })
})

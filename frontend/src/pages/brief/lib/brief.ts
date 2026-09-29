import type { Brief } from '@/entities/content-pack'
import type { GenerationCreate } from '@/entities/generation'
import { STRATEGY_ORDER } from '@/entities/variant'
import { clampSlideCount, DEFAULT_AUDIENCE } from '../model/form'
import type { BriefForm, ContentMode, ParsedContent } from '../model/form'

export const CONTENT_EXTENSIONS = ['.md', '.txt', '.json', '.docx', '.pdf', '.xlsx'] as const
export const TEXT_CONTENT_FILENAME = 'brief.md'

export interface BriefErrors {
  purpose?: string
  content?: string
}

export interface ContentFiles {
  primary: File | null
  ignored: File[]
  unsupported: File[]
}

export type ContentPlan =
  | { kind: 'missing' }
  | { kind: 'ready'; packId: string; label: string }
  | { kind: 'upload'; file: File; key: string; label: string }

function cleanList(values: readonly string[]): string[] {
  const seen = new Set<string>()
  const result: string[] = []
  for (const raw of values) {
    const value = raw.trim()
    if (value && !seen.has(value)) {
      seen.add(value)
      result.push(value)
    }
  }
  return result
}

export function purposeValue(form: Pick<BriefForm, 'purpose' | 'customPurpose'>): string | null {
  if (!form.purpose) return null
  if (form.purpose === 'other') return form.customPurpose.trim() || 'other'
  return form.purpose
}

export function validateBrief(form: BriefForm, content: ContentPlan): BriefErrors {
  const errors: BriefErrors = {}
  if (!form.purpose) errors.purpose = 'Выберите назначение презентации'
  if (content.kind === 'missing') {
    errors.content = form.contentMode === 'text' ? 'Вставьте текст контента' : 'Загрузите файл с контентом'
  }
  return errors
}

export function toBrief(form: BriefForm): Brief {
  const purpose = purposeValue(form)
  if (!purpose) throw new Error('Не выбрано назначение презентации')
  const forbidden = cleanList(form.forbiddenClaims)
  return {
    purpose,
    audience: form.audience.trim() || DEFAULT_AUDIENCE,
    language: form.language,
    target_slide_count: clampSlideCount(form.slideCount),
    tone: form.tone.trim() || null,
    mandatory_sections: cleanList(form.mandatorySections),
    forbidden_claims: forbidden.length > 0 ? forbidden : null,
  }
}

export function toContentBrief(form: BriefForm): Record<string, unknown> {
  if (purposeValue(form)) return { ...toBrief(form) }
  const brief: Record<string, unknown> = { ...toBrief({ ...form, purpose: 'other' }) }
  delete brief.purpose
  return brief
}

export interface GenerationBodyInput {
  brief: Brief
  templateId: string
  contentPackId: string
  useLlm: boolean
  providerSessionId?: string | null
}

export function toGenerationBody({ brief, templateId, contentPackId, useLlm, providerSessionId }: GenerationBodyInput): GenerationCreate {
  const body: GenerationCreate = {
    template_id: templateId,
    content_pack_id: contentPackId,
    brief,
    variants: STRATEGY_ORDER.map((strategy) => ({ strategy })),
    use_llm: useLlm,
  }
  if (useLlm && providerSessionId) body.provider_session_id = providerSessionId
  return body
}

export function textToContentFile(text: string, name = TEXT_CONTENT_FILENAME): File {
  return new File([text], name, { type: 'text/markdown' })
}

export function isSupportedContentFile(file: Pick<File, 'name'>): boolean {
  const name = file.name.toLowerCase()
  return CONTENT_EXTENSIONS.some((extension) => name.endsWith(extension))
}

export function fileExtension(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot >= 0 ? name.slice(dot + 1).toUpperCase() : ''
}

export function partitionContentFiles(files: readonly File[]): ContentFiles {
  let primary: File | null = null
  const ignored: File[] = []
  const unsupported: File[] = []
  for (const file of files) {
    if (!isSupportedContentFile(file)) unsupported.push(file)
    else if (primary) ignored.push(file)
    else primary = file
  }
  return { primary, ignored, unsupported }
}

function hashText(text: string): string {
  let hash = 5381
  for (let index = 0; index < text.length; index += 1) hash = ((hash << 5) + hash + text.charCodeAt(index)) | 0
  return `${text.length.toString(36)}.${(hash >>> 0).toString(36)}`
}

export function fileContentKey(file: Pick<File, 'name' | 'size' | 'lastModified'>): string {
  return `file:${file.name}:${file.size}:${file.lastModified}`
}

export function textContentKey(text: string): string {
  return `text:${hashText(text.trim())}`
}

interface ContentSource {
  mode: ContentMode
  files: readonly File[]
  text: string
  parsed: ParsedContent | null
}

export function resolveContent({ mode, files, text, parsed }: ContentSource): ContentPlan {
  if (mode === 'file') {
    const { primary } = partitionContentFiles(files)
    if (primary) {
      const key = fileContentKey(primary)
      if (parsed?.sourceKey === key) return { kind: 'ready', packId: parsed.packId, label: parsed.label }
      return { kind: 'upload', file: primary, key, label: primary.name }
    }
  } else if (text.trim()) {
    const key = textContentKey(text)
    if (parsed?.sourceKey === key) return { kind: 'ready', packId: parsed.packId, label: parsed.label }
    return { kind: 'upload', file: textToContentFile(text.trim()), key, label: 'Вставленный текст' }
  }
  return parsed ? { kind: 'ready', packId: parsed.packId, label: parsed.label } : { kind: 'missing' }
}

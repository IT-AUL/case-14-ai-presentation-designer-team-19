import type { IntakeFile, IntakeKind } from '@/entities/assistant'

export const INTAKE_ACCEPT = '.pptx,.potx,.md,.txt,.docx,.pdf,.csv,.xlsx,.json,.png,.jpg,.jpeg,.webp,.svg'

export const INTAKE_FORMATS: readonly string[] = ['.pptx', '.potx', '.md', '.txt', '.docx', '.pdf', '.csv', '.xlsx', '.json', '.png', '.jpg', '.webp', '.svg']

const IMAGE_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg', '.webp', '.svg'])

const ROLE_LABEL: Record<string, string> = {
  title: 'Обложки',
  section: 'Разделы',
  content: 'Рабочие',
  closing: 'Финал',
  divider: 'Разделители',
}

export function roleLabel(role: string): string {
  return ROLE_LABEL[role] ?? role
}

export function fileExtension(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot > 0 ? name.slice(dot).toLowerCase() : ''
}

export function isImageFile(name: string): boolean {
  return IMAGE_EXTENSIONS.has(fileExtension(name))
}

export function isIntakeFile(name: string): boolean {
  const ext = fileExtension(name)
  return ext !== '' && INTAKE_ACCEPT.split(',').includes(ext)
}

export function guessKind(name: string): IntakeKind {
  const ext = fileExtension(name)
  if (ext === '.pptx' || ext === '.potx') return 'template'
  if (IMAGE_EXTENSIONS.has(ext) || /brand|бренд|tokens|logo|логотип/i.test(name)) return 'brandbook'
  if (ext === '.csv' || ext === '.xlsx' || ext === '.json') return 'data'
  return 'content'
}

export function isContentFile(file: IntakeFile): boolean {
  return file.accepted && (file.kind === 'content' || file.kind === 'data')
}

export function mergeIntakeFiles(current: IntakeFile[], incoming: IntakeFile[]): IntakeFile[] {
  const names = new Set(incoming.map((file) => file.name))
  return [...current.filter((file) => !names.has(file.name)), ...incoming]
}

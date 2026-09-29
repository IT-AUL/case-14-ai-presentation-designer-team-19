import { formatBytes } from '@/shared/lib/format'

export const TEMPLATE_EXTENSIONS = ['.pptx', '.potx'] as const
export const TEMPLATE_MAX_BYTES = 200 * 1024 * 1024
export const TEMPLATE_ACCEPT = [
  ...TEMPLATE_EXTENSIONS,
  'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  'application/vnd.openxmlformats-officedocument.presentationml.template',
].join(',')

export function validateTemplateFile(file: Pick<File, 'name' | 'size'>): string | null {
  const name = file.name.toLowerCase()
  if (!TEMPLATE_EXTENSIONS.some((extension) => name.endsWith(extension))) {
    return `Нужен файл .pptx или .potx — «${file.name}» не подойдёт.`
  }
  if (file.size === 0) return `Файл «${file.name}» пустой.`
  if (file.size > TEMPLATE_MAX_BYTES) return `Файл весит ${formatBytes(file.size)}, а предел — 200 МБ.`
  return null
}

export function pickTemplateFile(files: FileList | readonly File[] | null | undefined): { file: File | null; error: string | null } {
  const list = files ? Array.from(files) : []
  if (!list.length) return { file: null, error: null }
  if (list.length > 1) return { file: null, error: 'Перетащите один файл шаблона.' }
  const [file] = list as [File]
  const error = validateTemplateFile(file)
  return error ? { file: null, error } : { file, error: null }
}

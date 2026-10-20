export type FileKind = 'template' | 'content' | 'data' | 'brandbook' | 'unknown'

export const FILE_KIND_LABEL: Record<FileKind, string> = {
  template: 'Шаблон',
  content: 'Текст / слайды',
  data: 'Данные',
  brandbook: 'Брендбук',
  unknown: 'Не распознан',
}

export const FILE_KIND_OPTIONS: readonly FileKind[] = ['template', 'content', 'data', 'brandbook']

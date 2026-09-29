export interface ReproductionInput {
  templateFile: string | null
  contentFile: string | null
  outputDir?: string
  strategy: string | null
  slideCount: number | null
}

export interface ReproductionCommand {
  command: string
  placeholders: string[]
}

export const TEMPLATE_PLACEHOLDER = '<template.pptx>'
export const CONTENT_PLACEHOLDER = '<content>'
const DEFAULT_OUTPUT = 'out'
const SAFE_ARGUMENT = /^[A-Za-z0-9_@%+=:,./-]+$/

export function shellQuote(value: string): string {
  if (value.length > 0 && SAFE_ARGUMENT.test(value)) return value
  return `'${value.replace(/'/g, `'\\''`)}'`
}

function baseName(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).at(-1) ?? path
}

export function buildReproductionCommand({ templateFile, contentFile, outputDir, strategy, slideCount }: ReproductionInput): ReproductionCommand {
  const placeholders: string[] = []
  const argument = (value: string | null, placeholder: string) => {
    const name = value?.trim() ? baseName(value.trim()) : null
    if (name) return shellQuote(name)
    placeholders.push(placeholder)
    return placeholder
  }

  const parts = ['deckdna', 'generate', argument(templateFile, TEMPLATE_PLACEHOLDER), argument(contentFile, CONTENT_PLACEHOLDER), shellQuote(outputDir?.trim() || DEFAULT_OUTPUT)]
  if (strategy?.trim()) parts.push('--strategy', shellQuote(strategy.trim()))
  if (slideCount !== null && Number.isInteger(slideCount) && slideCount > 0) parts.push('--slides', String(slideCount))

  return { command: parts.join(' '), placeholders }
}

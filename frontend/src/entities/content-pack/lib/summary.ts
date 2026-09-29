import type { ContentPack } from '../model/types'

export interface ContentSummary {
  title: string | null
  sections: { id: string; heading: string; level: number }[]
  words: number
  numbers: number
  tables: number
  charts: number
  diagrams: number
  assets: number
  warnings: string[]
}

const WORD = /[\p{L}\p{N}]+/gu
const NUMBER = /\d+(?:[.,]\d+)?\s*%?/g

export function summarizeContentPack(pack: ContentPack): ContentSummary {
  const texts = pack.sections.flatMap((section) => [
    section.heading,
    ...section.blocks.flatMap((block) => [block.text ?? '', ...(block.items ?? [])]),
  ])
  const joined = texts.join(' ')
  return {
    title: pack.title_hint ?? null,
    sections: pack.sections.map((section) => ({ id: section.id, heading: section.heading, level: section.level ?? 1 })),
    words: joined.match(WORD)?.length ?? 0,
    numbers: joined.match(NUMBER)?.length ?? 0,
    tables: pack.tables.length,
    charts: pack.charts.length,
    diagrams: pack.diagrams.length,
    assets: pack.assets.length,
    warnings: pack.warnings.map((warning) => warning.message),
  }
}

import type { Schemas } from '@/shared/api'

export type TemplateAsset = Schemas['TemplateAsset']
export type TemplateDetail = Schemas['TemplateDetail']
export type DesignDna = Schemas['DesignDNAOut']

export interface PackageInventory {
  slides?: number
  masters?: number
  layouts?: number
  themes?: number
  media?: number
  charts?: number
  tables?: number
  embeddings?: number
  layout_usage?: Record<string, number>
  dominant_layout?: [string, number]
  declared_fonts?: string[]
  observed_fonts?: Record<string, number>
  theme_palettes?: Record<string, Record<string, string>>
  observed_colors?: Record<string, number>
}

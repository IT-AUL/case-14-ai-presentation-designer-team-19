import type { TemplateDetail } from '../model/types'

export type AnalysisState = 'missing' | 'running' | 'failed' | 'completed'

export function analysisState(detail: TemplateDetail | undefined): AnalysisState {
  return detail?.latest_analysis?.status ?? 'missing'
}

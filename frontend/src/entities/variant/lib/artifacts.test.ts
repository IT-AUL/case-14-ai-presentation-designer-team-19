import fixtures from '@/shared/api/mocks/fixtures/variants.json'
import type { ExportRecord, VariantSummary } from '../model/types'
import { resolveDeckFiles } from './artifacts'
import { variantMetrics } from './metrics'

const faithful = fixtures.faithful.variant as VariantSummary
const faithfulExport = fixtures.faithful.export as ExportRecord
const allExports = [fixtures.faithful.export, fixtures.balanced.export, fixtures.visual.export] as ExportRecord[]

describe('deck files of a variant', () => {
  it('finds the recorded PPTX, PDF and passport artifacts', () => {
    const files = resolveDeckFiles(faithful, allExports)
    expect(files.pptx?.artifactId).toBe('art_ea848c4e2ad14ae3ab284ac4962162f4')
    expect(files.pptx?.exportId).toBeNull()
    expect(files.pdf).toMatchObject({ artifactId: 'art_80d0825f54124e0aa801ba4f915e3535', exportId: faithful.export_ids[0], deckRevision: 1, sizeBytes: 324225 })
    expect(files.quality_passport?.artifactId).toBe('art_170013f6c51f43bb8864c46ec04d383a')
    expect(files.html).toBeNull()
  })

  it('ignores exports of other variants', () => {
    const balanced = fixtures.balanced.variant as VariantSummary
    expect(resolveDeckFiles(balanced, allExports).pdf?.artifactId).toBe('art_e947e6ee7fb4449b80aece2402aaa19b')
    expect(resolveDeckFiles({ ...faithful, id: 'var_unknown' }, allExports).pdf).toBeNull()
  })

  it('prefers the newest deck revision', () => {
    const repaired: ExportRecord = {
      ...faithfulExport,
      id: 'exp_rev2',
      deck_revision: 2,
      artifacts: faithfulExport.artifacts.filter((artifact) => artifact.format === 'pdf').map((artifact) => ({ ...artifact, artifact_id: 'art_pdf_rev2' })),
    }
    const files = resolveDeckFiles(faithful, [faithfulExport, repaired])
    expect(files.pdf).toMatchObject({ artifactId: 'art_pdf_rev2', deckRevision: 2 })
    expect(files.quality_passport?.deckRevision).toBe(1)
  })

  it('attaches export metadata to the current deck when it was exported', () => {
    const pptxExport: ExportRecord = {
      ...faithfulExport,
      id: 'exp_pptx',
      artifacts: [{ format: 'pptx', artifact_id: faithful.deck_artifact_id ?? '', sha256: 'abc', size_bytes: 17057765, mime_type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation', download_url: '' }],
    }
    expect(resolveDeckFiles(faithful, [pptxExport]).pptx).toMatchObject({ exportId: 'exp_pptx', sizeBytes: 17057765 })
    expect(resolveDeckFiles({ ...faithful, deck_artifact_id: null }, [pptxExport]).pptx?.artifactId).toBe(faithful.deck_artifact_id)
  })
})

describe('variant metrics', () => {
  it('normalizes recorded metrics', () => {
    expect(variantMetrics(faithful)).toEqual({
      validity: 1,
      opensCleanly: true,
      editabilityLevel: 3,
      editabilityFraction: 0.6,
      issuesTotal: 92,
      contextualIssues: null,
      styleFidelity: null,
    })
  })

  it('keeps missing metrics unknown', () => {
    expect(variantMetrics({ metrics: null, contextual_issues: null })).toMatchObject({ validity: null, opensCleanly: null, editabilityLevel: null })
  })
})

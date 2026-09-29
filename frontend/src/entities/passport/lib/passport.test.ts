import recorded from '@/shared/api/mocks/fixtures/passport.json'
import { parsePassport, PassportFormatError } from './passport'

describe('quality passport parsing', () => {
  it('reads the recorded passport', () => {
    const passport = parsePassport(recorded)
    expect(passport.inputs.templateName).toBe('VK Tech шаблон.pptx')
    expect(passport.validity).toEqual({ opensCleanly: true, ooxmlErrors: 0, roundTripOk: true })
    expect(passport.editability).toEqual({ peiLevel: 3, nativeTextRatio: 0.4824, rasterOnlySlides: 0 })
    expect(passport.contentSupport).toEqual({ supportedClaims: 193, unsupportedClaims: null, numbersVerified: null, numbersFailed: null })
    expect(passport.readability).toEqual({ contrastFailures: 18, overflowCount: 23, avgOccupancy: null })
    expect(passport.timings.totalSeconds).toBeCloseTo(2.66, 2)
    expect(passport.timings.perStage).toEqual([])
    expect(passport.issues).toEqual({ blocker: 0, error: 54, warning: 54, info: 0, unresolved: 108 })
  })

  it('keeps sections the backend does not compute yet empty', () => {
    const passport = parsePassport(recorded)
    expect(passport.styleFidelity).toBeNull()
    expect(passport.usage).toBeNull()
    expect(passport.fallbacks).toEqual([])
  })

  it('exposes provenance for traceability', () => {
    const { provenance, exports } = parsePassport(recorded)
    expect(provenance.pipelineVersion).toBe('deckdna-poc/0.1.0')
    expect(provenance.skillVersion).toBe('0.1.0')
    expect(provenance.promptVersions).toEqual({})
    expect(Object.keys(provenance.configHashes)).toEqual(['configs/generation.default.yaml', 'configs/audit.default.yaml'])
    expect(exports).toEqual([{ format: 'pptx', artifactId: 'deck.pptx', sha256: recorded.exports[0]?.sha256, sizeBytes: 17057765 }])
  })

  it('reads optional sections once the backend fills them', () => {
    const passport = parsePassport({
      ...recorded,
      metrics: {
        ...recorded.metrics,
        style_fidelity: { palette_compliance: 0.97, font_compliance: 1 },
        usage: { total_tokens: 48200, model_calls: 27 },
        timings: { total_seconds: 91, per_stage: { plan: 38, layout: 21 } },
      },
      fallbacks: ['chart rendered as table', { stage: 'layout', reason: 'no matching layout' }],
      provenance: {
        ...recorded.provenance,
        prompt_versions: { planner: 'v0.2' },
        model_profiles: [{ role: 'text', model_id: 'Qwen/Qwen3-32B', temperature: 0.2 }],
      },
    })
    expect(passport.styleFidelity).toEqual([
      { key: 'palette_compliance', value: 0.97 },
      { key: 'font_compliance', value: 1 },
    ])
    expect(passport.usage).toHaveLength(2)
    expect(passport.timings.perStage).toEqual([
      { stage: 'plan', seconds: 38 },
      { stage: 'layout', seconds: 21 },
    ])
    expect(passport.fallbacks).toEqual([
      { label: 'chart rendered as table', detail: null },
      { label: 'layout', detail: 'no matching layout' },
    ])
    expect(passport.provenance.promptVersions).toEqual({ planner: 'v0.2' })
    expect(passport.provenance.modelProfiles).toEqual([
      { role: 'text', modelId: 'Qwen/Qwen3-32B', attributes: { role: 'text', model_id: 'Qwen/Qwen3-32B', temperature: '0.2' } },
    ])
  })

  it('rejects payloads that are not a passport', () => {
    expect(() => parsePassport(null)).toThrow(PassportFormatError)
    expect(() => parsePassport({ schema_version: '1.0' })).toThrow(PassportFormatError)
  })
})

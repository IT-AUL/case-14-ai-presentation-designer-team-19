import { isCapabilityAvailable } from './capabilities'

const capabilities = {
  use_llm: true,
  sse: false,
  export_formats: ['pptx', 'pdf', 'quality_passport'],
  exports: { html: { status: 'not_implemented' }, pdf: { available: true } },
  audit: { deterministic_rules: 24, contextual: 'enabled' },
}

describe('isCapabilityAvailable', () => {
  it('reads boolean flags', () => {
    expect(isCapabilityAvailable(capabilities, 'use_llm')).toBe(true)
    expect(isCapabilityAvailable(capabilities, 'sse')).toBe(false)
  })

  it('checks membership in listed values', () => {
    expect(isCapabilityAvailable(capabilities, 'export_formats.pdf')).toBe(true)
    expect(isCapabilityAvailable(capabilities, 'export_formats.html')).toBe(false)
  })

  it('understands nested status objects', () => {
    expect(isCapabilityAvailable(capabilities, 'exports.pdf')).toBe(true)
    expect(isCapabilityAvailable(capabilities, 'exports.html')).toBe(false)
    expect(isCapabilityAvailable(capabilities, 'audit.contextual')).toBe(true)
    expect(isCapabilityAvailable(capabilities, 'audit.deterministic_rules')).toBe(true)
  })

  it('treats unknown capabilities and missing data as unavailable', () => {
    expect(isCapabilityAvailable(capabilities, 'slide_previews')).toBe(false)
    expect(isCapabilityAvailable(undefined, 'use_llm')).toBe(false)
  })
})

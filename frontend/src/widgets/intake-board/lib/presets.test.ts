import { buildPresetTemplateFile, PRESET_TEMPLATE_NAME } from './presets'

function stubFetch(response: { ok: boolean; blob?: () => Promise<Blob> } | (() => Promise<never>)) {
  vi.stubGlobal('fetch', vi.fn(typeof response === 'function' ? response : async () => response))
}

describe('buildPresetTemplateFile', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('builds a pptx File from the bundled neutral template', async () => {
    stubFetch({ ok: true, blob: async () => new Blob([new Uint8Array([80, 75, 3, 4])]) })

    const file = await buildPresetTemplateFile()

    expect(file).not.toBeNull()
    expect(file?.name).toBe(PRESET_TEMPLATE_NAME)
    expect(file?.type).toBe('application/vnd.openxmlformats-officedocument.presentationml.presentation')
    expect(file?.size).toBe(4)
  })

  it('returns null when the asset cannot be fetched', async () => {
    stubFetch({ ok: false })
    expect(await buildPresetTemplateFile()).toBeNull()

    stubFetch(async () => {
      throw new Error('network down')
    })
    expect(await buildPresetTemplateFile()).toBeNull()
  })

  it('returns null for an empty asset payload', async () => {
    stubFetch({ ok: true, blob: async () => new Blob([]) })
    expect(await buildPresetTemplateFile()).toBeNull()
  })
})

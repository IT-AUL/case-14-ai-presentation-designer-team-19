import { pickTemplateFile, TEMPLATE_MAX_BYTES, validateTemplateFile } from './validateTemplateFile'

describe('validateTemplateFile', () => {
  it('accepts pptx and potx in any letter case', () => {
    expect(validateTemplateFile({ name: 'VK Tech шаблон.pptx', size: 20_525_772 })).toBeNull()
    expect(validateTemplateFile({ name: 'BRAND.POTX', size: 10 })).toBeNull()
  })

  it('rejects other formats, empty files and files over 200 MB', () => {
    expect(validateTemplateFile({ name: 'deck.key', size: 10 })).toMatch(/\.pptx или \.potx/)
    expect(validateTemplateFile({ name: 'deck.ppt', size: 10 })).toMatch(/не подойдёт/)
    expect(validateTemplateFile({ name: 'deck.pptx', size: 0 })).toMatch(/пустой/)
    expect(validateTemplateFile({ name: 'deck.pptx', size: TEMPLATE_MAX_BYTES + 1 })).toMatch(/предел — 200 МБ/)
  })
})

describe('pickTemplateFile', () => {
  const file = (name: string, size = 10) => new File([new Uint8Array(size)], name)

  it('picks a single valid file', () => {
    const picked = pickTemplateFile([file('a.pptx')])
    expect(picked.error).toBeNull()
    expect(picked.file?.name).toBe('a.pptx')
  })

  it('reports multiple or invalid files and ignores an empty drop', () => {
    expect(pickTemplateFile([file('a.pptx'), file('b.pptx')]).error).toMatch(/один файл/)
    expect(pickTemplateFile([file('a.pdf')]).file).toBeNull()
    expect(pickTemplateFile([])).toEqual({ file: null, error: null })
  })
})

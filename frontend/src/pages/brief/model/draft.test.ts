import { readBriefDraft, restoreBriefForm } from './draft'

describe('brief draft', () => {
  afterEach(() => window.sessionStorage.clear())

  it('starts from project defaults and the project content pack', () => {
    const form = readBriefDraft('prj_1', { language: 'en', targetSlideCount: 15, contentPackId: 'pack_1' })
    expect(form).toMatchObject({ purpose: null, language: 'en', slideCount: 15, contentMode: 'file', useLlm: false })
    expect(form.parsed).toMatchObject({ packId: 'pack_1', sourceKey: 'project' })
  })

  it('restores a saved draft and drops malformed values', () => {
    window.sessionStorage.setItem(
      'deckdna.brief-draft.v1:prj_1',
      JSON.stringify({ purpose: 'feature', audience: 'Инвесторы', slideCount: 2, language: 'de', mandatorySections: ['Итоги', 3], contentMode: 'text', text: 'abc' }),
    )
    const form = readBriefDraft('prj_1', { language: 'ru', targetSlideCount: 12 })
    expect(form).toMatchObject({ purpose: 'feature', audience: 'Инвесторы', slideCount: 3, language: 'ru', mandatorySections: ['Итоги'], contentMode: 'text', text: 'abc' })
  })

  it('keeps the project content pack when the draft has none', () => {
    expect(restoreBriefForm({ parsed: null }, { contentPackId: 'pack_9' }).parsed?.packId).toBe('pack_9')
    expect(restoreBriefForm({ purpose: 'unknown' }).purpose).toBeNull()
  })
})

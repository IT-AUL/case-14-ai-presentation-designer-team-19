import { generationFixture as generation, passportFixture as passport, templateDetailFixture as template, variantFixtures as variants } from '@/shared/api/mocks'
import { buildReproductionCommand, CONTENT_PLACEHOLDER, shellQuote, TEMPLATE_PLACEHOLDER } from './reproductionCommand'

describe('shellQuote', () => {
  it('keeps safe arguments as is', () => {
    expect(shellQuote('deck_v2.pptx')).toBe('deck_v2.pptx')
    expect(shellQuote('out/balanced')).toBe('out/balanced')
  })

  it('quotes spaces, unicode and single quotes', () => {
    expect(shellQuote('VK Tech шаблон.pptx')).toBe("'VK Tech шаблон.pptx'")
    expect(shellQuote("it's.md")).toBe("'it'\\''s.md'")
    expect(shellQuote('')).toBe("''")
  })
})

describe('buildReproductionCommand', () => {
  it('builds the real CLI form from recorded run data', () => {
    const { command, placeholders } = buildReproductionCommand({
      templateFile: template.filename,
      contentFile: null,
      strategy: variants.balanced.variant.strategy,
      slideCount: generation.deck_plan?.brief.target_slide_count ?? null,
    })

    expect(command).toBe(`deckdna generate 'VK Tech шаблон.pptx' ${CONTENT_PLACEHOLDER} out --strategy balanced --slides 12`)
    expect(placeholders).toEqual([CONTENT_PLACEHOLDER])
  })

  it('never emits a seed flag or the legacy run subcommand', () => {
    const { command } = buildReproductionCommand({ templateFile: passport.inputs.template_name, contentFile: 'brief.md', strategy: 'visual', slideCount: 10 })

    expect(command).toBe("deckdna generate 'VK Tech шаблон.pptx' brief.md out --strategy visual --slides 10")
    expect(command).not.toMatch(/--seed|deckdna run/)
  })

  it('uses only the file name of a path and a custom output directory', () => {
    const { command } = buildReproductionCommand({
      templateFile: 'C:\\decks\\corp.potx',
      contentFile: '/tmp/content/notes.md',
      outputDir: 'result dir',
      strategy: 'faithful',
      slideCount: 5,
    })

    expect(command).toBe("deckdna generate corp.potx notes.md 'result dir' --strategy faithful --slides 5")
  })

  it('marks unknown inputs as placeholders and skips unknown options', () => {
    const { command, placeholders } = buildReproductionCommand({ templateFile: '  ', contentFile: null, strategy: null, slideCount: null })

    expect(command).toBe(`deckdna generate ${TEMPLATE_PLACEHOLDER} ${CONTENT_PLACEHOLDER} out`)
    expect(placeholders).toEqual([TEMPLATE_PLACEHOLDER, CONTENT_PLACEHOLDER])
  })
})

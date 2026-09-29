import capabilitiesFixture from '@/shared/api/mocks/fixtures/capabilities.json'
import { capabilityRows, countRules, describeManifest, formatRows, ruleGroups, versionRows } from './about'

describe('about panel data', () => {
  it('labels known version fields and skips nested values', () => {
    expect(versionRows({ skill_version: '0.1.0', service: 'deckdna-api', extra: 'x', nested: { a: 1 } } as never)).toEqual([
      { label: 'Сервис', value: 'deckdna-api' },
      { label: 'Скилл', value: '0.1.0' },
      { label: 'extra', value: 'x' },
    ])
    expect(versionRows(undefined)).toEqual([])
  })

  it('marks html export as coming soon when the service does not offer it', () => {
    const rows = capabilityRows({ exports: { pptx: true, pdf: true, html: false }, audit: { contextual_rules: 10 } })
    const state = Object.fromEntries(rows.map((row) => [row.id, row.state]))
    expect(state).toMatchObject({ pptx: 'on', pdf: 'on', html: 'soon', contextual: 'on', plan: 'soon' })
    expect(rows.find((row) => row.id === 'html')?.description).toBe('в разработке')
    expect(capabilityRows(null).every((row) => row.state === 'soon')).toBe(true)
  })

  it('reads accepted upload formats when present', () => {
    expect(formatRows({ uploads: { template_extensions: ['.pptx', '.potx'] } })).toEqual([{ label: 'Форматы шаблона', value: '.pptx .potx' }])
    expect(formatRows({})).toEqual([])
  })

  it('describes the skill manifest with versioned groups', () => {
    const view = describeManifest({
      name: 'deckdna',
      version: '0.1.0',
      entrypoint: 'SKILL.md',
      tools: ['layout_compiler'],
      prompts: [{ name: 'plan', version: '2', path: 'prompts/plan.md' }],
      provenance: { rule_version: 'audit-basic/0.1.0' },
    })
    expect(view).toMatchObject({ name: 'deckdna', version: '0.1.0', meta: [{ label: 'entrypoint', value: 'SKILL.md' }] })
    expect(view.groups.map((group) => group.title)).toEqual(['Промпты', 'Инструменты', 'Версии компонентов'])
    expect(view.groups[0]?.items[0]).toEqual({ name: 'plan', version: '2', detail: 'prompts/plan.md' })
    expect(view.groups[2]?.items[0]).toEqual({ name: 'rule_version', version: 'audit-basic/0.1.0', detail: null })
  })

  it('groups the 24 deterministic and the contextual audit rules', () => {
    const groups = ruleGroups()
    expect(countRules(groups).D).toBe(24)
    expect(countRules(groups).N).toBeGreaterThan(0)
    expect(groups.find((group) => group.category === 'meaning')?.rules.every((rule) => rule.kind === 'N')).toBe(true)
  })

  it('recognises the capabilities shape the live backend returns', () => {
    const state = Object.fromEntries(capabilityRows(capabilitiesFixture).map((row) => [row.id, row.state]))
    expect(state).toMatchObject({ pptx: 'on', pdf: 'on', passport: 'on', html: 'soon' })
  })

  it('turns features on from the explicit features block', () => {
    const features = { html_export: true, plan_only: true, png_previews: true, async_generation: true, sse_progress: true, contextual_audit: true }
    expect(capabilityRows({ features }).every((row) => ['html', 'plan', 'previews', 'async', 'sse', 'contextual'].includes(row.id) ? row.state === 'on' : true)).toBe(true)
  })
})

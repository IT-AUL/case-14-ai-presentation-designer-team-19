import { applyRuleCatalog, isAutoFixable, listRules, parseRuleCatalog, ruleMeta, RULES } from './rules'

afterEach(() => applyRuleCatalog(null))

describe('rule catalog', () => {
  it('uses the local dictionary until the service sends a catalog', () => {
    expect(ruleMeta('text.overflow').name).toBe(RULES['text.overflow']?.name)
    expect(isAutoFixable('text.overflow')).toBe(true)
    expect(listRules()).toHaveLength(Object.keys(RULES).length)
  })

  it('takes titles, categories and repairability from the service catalog', () => {
    applyRuleCatalog(
      parseRuleCatalog({
        items: [
          { code: 'text.overflow', title_ru: 'Текст не помещается в рамку', category: 'text', deterministic: true, fix_title_ru: 'Расширить рамку' },
          { code: 'template.color_palette', title_ru: 'Цвет вне палитры', category: 'brand', repairable: false },
          { code: 'new.rule', title_ru: 'Новое правило', category: 'unknown' },
        ],
      }),
    )
    expect(ruleMeta('text.overflow')).toMatchObject({ name: 'Текст не помещается в рамку', autoFix: 'Расширить рамку', repairable: true })
    expect(isAutoFixable('template.color_palette')).toBe(false)
    expect(ruleMeta('new.rule').category).toBe('integrity')
    expect(isAutoFixable('accessibility.contrast')).toBe(false)
    expect(listRules().map(([code]) => code)).toEqual(['text.overflow', 'template.color_palette', 'new.rule'])
  })

  it('accepts a bare array and ignores entries without a code', () => {
    expect(parseRuleCatalog([{ code: 'a' }, { title_ru: 'x' }, 'junk'])).toEqual([{ code: 'a' }])
    expect(parseRuleCatalog(null)).toEqual([])
  })
})

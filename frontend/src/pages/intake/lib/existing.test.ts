import { describe, expect, it } from 'vitest'
import { datasetDimensionsForFile, existingFiles, formatDatasetDimensions, topicFromBrief, withoutColorCodes } from './existing'

describe('topicFromBrief', () => {
  it('keeps a written topic and drops a bare purpose code', () => {
    expect(topicFromBrief({ purpose: 'показать рост клиентов по кварталам' })).toBe('показать рост клиентов по кварталам')
    expect(topicFromBrief({ purpose: 'presentation' })).toBe('')
    expect(topicFromBrief(undefined)).toBe('')
  })
})

describe('existingFiles', () => {
  it('lists the template, datasets and brandbook already in the project', () => {
    expect(existingFiles({ template: 'vk.pptx', brandbook: 'Северный ветер', datasets: [{ title: 'Продажи' }], sections: 2 })).toEqual([
      { name: 'vk.pptx', kind: 'template' },
      { name: 'Продажи', kind: 'data' },
      { name: 'Северный ветер', kind: 'brandbook' },
    ])
  })

  it('includes N строк · M колонок when dataset dimensions are present', () => {
    expect(
      existingFiles({
        template: null,
        brandbook: null,
        datasets: [{ title: 'Продажи', row_count: 240, columns: [{}, {}, {}, {}, {}, {}] }],
        sections: 1,
      }),
    ).toEqual([{ name: 'Продажи', kind: 'data', meta: '240 строк · 6 колонок' }])
  })

  it('names plain text materials when there is no table or when hasText is true alongside datasets', () => {
    expect(existingFiles({ template: null, brandbook: null, datasets: [], sections: 3 })).toEqual([{ name: 'Текст материалов', kind: 'content' }])
    expect(
      existingFiles({
        template: 'vk.pptx',
        brandbook: null,
        datasets: [{ title: 'Продажи' }],
        sections: 2,
        hasText: true,
      }),
    ).toEqual([
      { name: 'vk.pptx', kind: 'template' },
      { name: 'Продажи', kind: 'data' },
      { name: 'Текст материалов', kind: 'content' },
    ])
  })
})

describe('formatDatasetDimensions and datasetDimensionsForFile', () => {
  it('formats rows and columns with Russian pluralization', () => {
    expect(formatDatasetDimensions({ row_count: 1, columns: [{}] })).toBe('1 строка · 1 колонка')
    expect(formatDatasetDimensions({ row_count: 240, columns: [{}, {}, {}, {}, {}, {}] })).toBe('240 строк · 6 колонок')
    expect(
      datasetDimensionsForFile('purchases.xlsx', [
        { title: 'Закупки', file_name: 'purchases.xlsx', row_count: 120, columns: [{}, {}, {}] },
      ]),
    ).toBe('120 строк · 3 колонки')
  })
})

describe('withoutColorCodes', () => {
  it('drops raw colour codes from the assistant note', () => {
    expect(withoutColorCodes('Шаблон: 54 слайда, главный цвет #0077FF, шрифт Arial.')).toBe('Шаблон: 54 слайда, шрифт Arial.')
    expect(withoutColorCodes('Акцент #E4572E совпадает')).toBe('Акцент совпадает')
  })
})

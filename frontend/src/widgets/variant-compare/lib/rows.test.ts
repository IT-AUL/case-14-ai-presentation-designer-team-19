import { describe, expect, it } from 'vitest'
import { variantFixtures } from '@/shared/api/mocks'
import { alignRows, type AlignableSlide } from './rows'

interface TestSlide extends AlignableSlide {
  id: string
}

const bare = (variant: string, index: number): TestSlide => ({ id: `${variant}-${index}`, index })

const deck = (variant: string, slides: readonly [string, string][]): TestSlide[] =>
  slides.map(([purpose, title], index) => ({ id: `${variant}-${index}`, index, purpose, title, slide_plan_id: `slide-${index}` }))

const ids = (cells: readonly (TestSlide | null)[]) => cells.map((cell) => cell?.id ?? null)

describe('alignRows', () => {
  it('keeps equal decks row by row', () => {
    const slides: [string, string][] = [
      ['title', 'Обложка'],
      ['problem', 'Проблема'],
    ]
    const rows = alignRows([deck('a', slides), deck('b', slides), deck('c', slides)])
    expect(rows.map((row) => ids(row.cells))).toEqual([
      ['a-0', 'b-0', 'c-0'],
      ['a-1', 'b-1', 'c-1'],
    ])
    expect(rows.map((row) => row.purpose)).toEqual(['title', 'problem'])
  })

  it('opens a gap where one strategy adds an agenda slide instead of shifting the rest', () => {
    const faithful = deck('f', [
      ['title', 'Обложка'],
      ['problem', 'Проблема'],
      ['solution', 'Решение'],
    ])
    const balanced = deck('b', [
      ['title', 'Обложка'],
      ['agenda', 'План презентации'],
      ['problem', 'Проблема'],
      ['solution', 'Решение'],
    ])
    const rows = alignRows([faithful, balanced])
    expect(rows.map((row) => ids(row.cells))).toEqual([
      ['f-0', 'b-0'],
      [null, 'b-1'],
      ['f-1', 'b-2'],
      ['f-2', 'b-3'],
    ])
    expect(rows[1]?.purpose).toBe('agenda')
  })

  it('pairs a split slide with its original and leaves the continuation alone', () => {
    const split = deck('s', [
      ['solution', 'Архитектура'],
      ['solution', 'Архитектура — часть 2'],
      ['overview', 'Итоги'],
    ])
    const whole = deck('w', [
      ['solution', 'Архитектура'],
      ['overview', 'Итоги'],
    ])
    expect(alignRows([split, whole]).map((row) => ids(row.cells))).toEqual([
      ['s-0', 'w-0'],
      ['s-1', null],
      ['s-2', 'w-1'],
    ])
  })

  it('ignores per-plan slide ids unless the variants share one plan', () => {
    const a = deck('a', [
      ['title', 'Обложка'],
      ['problem', 'Проблема'],
    ])
    const b = deck('b', [
      ['agenda', 'Содержание'],
      ['title', 'Обложка'],
      ['problem', 'Проблема'],
    ])
    expect(alignRows([a, b]).map((row) => ids(row.cells))).toEqual([
      [null, 'b-0'],
      ['a-0', 'b-1'],
      ['a-1', 'b-2'],
    ])
    expect(alignRows([a, b], { sharedPlan: true }).map((row) => ids(row.cells))).toEqual([
      ['a-0', 'b-0'],
      ['a-1', 'b-1'],
      [null, 'b-2'],
    ])
  })

  it('falls back to the slide index when slides carry no plan data', () => {
    const rows = alignRows([[bare('a', 2), bare('a', 0)], [bare('b', 1)]])
    expect(rows.map((row) => ids(row.cells))).toEqual([
      ['a-0', null],
      [null, 'b-1'],
      ['a-2', null],
    ])
  })

  it('pads shorter decks with gaps when indexes are the only signal', () => {
    const rows = alignRows([[bare('a', 0)], [bare('b', 0), bare('b', 1), bare('b', 2)], [bare('c', 0), bare('c', 1)]])
    expect(rows.map((row) => ids(row.cells))).toEqual([
      ['a-0', 'b-0', 'c-0'],
      [null, 'b-1', 'c-1'],
      [null, 'b-2', null],
    ])
  })

  it('keeps a column for decks that are still loading', () => {
    const rows = alignRows([undefined, [bare('b', 0)], null])
    expect(rows.map((row) => ids(row.cells))).toEqual([[null, 'b-0', null]])
  })

  it('returns no rows when nothing is loaded', () => {
    expect(alignRows([undefined, [], null])).toEqual([])
  })

  it('aligns the recorded decks so the agenda slide does not shift the story', () => {
    const columns = (['faithful', 'balanced', 'visual'] as const).map((strategy) => variantFixtures[strategy].slides)
    const rows = alignRows(columns)
    const titles = rows.map((row) => row.cells.map((cell) => cell?.title ?? null))

    expect(titles.find((row) => row[1] === 'План презентации')).toEqual([null, 'План презентации', 'План презентации'])
    expect(titles.find((row) => row[0] === 'Проблема')).toEqual(['Проблема', 'Проблема', 'Проблема'])
    expect(titles.at(-1)).toEqual(['Спасибо за внимание', 'Спасибо за внимание', 'Спасибо за внимание'])
    for (const row of rows) {
      const present = row.cells.filter((cell) => cell !== null)
      expect(new Set(present.map((cell) => cell.purpose)).size).toBe(1)
    }
  })
})

export interface AlignableSlide {
  index: number
  slide_plan_id?: string | null
  purpose?: string | null
  title?: string | null
}

export interface AlignedRow<T> {
  key: string
  purpose: string | null
  cells: (T | null)[]
}

export interface AlignOptions {
  sharedPlan?: boolean
}

const TITLE_PART = /\s*[—–-]\s*(часть|part)\s*\d+\s*$/i

function normalizeTitle(title: string | null | undefined): string {
  return (title ?? '').replace(TITLE_PART, '').replace(/\s+/g, ' ').trim().toLowerCase()
}

function exactTitle(title: string | null | undefined): string {
  return (title ?? '').replace(/\s+/g, ' ').trim().toLowerCase()
}

function hasMeaning(slide: AlignableSlide): boolean {
  return Boolean(slide.purpose?.trim()) || exactTitle(slide.title) !== ''
}

function matchScore(a: AlignableSlide, b: AlignableSlide, sharedPlan: boolean): number {
  if (sharedPlan && a.slide_plan_id && a.slide_plan_id === b.slide_plan_id) return 8
  if (!hasMeaning(a) && !hasMeaning(b)) return a.index === b.index ? 1 : 0
  const samePurpose = Boolean(a.purpose) && a.purpose === b.purpose
  const sameTitle = exactTitle(a.title) !== '' && exactTitle(a.title) === exactTitle(b.title)
  const similarTitle = normalizeTitle(a.title) !== '' && normalizeTitle(a.title) === normalizeTitle(b.title)
  if (sameTitle) return samePurpose ? 6 : 4
  if (similarTitle) return samePurpose ? 3 : 2
  return samePurpose ? 1 : 0
}

interface Row<T> {
  cells: (T | null)[]
}

function rowScore<T extends AlignableSlide>(row: Row<T>, slide: T, sharedPlan: boolean): number {
  let best = 0
  for (const cell of row.cells) if (cell) best = Math.max(best, matchScore(cell, slide, sharedPlan))
  return best
}

function rowIndex<T extends AlignableSlide>(row: Row<T>): number {
  return row.cells.find((cell): cell is T => cell !== null)?.index ?? 0
}

function rowFirst<T extends AlignableSlide>(row: Row<T>, slide: T, skipRow: number, skipSlide: number): boolean {
  if (skipRow !== skipSlide) return skipRow > skipSlide
  return rowIndex(row) <= slide.index
}

function grid(rows: number, columns: number) {
  const values = new Array<number>((rows + 1) * (columns + 1)).fill(0)
  const index = (i: number, j: number) => i * (columns + 1) + j
  return {
    get: (i: number, j: number): number => values[index(i, j)] ?? 0,
    set: (i: number, j: number, value: number) => {
      values[index(i, j)] = value
    },
  }
}

function merge<T extends AlignableSlide>(rows: readonly Row<T>[], slides: readonly T[], width: number, sharedPlan: boolean): Row<T>[] {
  const n = rows.length
  const m = slides.length
  const best = grid(n, m)
  const pair = grid(n, m)

  rows.forEach((row, i) => {
    slides.forEach((slide, j) => pair.set(i, j, rowScore(row, slide, sharedPlan)))
  })
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      const matched = pair.get(i, j)
      const take = matched > 0 ? matched + best.get(i + 1, j + 1) : -1
      best.set(i, j, Math.max(take, best.get(i + 1, j), best.get(i, j + 1)))
    }
  }

  const merged: Row<T>[] = []
  let i = 0
  let j = 0
  while (i < n || j < m) {
    const row = rows[i]
    const slide = slides[j]
    const matched = row && slide ? pair.get(i, j) : 0
    if (row && slide && matched > 0 && best.get(i, j) === matched + best.get(i + 1, j + 1)) {
      merged.push({ cells: [...row.cells, slide] })
      i += 1
      j += 1
    } else if (row && (!slide || rowFirst(row, slide, best.get(i + 1, j), best.get(i, j + 1)))) {
      merged.push({ cells: [...row.cells, null] })
      i += 1
    } else if (slide) {
      merged.push({ cells: [...new Array<T | null>(width).fill(null), slide] })
      j += 1
    } else {
      break
    }
  }
  return merged
}

function rowKey<T extends AlignableSlide>(cells: readonly (T | null)[], position: number): string {
  const first = cells.find((cell): cell is T => cell !== null)
  return `${position}:${first?.slide_plan_id ?? first?.purpose ?? 'slide'}:${first?.index ?? position}`
}

function rowPurpose<T extends AlignableSlide>(cells: readonly (T | null)[]): string | null {
  const purposes = new Set(cells.flatMap((cell) => (cell?.purpose ? [cell.purpose] : [])))
  return purposes.size === 1 ? ([...purposes][0] ?? null) : null
}

export function alignRows<T extends AlignableSlide>(columns: readonly (readonly T[] | null | undefined)[], options: AlignOptions = {}): AlignedRow<T>[] {
  const sharedPlan = options.sharedPlan ?? false
  let rows: Row<T>[] = []
  columns.forEach((column, width) => {
    const slides = [...(column ?? [])].sort((a, b) => a.index - b.index)
    rows = merge(rows, slides, width, sharedPlan)
  })
  return rows.map((row, position) => ({ key: rowKey(row.cells, position), purpose: rowPurpose(row.cells), cells: row.cells }))
}

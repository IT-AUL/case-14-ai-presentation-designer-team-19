import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { BrandbookSummary, TemplateSummary } from '@/entities/assistant'
import { buildDesignSystem } from '@/entities/template'
import { designDnaFixture, templateDetailFixture } from '@/shared/api/mocks'
import { FileRows } from './FileRows'
import { TemplateTable } from './TemplateTable'

const summary: TemplateSummary = {
  template_id: 'tpl',
  filename: 'VK Tech шаблон.pptx',
  slides: 54,
  roles: { title: 2, section: 2, closing: 3, content: 47 },
  palette: ['#0077FF', '#FFD91D'],
  fonts: ['Arial', 'Play'],
  heading_font: 'Arial',
  body_font: 'Calibri',
  preview_artifact_ids: [],
  assistant_note: null,
}

const brandbook = {
  id: 'b',
  name: 'Северный ветер',
  palette: [],
  fonts: [],
  logos: [],
  rules: [],
  sources: [],
  warnings: [],
  priority: 'brandbook',
  assistant_note: null,
  conflicts: [
    { field: 'color.primary', kind: 'color', template: '#0077FF', brandbook: '#E4572E', resolved: 'brandbook', note: null },
    { field: 'font.heading', kind: 'font', template: 'Arial', brandbook: 'DejaVu Serif', resolved: 'brandbook', note: null },
  ],
} as unknown as BrandbookSummary

const system = buildDesignSystem(templateDetailFixture, designDnaFixture)

function renderTable(props: Partial<React.ComponentProps<typeof TemplateTable>> = {}) {
  return render(
    <TemplateTable
      summary={summary}
      system={system}
      previews={[]}
      aspectRatio={16 / 9}
      analyzing={false}
      brandbook={null}
      priority="brandbook"
      priorityControl={null}
      {...props}
    />,
  )
}

describe('TemplateTable passport', () => {
  it('shows palette shares, declared against observed fonts, and layouts by role', () => {
    renderTable()
    const passport = screen.getByRole('article', { name: 'Паспорт шаблона' })
    expect(passport).toHaveTextContent('VK Tech шаблон.pptx')
    expect(passport).toHaveTextContent('54 слайда · 16:9 · 39 макетов')
    expect(within(passport).getByRole('region', { name: 'Палитра' })).toHaveTextContent('%')
    const fonts = within(passport).getByRole('region', { name: 'Шрифты' })
    expect(fonts).toHaveTextContent('Заявлено в теме')
    expect(fonts).toHaveTextContent('Arial')
    expect(fonts).toHaveTextContent('На слайдах')
    expect(fonts).toHaveTextContent('Play 53%')
    expect(fonts).toHaveTextContent('не совпадают')
    const roles = within(passport).getByRole('region', { name: 'Макеты по ролям' })
    expect(roles).toHaveTextContent('2Обложки')
    expect(roles).toHaveTextContent('47Рабочие')
    expect(roles).toHaveTextContent('3Финал')
  })

  it('never prints a hex code', () => {
    renderTable({ brandbook })
    expect(screen.getByRole('article', { name: 'Паспорт шаблона' }).textContent).not.toMatch(/#[0-9a-f]{6}/i)
  })

  it('makes light swatches visible and labels them by name', () => {
    renderTable()
    const chips = screen.getAllByRole('img').filter((node) => node.className.includes('paletteChip'))
    expect(chips.length).toBeGreaterThan(0)
    for (const chip of chips) expect(chip).toHaveAttribute('aria-label')
  })

  it('draws no slide when the backend gives no previews, and shows them when it does', () => {
    const { rerender } = renderTable()
    expect(screen.queryByRole('list', { name: 'Слайды шаблона' })).not.toBeInTheDocument()
    rerender(
      <TemplateTable summary={summary} system={system} previews={['art_1', 'art_2']} aspectRatio={16 / 9} analyzing={false} brandbook={null} priority="brandbook" priorityControl={null} />,
    )
    const images = within(screen.getByRole('list', { name: 'Слайды шаблона' })).getAllByRole('img')
    expect(images).toHaveLength(2)
    expect(images[0]).toHaveAttribute('src', '/api/v1/artifacts/art_1/download')
    fireEvent.error(images.at(0) as HTMLElement)
    expect(within(screen.getByRole('list', { name: 'Слайды шаблона' })).getAllByRole('img')).toHaveLength(1)
  })

  it('degrades to a compact card from the summary while the analysis is absent', () => {
    renderTable({ system: null })
    const passport = screen.getByRole('article', { name: 'Паспорт шаблона' })
    expect(passport).toHaveTextContent('54 слайда')
    expect(within(passport).getByRole('region', { name: 'Палитра' })).toHaveTextContent('Цвета темы шаблона')
    expect(within(passport).getByRole('region', { name: 'Шрифты' })).toHaveTextContent('Arial, Calibri')
    expect(screen.queryByText('На слайдах')).not.toBeInTheDocument()
  })

  it('lists brandbook conflicts with the chosen side marked and exposes the priority control', () => {
    const { rerender } = renderTable({ brandbook, priorityControl: <div role="radiogroup" aria-label="Чьи цвета и шрифты важнее" /> })
    const brand = screen.getByRole('region', { name: /Брендбук/ })
    expect(brand).toHaveTextContent('Расходится с шаблоном в 2 местах. Что важнее?')
    expect(within(brand).getByRole('radiogroup', { name: 'Чьи цвета и шрифты важнее' })).toBeInTheDocument()
    expect(brand).toHaveTextContent('Главный цвет')
    expect(brand).toHaveTextContent('Шрифт заголовков')
    expect(brand).toHaveTextContent('DejaVu Serif')
    expect(brand.querySelectorAll('[data-chosen]')).toHaveLength(2)
    expect(brand.querySelector('[data-chosen]')).toHaveTextContent('Брендбук')
    rerender(
      <TemplateTable summary={summary} system={system} previews={[]} aspectRatio={16 / 9} analyzing={false} brandbook={brandbook} priority="template" priorityControl={null} />,
    )
    expect(screen.getByRole('region', { name: /Брендбук/ }).querySelector('[data-chosen]')).toHaveTextContent('Шаблон')
  })

  it('renders the analyze brandbook button and displays extracted colors and fonts with quotes and pages', () => {
    const onAnalyzeBrandbook = vi.fn()
    const analyzedBrandbook = {
      ...brandbook,
      palette: [
        { hex: '#E4572E', name: 'Медный север', role: 'primary', source: 'brandbook.pdf:page=3:vision: Основной цвет бренда — медный север #E4572E' },
      ],
      fonts: [
        { family: 'DejaVu Serif', role: 'heading', source: 'brandbook.pdf:page=7:vision: Заголовки слайдов набираются гарнитурой DejaVu Serif', fallback: 'Georgia' },
      ],
    } as unknown as BrandbookSummary

    renderTable({ brandbook: analyzedBrandbook, onAnalyzeBrandbook })
    const analyzeButton = screen.getByRole('button', { name: 'Разобрать брендбук' })
    fireEvent.click(analyzeButton)
    expect(onAnalyzeBrandbook).toHaveBeenCalledOnce()

    const brand = screen.getByRole('region', { name: /Брендбук/ })
    expect(within(brand).getByRole('list', { name: 'Цвета брендбука' })).toHaveTextContent('Главный цвет')
    expect(within(brand).getByRole('list', { name: 'Цвета брендбука' })).toHaveTextContent('стр. 3')
    expect(within(brand).getByRole('list', { name: 'Цвета брендбука' })).toHaveTextContent('«Основной цвет бренда — медный север»')
    expect(within(brand).getByRole('list', { name: 'Шрифты брендбука' })).toHaveTextContent('DejaVu Serif')
    expect(within(brand).getByRole('list', { name: 'Шрифты брендбука' })).toHaveTextContent('стр. 7')
    expect(within(brand).getByRole('list', { name: 'Шрифты брендбука' })).toHaveTextContent('«Заголовки слайдов набираются гарнитурой DejaVu Serif»')
    expect(brand.textContent).not.toMatch(/#[0-9a-f]{6}/i)
  })

  it('offers the rules download only when the analysis can be exported', () => {
    const onDownload = vi.fn()
    const { rerender } = renderTable()
    expect(screen.queryByRole('button', { name: 'Скачать правила (JSON)' })).not.toBeInTheDocument()
    rerender(<TemplateTable summary={summary} system={system} previews={[]} aspectRatio={16 / 9} analyzing={false} brandbook={null} priority="brandbook" priorityControl={null} onDownload={onDownload} />)
    fireEvent.click(screen.getByRole('button', { name: 'Скачать правила (JSON)' }))
    expect(onDownload).toHaveBeenCalledOnce()
  })

  it('shows a waiting tile before there is a summary', () => {
    renderTable({ summary: null, analyzing: true })
    expect(screen.getByText('Читаю шаблон')).toBeInTheDocument()
  })
})

describe('FileRows', () => {
  it('proposes brandbook for .png, .jpg, and .webp files and shows clear rejection reason near the file', () => {
    const onKindChange = vi.fn()
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(
      <QueryClientProvider client={client}>
        <FileRows
          files={[
            { name: 'brand-sheet.webp', kind: 'unknown', detected_kind: 'unknown', accepted: true, reason: null, size_bytes: 2048 },
            { name: 'scan.png', kind: 'brandbook', detected_kind: 'brandbook', accepted: false, reason: 'Не удалось прочитать картинку как брендбук', size_bytes: 1024 },
          ]}
          pending={[]}
          datasets={[]}
          onKind={onKindChange}
          onRemove={vi.fn()}
        />
      </QueryClientProvider>,
    )
    const webpSelect = screen.getByRole('combobox', { name: 'Что это за файл: brand-sheet.webp' })
    expect(webpSelect).toHaveValue('brandbook')
    const pngSelect = screen.getByRole('combobox', { name: 'Что это за файл: scan.png' })
    expect(pngSelect).toHaveValue('brandbook')
    expect(screen.getByRole('alert')).toHaveTextContent('Не удалось прочитать картинку как брендбук')
  })
})

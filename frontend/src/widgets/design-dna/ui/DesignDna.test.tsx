import { render, screen, within } from '@testing-library/react'
import type { DesignDna as DesignDnaData, TemplateDetail } from '@/entities/template'
import { designDnaFixture, templateDetailFixture } from '@/shared/api/mocks'
import { DesignDna } from './DesignDna'

const detail: TemplateDetail = templateDetailFixture
const dna: DesignDnaData = designDnaFixture
const WIDTH = dna.slide_size.width_emu
const HEIGHT = dna.slide_size.height_emu

const filled: DesignDnaData = {
  ...dna,
  declared: {
    ...dna.declared,
    layouts: dna.declared.layouts.map((layout) =>
      layout.part.endsWith('slideLayout11.xml')
        ? { ...layout, name: '1_Контент', type: 'obj', placeholders: [{ type: 'title', idx: 0, bbox: { x: WIDTH * 0.06, y: HEIGHT * 0.08, w: WIDTH * 0.8, h: HEIGHT * 0.12 } }] }
        : layout,
    ),
  },
  observed: {
    ...dna.observed,
    font_sizes: [
      { size_pt: 18, frequency: 23, roles: ['title'] },
      { size_pt: 13.22, frequency: 16, roles: [] },
    ],
  },
  anchors: [{ kind: 'logo', bbox: { x: 0.84, y: 0.04, w: 0.12, h: 0.08 }, slide_coverage: 0.94 }],
  slide_roles: [{ role: 'title', slide_ids: ['s1', 's2'], confidence: 0.62, evidence: 'крупный заголовок' }],
  unsupported_features: [{ feature: 'SmartArt', location: 'слайд 12', strategy: 'raster_fallback', disclosure: null }],
}

describe('DesignDna on the recorded VK Tech analysis', () => {
  beforeEach(() => {
    render(<DesignDna detail={detail} dna={dna} />)
  })

  it('shows the file strip and the structure counts', () => {
    expect(screen.getByText('VK Tech шаблон.pptx')).toBeInTheDocument()
    expect(screen.getByText('16:9 · 25,40 × 14,29 см')).toBeInTheDocument()
    expect(screen.getByText('2 мастера · 39 макетов · 54 слайда')).toBeInTheDocument()
  })

  it('summarizes what was understood without invented rule counts', () => {
    const panel = screen.getByRole('region', { name: 'Что система поняла' })
    expect(within(panel).getByText('54')).toBeInTheDocument()
    expect(within(panel).queryByText(/146/)).not.toBeInTheDocument()
    expect(within(panel).getByText(/Заявлен/)).toHaveTextContent('Заявлен Arial, на слайдах Play (53%) и Calibri (41%).')
    expect(panel).toHaveTextContent('36 из 54 слайдов собраны на одном макете 11.')
    expect(panel).toHaveTextContent('21 из 39 макетов не используются ни на одном слайде.')
    expect(panel).not.toHaveTextContent('нет данных')
    expect(panel).not.toHaveTextContent('Шкала кеглей')
  })

  it('renders the palette with theme slots and observed colors', () => {
    const palette = screen.getByRole('region', { name: 'Палитра' })
    expect(within(palette).getByText('12 слотов темы «VK Tech» · 10 цветов реально используется')).toBeInTheDocument()
    expect(within(palette).getAllByText('#0077FF')).toHaveLength(2)
    expect(within(palette).getAllByText('вне темы').length).toBeGreaterThan(0)
  })

  it('highlights the declared versus observed font mismatch', () => {
    const fonts = screen.getByRole('region', { name: 'Шрифты' })
    expect(within(fonts).getByText('заявлено ≠ фактически')).toBeInTheDocument()
    expect(within(fonts).getByText('Play · основной')).toBeInTheDocument()
    expect(within(fonts).getByText('Фактически · 1 396 фрагментов')).toBeInTheDocument()
    expect(within(fonts).getByText('На Arial приходится 1 фрагмент текста из 1 396.')).toBeInTheDocument()
  })

  it('ranks layouts by usage and hides blocks without data', () => {
    const layouts = screen.getByRole('region', { name: 'Макеты' })
    expect(within(layouts).getByText('18 из 39 макетов используются на слайдах')).toBeInTheDocument()
    expect(within(layouts).getByText('Макет 11')).toBeInTheDocument()
    expect(within(layouts).getByText('36 слайдов · 67%')).toBeInTheDocument()
    for (const hidden of ['Шкала кеглей', 'Сетка, поля и якоря', 'Якоря', 'Роли слайдов', 'Что система не умеет', 'Мастер → макеты → слайды']) {
      expect(screen.queryByRole('heading', { name: hidden })).not.toBeInTheDocument()
    }
  })
})

describe('DesignDna on a filled DNA', () => {
  it('draws blueprints, scale, anchors, roles and unsupported features', () => {
    render(<DesignDna detail={detail} dna={filled} />)
    expect(screen.getByRole('img', { name: 'Схема макета 1_Контент' })).toHaveTextContent('заголовок')
    expect(within(screen.getByRole('region', { name: 'Шкала кеглей' })).getByText('дробный кегль')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Якоря' })).toBeInTheDocument()
    expect(screen.getByText('Лого · 94%')).toBeInTheDocument()
    const roles = screen.getByRole('region', { name: 'Роли слайдов' })
    expect(within(roles).getByText('Титул')).toBeInTheDocument()
    expect(within(roles).getByText('0.62')).toBeInTheDocument()
    expect(screen.getByText('растрируется, в отчёте')).toBeInTheDocument()
  })
})

import { render, screen, within } from '@testing-library/react'
import { designDnaFixture, templateDetailFixture } from '@/shared/api/mocks'
import type { DesignDna } from '@/entities/template'
import { TemplateRules } from './TemplateRules'

describe('TemplateRules', () => {
  it('deduplicates theme palette swatches by human color name', () => {
    render(<TemplateRules detail={templateDetailFixture} dna={designDnaFixture} />)

    const list = screen.getByRole('list', { name: 'Палитра темы' })
    const items = within(list).getAllByRole('listitem')
    const labels = items.map((item) => item.textContent?.trim() ?? '')

    expect(new Set(labels).size).toBe(labels.length)
    expect(labels.filter((label) => label === 'белый')).toHaveLength(1)
    expect(labels.filter((label) => label === 'синий')).toHaveLength(1)
  })

  it('translates toc and contact slide roles and hides blank', () => {

    const dnaWithRoles: DesignDna = {
      ...designDnaFixture,
      slide_roles: [
        { role: 'title', slide_ids: ['s1'], confidence: 0.95 },
        { role: 'toc', slide_ids: ['s2'], confidence: 0.9 },
        { role: 'content', slide_ids: ['s3', 's4', 's5'], confidence: 0.85 },
        { role: 'contact', slide_ids: ['s6'], confidence: 0.9 },
        { role: 'blank', slide_ids: ['s7', 's8'], confidence: 0.8 },
      ],
    }

    render(<TemplateRules detail={templateDetailFixture} dna={dnaWithRoles} />)

    expect(screen.getByText(/Оглавление — 1/)).toBeInTheDocument()
    expect(screen.getByText(/Контакты — 1/)).toBeInTheDocument()
    expect(screen.queryByText(/blank/i)).not.toBeInTheDocument()
  })

  it('filters out decorative font sizes above 96 pt', () => {
    const dnaWithSizes: DesignDna = {
      ...designDnaFixture,
      observed: {
        ...designDnaFixture.observed,
        font_sizes: [
          { size_pt: 239, frequency: 2, roles: [] },
          { size_pt: 138, frequency: 4, roles: [] },
          { size_pt: 66, frequency: 8, roles: ['title'] },
          { size_pt: 48, frequency: 12, roles: ['title'] },
          { size_pt: 24, frequency: 30, roles: ['body'] },
          { size_pt: 14, frequency: 90, roles: ['body'] },
        ],
      },
    }

    render(<TemplateRules detail={templateDetailFixture} dna={dnaWithSizes} />)

    const sizesRow = screen.getByText(/Размеры текста:/)
    expect(sizesRow).toHaveTextContent('Размеры текста: 66, 48, 24, 14 пт')
    expect(sizesRow).not.toHaveTextContent('239')
    expect(sizesRow).not.toHaveTextContent('138')
  })
})

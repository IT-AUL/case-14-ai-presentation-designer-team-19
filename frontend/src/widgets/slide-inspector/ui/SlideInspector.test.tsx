import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { buildIssueViews } from '@/entities/audit'
import { variantFixtures } from '@/shared/api/mocks'
import { SlideInspector, type SlideInspectorProps } from './SlideInspector'

vi.mock('@/shared/ui', async (original) => ({
  ...await original<Record<string, unknown>>(),
  PdfPage: ({ children }: { children?: ReactNode }) => <div>{children}</div>,
}))

it('starts with a clean slide, shows all frames on request, and preserves the focused issue when frames are hidden', async () => {
  const user = userEvent.setup()
  const issue = variantFixtures.balanced.issues.find((item) => item.bbox)
  if (!issue) throw new Error('Missing issue with bounds')
  const views = buildIssueViews([issue], [])
  const props: SlideInspectorProps = {
    pdfUrl: '/deck.pdf', pdfRevision: 1, imageUrl: null, imageRevision: null, revision: 1,
    slideNumber: 1, slideCount: 12, title: 'Продажи', views, journal: [], activeKey: null,
    hoverKey: null, mode: 'slide', onModeChange: vi.fn(), onPick: vi.fn(), onHover: vi.fn(), onSlideChange: vi.fn(),
  }
  const { rerender } = render(<SlideInspector {...props} />)
  const toggle = screen.getByRole('button', { name: 'Все рамки' })
  expect(toggle).toHaveAttribute('aria-pressed', 'false')
  expect(screen.queryByRole('button', { name: /^D ·/ })).not.toBeInTheDocument()
  await user.click(toggle)
  expect(screen.getByRole('button', { name: /^D ·/ })).toBeInTheDocument()
  await user.click(toggle)
  rerender(<SlideInspector {...props} activeKey={views[0]?.key ?? null} />)
  expect(screen.getByRole('button', { name: /^D ·/ })).toHaveAttribute('aria-pressed', 'true')
})

import { telltaleSlides } from './slides'

describe('telltaleSlides', () => {
  const slide = (index: number, purpose: string) => ({ id: `s${index}`, variant_id: 'v', index, slide_plan_id: null, purpose, title: null, revision: 1, preview_artifact_id: null })

  it('shows content slides first so the variants look different', () => {
    const slides = [slide(0, 'title'), slide(1, 'agenda'), slide(2, 'problem'), slide(3, 'data'), slide(4, 'thank_you')]
    expect(telltaleSlides(slides, 3).map((item) => item.index)).toEqual([2, 3, 0])
  })
})


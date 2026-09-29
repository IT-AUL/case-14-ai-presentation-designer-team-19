import { describe, expect, it } from 'vitest'
import { shouldIgnoreShortcut } from './keyboard'

function press(key: string, target: Element | null, modifiers: Partial<Record<'metaKey' | 'ctrlKey' | 'altKey', boolean>> = {}) {
  return shouldIgnoreShortcut({ key, target, defaultPrevented: false, metaKey: false, ctrlKey: false, altKey: false, ...modifiers })
}

function element(html: string, selector: string): Element {
  const host = document.createElement('div')
  host.innerHTML = html
  return host.querySelector(selector) as Element
}

describe('shouldIgnoreShortcut', () => {
  it('leaves arrows to focused buttons, radios, tabs and checkboxes', () => {
    expect(press('ArrowRight', element('<div role="radiogroup"><button role="radio">a</button></div>', '[role=radio]'))).toBe(true)
    expect(press('ArrowLeft', element('<div role="tablist"><button role="tab">a</button></div>', '[role=tab]'))).toBe(true)
    expect(press('ArrowDown', element('<button><span>a</span></button>', 'span'))).toBe(true)
    expect(press('ArrowUp', element('<a href="#">a</a>', 'a'))).toBe(true)
  })

  it('handles arrows on the page body and j/k on buttons', () => {
    expect(press('ArrowRight', document.body)).toBe(false)
    expect(press('j', element('<button>a</button>', 'button'))).toBe(false)
    expect(press('k', element('<div role="radio">a</div>', 'div'))).toBe(false)
  })

  it('never steals letters from text inputs or modified keys', () => {
    expect(press('j', element('<input />', 'input'))).toBe(true)
    expect(press('x', element('<textarea></textarea>', 'textarea'))).toBe(true)
    expect(press('ArrowRight', document.body, { metaKey: true })).toBe(true)
  })
})

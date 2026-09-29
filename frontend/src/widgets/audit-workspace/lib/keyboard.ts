const TEXT_ENTRY = 'input, textarea, select, [contenteditable=""], [contenteditable="true"], [role="dialog"], [role="textbox"]'

const INTERACTIVE = [
  TEXT_ENTRY,
  'button',
  'a[href]',
  'summary',
  '[role="radio"]',
  '[role="tab"]',
  '[role="checkbox"]',
  '[role="switch"]',
  '[role="slider"]',
  '[role="menuitem"]',
  '[role="option"]',
].join(', ')

const LETTER_KEYS = new Set(['j', 'k', 'x', 'Escape'])

export function shouldIgnoreShortcut(event: Pick<KeyboardEvent, 'key' | 'target' | 'defaultPrevented' | 'metaKey' | 'ctrlKey' | 'altKey'>): boolean {
  if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return true
  const target = event.target instanceof Element ? event.target : null
  if (!target) return false
  return Boolean(target.closest(LETTER_KEYS.has(event.key) ? TEXT_ENTRY : INTERACTIVE))
}

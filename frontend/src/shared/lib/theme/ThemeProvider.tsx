import { useCallback, useLayoutEffect, useMemo, useState, type ReactNode } from 'react'
import { readJson, writeJson } from '@/shared/lib/storage'
import { ThemeContext, type Theme } from './theme'

const STORAGE_KEY = 'deckdna.theme.v1'

function initialTheme(): Theme {
  return readJson<Theme | null>(STORAGE_KEY, null) === 'dark' ? 'dark' : 'light'
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(initialTheme)

  useLayoutEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next)
    writeJson(STORAGE_KEY, next)
  }, [])

  const toggleTheme = useCallback(() => setTheme(theme === 'dark' ? 'light' : 'dark'), [setTheme, theme])

  const value = useMemo(() => ({ theme, setTheme, toggleTheme }), [theme, setTheme, toggleTheme])

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

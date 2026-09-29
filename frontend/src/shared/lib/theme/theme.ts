import { createContext, useContext } from 'react'

export type Theme = 'light' | 'dark'

export interface ThemeApi {
  theme: Theme
  setTheme: (theme: Theme) => void
  toggleTheme: () => void
}

export const ThemeContext = createContext<ThemeApi | null>(null)

export function useTheme(): ThemeApi {
  const context = useContext(ThemeContext)
  if (!context) throw new Error('useTheme must be used within ThemeProvider')
  return context
}

import { clsx } from 'clsx'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Link } from 'react-router'
import { routes } from '@/shared/config'
import { useTheme } from '@/shared/lib/theme'
import styles from './AppHeader.module.css'
import { Icon, LogoMark, Moon, Sun } from '@/shared/ui'

export type StatusTone = 'ok' | 'info' | 'warn' | 'error' | 'neutral'

export interface RunStatus {
  label: string
  tone: StatusTone
}

interface AppHeaderProps {
  projectName?: ReactNode
  status?: RunStatus
  providerConnected: boolean
  onOpenProvider: () => void
  onOpenAbout: () => void
}

export function AppHeader({ projectName, status, providerConnected, onOpenProvider, onOpenAbout }: AppHeaderProps) {
  const { theme, toggleTheme } = useTheme()

  return (
    <header className={styles.header}>
      <Link to={routes.projects()} className={styles.brand} aria-label="DeckDNA — к проектам">
        <span className={styles.logo}>
          <LogoMark size={20} />
        </span>
        <span className={styles.wordmark}>DeckDNA</span>
      </Link>
      {projectName !== undefined && (
        <nav aria-label="Навигация" className={styles.crumbs}>
          <span aria-hidden="true">/</span>
          <Link to={routes.projects()} className={styles.crumbLink}>
            Проекты
          </Link>
          <span aria-hidden="true">/</span>
          <span className={styles.crumbCurrent}>{projectName}</span>
        </nav>
      )}
      <div className={styles.spacer} />
      {status && (
        <div className={styles.status} data-tone={status.tone}>
          <span className={styles.statusDot} />
          <span>{status.label}</span>
        </div>
      )}
      <SettingsMenu providerConnected={providerConnected} onOpenProvider={onOpenProvider} onOpenAbout={onOpenAbout} />
      <button
        type="button"
        className={clsx(styles.action, styles.iconAction)}
        onClick={toggleTheme}
        aria-label="Переключить тему"
        title={theme === 'dark' ? 'Светлая тема' : 'Тёмная тема'}
        data-theme-target={theme === 'dark' ? 'light' : 'dark'}
      >
        <Icon as={theme === 'dark' ? Sun : Moon} size={15} />
      </button>
    </header>
  )
}

function SettingsMenu({ providerConnected, onOpenProvider, onOpenAbout }: Pick<AppHeaderProps, 'providerConnected' | 'onOpenProvider' | 'onOpenAbout'>) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (!open) return
    const onPointer = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      setOpen(false)
      triggerRef.current?.focus()
    }
    document.addEventListener('pointerdown', onPointer)
    document.addEventListener('keydown', onKey)
    rootRef.current?.querySelector<HTMLButtonElement>('[role="menuitem"]')?.focus()
    return () => {
      document.removeEventListener('pointerdown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const choose = (action: () => void) => {
    setOpen(false)
    action()
  }

  return (
    <div className={styles.menuRoot} ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className={styles.action}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <span className={styles.providerDot} data-connected={providerConnected} />
        <span>Настройки</span>
      </button>
      {open && (
        <div role="menu" aria-label="Настройки" className={styles.menu}>
          <button type="button" role="menuitem" className={styles.menuItem} onClick={() => choose(onOpenProvider)}>
            <span>Модели</span>
            <span className={styles.menuHint}>{providerConnected ? 'подключены' : 'не подключены'}</span>
          </button>
          <button type="button" role="menuitem" className={styles.menuItem} onClick={() => choose(onOpenAbout)}>
            О системе
          </button>
        </div>
      )}
    </div>
  )
}

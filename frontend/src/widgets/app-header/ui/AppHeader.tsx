import { clsx } from 'clsx'
import type { ReactNode } from 'react'
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
      <button type="button" className={styles.action} onClick={onOpenProvider} title="Провайдер моделей">
        <span className={styles.providerDot} data-connected={providerConnected} />
        <span>Модели</span>
      </button>
      <button type="button" className={styles.action} onClick={onOpenAbout}>
        О системе
      </button>
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

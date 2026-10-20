import { useId, type ReactNode } from 'react'
import styles from './IssuePanel.module.css'

export type PanelTab = 'issues' | 'slide' | 'journal'

interface IssuePanelProps {
  tab: PanelTab
  onTabChange: (tab: PanelTab) => void
  openCount: number
  journalCount: number
  issues: ReactNode
  journal: ReactNode
  slide?: ReactNode
}

export function IssuePanel({ tab, onTabChange, openCount, journalCount, issues, journal, slide }: IssuePanelProps) {
  const id = useId()
  const tabs: { value: PanelTab; label: string }[] = [
    ...(slide ? [{ value: 'slide' as const, label: 'Слайд' }] : []),
    { value: 'issues', label: `Проблемы · ${openCount}` },
    { value: 'journal', label: `Журнал · ${journalCount}` },
  ]

  return (
    <aside aria-label="Проблемы" className={styles.panel}>
      <header className={styles.panelHeader}>
        <h1>Доводка презентации</h1>
        <p>Выберите слайд и скажите, что улучшить.</p>
      </header>
      <div role="tablist" aria-label="Панель аудита" className={styles.tabs}>
        {tabs.map((item) => (
          <button
            key={item.value}
            type="button"
            role="tab"
            id={`${id}-${item.value}`}
            aria-controls={`${id}-${item.value}-panel`}
            aria-selected={tab === item.value}
            className={styles.tab}
            onClick={() => onTabChange(item.value)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={`${id}-${tab}-panel`} aria-labelledby={`${id}-${tab}`} className={styles.body}>
        {tab === 'issues' ? issues : tab === 'slide' && slide ? slide : journal}
      </div>
    </aside>
  )
}

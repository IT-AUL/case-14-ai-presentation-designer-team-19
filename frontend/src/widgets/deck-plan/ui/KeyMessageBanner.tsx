import { useState } from 'react'
import { Button } from '@/shared/ui'
import styles from './DeckPlanView.module.css'

interface KeyMessageBannerProps {
  keyMessage: string
  onSave?: (next: string) => void
  canUndo?: boolean
  onUndo?: () => void
  disabled?: boolean
}

export function KeyMessageBanner({ keyMessage, onSave, canUndo = false, onUndo, disabled = false }: KeyMessageBannerProps) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(keyMessage)

  const beginEdit = () => {
    if (!onSave || disabled) return
    setDraft(keyMessage)
    setEditing(true)
  }

  const commit = () => {
    const trimmed = draft.trim()
    setEditing(false)
    if (trimmed && trimmed !== keyMessage) {
      onSave?.(trimmed)
    }
  }

  const cancel = () => {
    setDraft(keyMessage)
    setEditing(false)
  }

  return (
    <section className={styles.keyMessageBanner} aria-label="Главная мысль колоды">
      <div className={styles.keyMessageContent}>
        <div className={styles.keyMessageEyebrow}>Главная мысль колоды</div>
        {editing ? (
          <div className={styles.keyMessageEditRow}>
            <input
              type="text"
              className={styles.keyMessageInput}
              aria-label="Текст главной мысли колоды"
              value={draft}
              autoFocus
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault()
                  commit()
                } else if (event.key === 'Escape') {
                  event.preventDefault()
                  cancel()
                }
              }}
            />
            <Button variant="primary" size="sm" onClick={commit}>
              Сохранить
            </Button>
            <Button variant="secondary" size="sm" onClick={cancel}>
              Отмена
            </Button>
          </div>
        ) : onSave ? (
          <button
            type="button"
            className={styles.keyMessageValueButton}
            disabled={disabled}
            onClick={beginEdit}
            title="Нажмите, чтобы изменить главную мысль колоды"
          >
            {keyMessage || 'Сформулировать главную мысль колоды…'}
          </button>
        ) : (
          <div className={styles.keyMessageValue}>{keyMessage}</div>
        )}
      </div>
      {!editing && (onSave || canUndo) && (
        <div className={styles.keyMessageActions}>
          {onSave && (
            <Button variant="secondary" size="sm" disabled={disabled} onClick={beginEdit}>
              Изменить мысль
            </Button>
          )}
          {canUndo && onUndo && (
            <Button variant="secondary" size="sm" disabled={disabled} onClick={onUndo} title="Отменить последнее действие (Ctrl+Z)">
              Отменить
            </Button>
          )}
        </div>
      )}
    </section>
  )
}

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Icon, X } from '../icon'
import { ToastContext, type ToastOptions } from './toast'
import styles from './Toast.module.css'

const TOAST_DURATION_MS = 2600

export function ToastProvider({ children }: { children: ReactNode }) {
  const [message, setMessage] = useState<string | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const timer = useRef<number | undefined>(undefined)

  const show = useCallback((next: string, options?: ToastOptions) => {
    if (options?.tone === 'error') {
      setErrorMessage(next)
      return
    }
    window.clearTimeout(timer.current)
    setMessage(next)
    timer.current = window.setTimeout(() => setMessage(null), TOAST_DURATION_MS)
  }, [])

  useEffect(() => () => window.clearTimeout(timer.current), [])

  const value = useMemo(() => ({ show }), [show])

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className={styles.region}>
        {errorMessage && (
          <div role="alert" className={styles.toast} data-tone="error">
            <span className={styles.message}>{errorMessage}</span>
            <button type="button" className={styles.close} onClick={() => setErrorMessage(null)} aria-label="Закрыть сообщение об ошибке">
              <Icon as={X} size={14} />
            </button>
          </div>
        )}
        <div role="status" aria-live="polite" className={styles.slot}>
          {message && <div className={styles.toast}>{message}</div>}
        </div>
      </div>
    </ToastContext.Provider>
  )
}

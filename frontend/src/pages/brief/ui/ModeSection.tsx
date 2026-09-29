import { useId, useState } from 'react'
import { useActiveProviderSession } from '@/entities/provider-session'
import { FEATURE_PATHS, useCapabilityFlag } from '@/entities/system'
import { Icon, Minus, Plus, Switch } from '@/shared/ui'
import styles from './BriefForm.module.css'

interface ModeSectionProps {
  useLlm: boolean
  onUseLlmChange: (value: boolean) => void
}

export function ModeSection({ useLlm, onUseLlmChange }: ModeSectionProps) {
  const [open, setOpen] = useState(false)
  const bodyId = useId()
  const session = useActiveProviderSession()
  const modelAuto = useCapabilityFlag(FEATURE_PATHS.modelAuto)
  const hasModel = Boolean(session) || modelAuto
  const tone = hasModel ? 'ok' : useLlm ? 'warn' : 'neutral'

  return (
    <section className={styles.mode}>
      <button type="button" className={styles.modeToggle} aria-expanded={open} aria-controls={bodyId} onClick={() => setOpen((value) => !value)}>
        <span className={styles.modeTitle}>Режим сборки</span>
        <span className={styles.modeLabel}>{useLlm ? 'С моделью · 1–5 мин' : 'Быстрый · секунды'}</span>
        <span className={styles.spacer} />
        <span className={styles.modeArrow} aria-hidden="true">
          <Icon as={open ? Minus : Plus} size={14} />
        </span>
      </button>
      {open && (
        <div id={bodyId} className={styles.modeBody}>
          <Switch
            checked={useLlm}
            onCheckedChange={onUseLlmChange}
            label="Использовать языковую модель"
            description="Модель пишет план и заголовки-выводы и проверяет смысл слайдов."
          />
          <div className={styles.provider} data-tone={tone} role="status">
            <span className={styles.providerDot} aria-hidden="true" />
            {session ? (
              <span className={styles.providerText}>
                <span className={styles.providerTitle}>Провайдер подключён · {session.label}</span>
                <span>
                  Текст: {session.models.text} · зрение: {session.models.vision}
                </span>
              </span>
            ) : modelAuto ? (
              <span className={styles.providerText}>
                <span className={styles.providerTitle}>Модель сервера подключена</span>
                <span>Сервер использует встроенную языковую модель.</span>
              </span>
            ) : (
              <span className={styles.providerText}>
                <span className={styles.providerTitle}>Своя модель не подключена</span>
                <span>Подключить можно в разделе «Модели» в шапке.</span>
              </span>
            )}
          </div>
        </div>
      )}
    </section>
  )
}


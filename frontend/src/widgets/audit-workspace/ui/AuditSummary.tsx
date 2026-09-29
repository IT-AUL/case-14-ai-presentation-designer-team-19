import { clsx } from 'clsx'
import { useId } from 'react'
import { Link } from 'react-router'
import {
  CATEGORY_LABEL,
  CATEGORY_ORDER,
  CheckKindBadge,
  SEVERITY,
  SEVERITY_ORDER,
  type AuditRun,
  type RuleCategory,
  type Severity,
} from '@/entities/audit'
import { strategyInfo, type VariantSummary } from '@/entities/variant'
import { ArrowRight, Check, Icon, Mono, RefreshCw } from '@/shared/ui'
import { CHECK_STATUS_LABEL } from '../model/labels'
import styles from './AuditSummary.module.css'

interface AuditSummaryProps {
  variants: readonly VariantSummary[]
  variantId: string
  onVariantChange: (variantId: string) => void
  audit: AuditRun
  severityCounts: Record<Severity, number>
  kindCounts: { D: number; N: number }
  fixableCount: number
  hasProvider: boolean
  contextualPending: boolean
  onRunContextual: () => void
  reauditPending: boolean
  onReaudit: () => void
  exportHref: string
}

export function AuditSummary(props: AuditSummaryProps) {
  const { variants, variantId, audit, severityCounts, kindCounts, fixableCount, hasProvider, contextualPending, reauditPending } = props
  const selectId = useId()
  const reasonId = useId()
  const current = variants.find((variant) => variant.id === variantId)
  const contextualDone = audit.contextual_status === 'completed'
  const contextualNote = contextualDone ? CHECK_STATUS_LABEL.completed : hasProvider ? CHECK_STATUS_LABEL[audit.contextual_status] : 'не запускалась'

  return (
    <section aria-label="Сводка аудита" className={styles.summary}>
      <div className={styles.block}>
        <label className={styles.caption} htmlFor={selectId}>
          Вариант · ревизия
        </label>
        <div className={styles.variantRow}>
          <span className={styles.selectWrap}>
            <select
              id={selectId}
              className={styles.select}
              value={variantId}
              onChange={(event) => props.onVariantChange(event.target.value)}
            >
              {(variants.length > 0 ? variants : current ? [current] : []).map((variant) => (
                <option key={variant.id} value={variant.id}>
                  {strategyInfo(variant.strategy).name}
                </option>
              ))}
              {variants.length === 0 && !current && <option value={variantId}>Вариант</option>}
            </select>
            <span aria-hidden className={styles.caret}>
              ▾
            </span>
          </span>
          <Mono className={styles.revision}>r{audit.deck_revision}</Mono>
        </div>
      </div>

      <div className={clsx(styles.block, styles.bordered, styles.severities)} aria-label="Открытые проблемы по серьёзности" role="group">
        {SEVERITY_ORDER.map((severity) => (
          <div key={severity} className={styles.tile} data-severity={severity}>
            <Mono className={styles.tileValue}>{severityCounts[severity]}</Mono>
            <span className={styles.tileLabel}>{SEVERITY[severity].shortLabel}</span>
          </div>
        ))}
      </div>

      <div className={clsx(styles.block, styles.bordered, styles.checks)} role="group" aria-label="Типы проверок">
        <div className={styles.checkRow} title="Детерминированные проверки: правило в коде, результат всегда один">
          <CheckKindBadge kind="D" />
          <span>правила</span>
          <Mono className={styles.checkCount}>{kindCounts.D}</Mono>
          <span className={styles.checkStatus}>{CHECK_STATUS_LABEL[audit.deterministic_status]}</span>
        </div>
        <div className={styles.checkRow} title="Контекстные проверки: модель отвечает на вопросы по картинке слайда">
          <CheckKindBadge kind="N" />
          <span>модель</span>
          <Mono className={styles.checkCount}>{contextualDone ? kindCounts.N : '—'}</Mono>
          <span className={styles.checkStatus}>{contextualNote}</span>
        </div>
      </div>

      <div className={clsx(styles.block, styles.bordered)}>
        <span className={styles.caption}>Автоисправимо</span>
        <Mono className={styles.value}>{fixableCount}</Mono>
      </div>

      <div className={styles.actions}>
        <button
          type="button"
          className={styles.action}
          onClick={props.onRunContextual}
          disabled={contextualPending || !hasProvider}
          aria-busy={contextualPending}
          aria-describedby={hasProvider ? undefined : reasonId}
          title={hasProvider ? 'Проверить смысл слайдов моделью' : 'Подключите модель в разделе «Модели» в шапке'}
        >
          <span className={styles.nMark} aria-hidden>
            N
          </span>
          <span className={styles.actionLabel}>{contextualPending ? 'Модель проверяет…' : 'Смысл моделью'}</span>
        </button>
        {!hasProvider && (
          <span id={reasonId} className={styles.reason}>
            нужна модель
          </span>
        )}
        <button
          type="button"
          className={styles.action}
          onClick={props.onReaudit}
          disabled={reauditPending}
          aria-busy={reauditPending}
          title="Повторный аудит"
        >
          <Icon as={RefreshCw} size={14} className={reauditPending ? styles.spin : undefined} />
          <span className={styles.actionLabel}>{reauditPending ? 'Идёт повторный аудит…' : 'Повторный аудит'}</span>
        </button>
        <Link to={props.exportHref} className={styles.primary}>
          Экспорт
          <Icon as={ArrowRight} size={14} />
        </Link>
      </div>
    </section>
  )
}

interface PolicyStripProps {
  counts: Record<RuleCategory, number>
  audit: AuditRun
  active: 'all' | RuleCategory
  onToggle: (category: RuleCategory) => void
}

export function PolicyStrip({ counts, audit, active, onToggle }: PolicyStripProps) {
  return (
    <div className={styles.policies} role="group" aria-label="Категории проверок">
      <span className={styles.policiesLabel}>Категории</span>
      {CATEGORY_ORDER.map((category) => {
        const checked = category === 'meaning' ? audit.contextual_status === 'completed' : audit.deterministic_status === 'completed'
        const count = counts[category]
        const state = !checked ? 'unchecked' : count === 0 ? 'clean' : 'issues'
        return (
          <button
            key={category}
            type="button"
            className={styles.policy}
            data-state={state}
            aria-pressed={active === category}
            onClick={() => onToggle(category)}
            title={!checked ? 'Проверки этой категории ещё не запускались' : undefined}
          >
            <span>{CATEGORY_LABEL[category]}</span>
            <Mono className={styles.policyValue}>{state === 'unchecked' ? '—' : state === 'clean' ? <Icon as={Check} size={12} strokeWidth={3} /> : count}</Mono>
          </button>
        )
      })}
    </div>
  )
}

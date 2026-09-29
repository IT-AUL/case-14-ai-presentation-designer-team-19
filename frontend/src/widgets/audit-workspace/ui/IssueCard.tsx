import { clsx } from 'clsx'
import { memo, useState } from 'react'
import {
  CATEGORY_LABEL,
  CheckKindBadge,
  checkKind,
  describeMeasurement,
  isPendingStatus,
  measurementSentence,
  plannedFix,
  ruleMeta,
  SeverityBadge,
  slideNumber,
  type AuditIssue,
  type DisplayStatus,
  type IssueCluster,
  type IssueView,
  type SelectionState,
} from '@/entities/audit'
import { DismissIssueForm } from '@/features/dismiss-issue'
import { Badge, CircleCheck, Icon, Mono, type Tone } from '@/shared/ui'
import { STATUS_LABEL } from '../model/labels'
import { IssueCheck } from './IssueCheck'
import styles from './IssueCard.module.css'

const STATUS_TONE: Record<DisplayStatus, Tone> = {
  open: 'neutral',
  selected: 'ink',
  fixed: 'ok',
  unresolved: 'error',
  dismissed: 'muted',
}

export const MANUAL_ONLY = 'Только вручную'

export type CardContext = 'rule' | 'other'

export interface IssueCardHandlers {
  auditId: string | undefined
  repairPending: boolean
  onActivate: (key: string) => void
  onHover: (key: string | null) => void
  onSelect: (issueIds: readonly string[], selected: boolean) => void
  onRepair: (issues: readonly AuditIssue[]) => void
  onDismissed: (issues: readonly AuditIssue[], reason: string) => void
}

export interface IssueCardProps extends IssueCardHandlers {
  cluster: IssueCluster
  context: CardContext
  active: boolean
  hovered: boolean
  selection: SelectionState
}

const SHAPE_REFERENCE = /\s*(?:во фрейме|в фигуре)?\s*'[^']*'/g

function readableMessage(message: string): string {
  return message.replace(SHAPE_REFERENCE, '').replace(/\s{2,}/g, ' ').trim()
}

function fixGuidance(view: IssueView): { title: string; text: string } {
  const { issue, selectable } = view
  const planned = plannedFix(issue)
  if (selectable && planned) return { title: 'Что будет сделано', text: planned }
  if (!issue.deterministic && planned) {
    return { title: 'Как исправить', text: `Модель предлагает: ${planned.charAt(0).toLowerCase()}${planned.slice(1)}.` }
  }
  return { title: 'Как исправить', text: 'Автоматической правки для этого правила нет — поправьте в PowerPoint или отклоните с причиной.' }
}

function slideLabel(issue: AuditIssue): string {
  const number = slideNumber(issue)
  return number ? `сл. ${String(number).padStart(2, '0')}` : 'колода'
}

function IssueCardView({ cluster, context, active, hovered, selection, auditId, repairPending, onActivate, onHover, onSelect, onRepair, onDismissed }: IssueCardProps) {
  const [dismissing, setDismissing] = useState(false)
  const { primary } = cluster
  const { issue, status, note, selectable } = primary
  const issues = cluster.views.map((view) => view.issue)
  const count = cluster.views.length
  const meta = ruleMeta(issue.rule_code)
  const measurement = describeMeasurement(issue)
  const sentence = measurementSentence(issue)
  const pending = isPendingStatus(status)
  const guidance = fixGuidance(primary)
  const headline = issue.deterministic ? (sentence ?? readableMessage(issue.message)) : issue.message
  const provenance = issue.deterministic
    ? issue.provenance?.rule_version
    : [issue.provenance?.model_id, issue.provenance?.prompt_version].filter(Boolean).join(' · ')
  const earlierDismissal = pending && note?.outcome === 'dismissed' ? note : null
  const shapeIds = [...new Set(issues.flatMap((item) => item.shape_ids ?? []))]
  const messages = [...new Set(issues.map((item) => item.message))]
  const evidence = issues.flatMap((item) => item.evidence ?? []).slice(0, 4)
  const multiple = count > 1

  return (
    <article
      className={clsx(styles.card, active && styles.active, hovered && styles.hovered)}
      data-issue-key={primary.key}
      aria-label={`${meta.name}, ${slideLabel(issue)}${multiple ? `, ${count} одинаковых` : ''}`}
      onMouseEnter={() => onHover(primary.key)}
      onMouseLeave={() => onHover(null)}
    >
      <div className={styles.head}>
        <span className={styles.check}>
          {selectable ? (
            <IssueCheck
              state={selection}
              label={`Выбрать для исправления: ${meta.name}${multiple ? ` ×${count}` : ''}`}
              onChange={(selected) => onSelect(issues.map((item) => item.id), selected)}
            />
          ) : pending ? (
            <IssueCheck state="none" label={`${MANUAL_ONLY}: ${meta.name}`} disabledReason={MANUAL_ONLY} />
          ) : null}
        </span>
        <button type="button" className={styles.toggle} aria-expanded={active} onClick={() => onActivate(primary.key)}>
          {context === 'rule' ? (
            <span className={styles.titleRow}>
              <CheckKindBadge kind={checkKind(issue)} />
              <span className={styles.headline}>{headline}</span>
              {multiple && <Mono className={styles.times}>×{count}</Mono>}
              <Mono className={styles.slide}>{slideLabel(issue)}</Mono>
            </span>
          ) : (
            <span className={styles.titleRow}>
              <SeverityBadge severity={issue.severity} short />
              <CheckKindBadge kind={checkKind(issue)} />
              <span className={styles.name}>{meta.name}</span>
              {multiple && <Mono className={styles.times}>×{count}</Mono>}
              <Mono className={styles.slide}>{slideLabel(issue)}</Mono>
            </span>
          )}
          {!active && (context === 'other' || status !== 'open') && (
            <span className={styles.summary}>
              {context === 'other' && <span className={styles.message}>{headline}</span>}
              <span className={styles.spacer} />
              <Badge tone={STATUS_TONE[status]} shape="tag" className={styles.status}>
                {STATUS_LABEL[status]}
              </Badge>
            </span>
          )}
        </button>
      </div>

      {active && (
        <div className={styles.details}>
          {context === 'rule' && <p className={styles.lead}>{meta.name}</p>}
          {measurement.hasValue && (
            <dl className={styles.measure}>
              <div className={styles.cell}>
                <dt>{measurement.quantity ?? 'Измерено'}</dt>
                <dd className={styles.measured} data-severity={issue.severity}>
                  {measurement.measured}
                </dd>
              </div>
              <div className={styles.cell}>
                <dt>Норма</dt>
                <dd>{measurement.threshold}</dd>
              </div>
            </dl>
          )}
          {multiple && <p className={styles.note}>Одинаковая проблема у {count} элементов на слайде — правка и отклонение применяются ко всем.</p>}
          {!issue.deterministic && typeof issue.confidence === 'number' && (
            <div className={styles.confidence}>
              <span className={styles.confidenceLabel}>Уверенность модели</span>
              <span className={styles.track}>
                <span className={styles.fill} style={{ width: `${Math.round(issue.confidence * 100)}%` }} />
              </span>
              <Mono>{issue.confidence.toFixed(2)}</Mono>
            </div>
          )}
          <div className={styles.plan}>
            <div className={styles.planTitle}>{guidance.title}</div>
            <div>{guidance.text}</div>
          </div>

          {status === 'unresolved' && (
            <div className={styles.failed}>
              <b>Не удалось исправить.</b>{' '}
              {note ? `Не исправилось в ревизии r${note.revision}.` : 'Правка не применилась.'} Поправьте вручную или отклоните.
            </div>
          )}
          {status === 'dismissed' && <div className={styles.dismissed}>Отклонено: {note?.reason ?? 'причина сохранена'}</div>}
          {status === 'fixed' && (
            <div className={styles.fixed}>
              <Icon as={CircleCheck} size={14} /> Исправлено{note ? ` в ревизии r${note.revision}` : ''}
            </div>
          )}
          {earlierDismissal && (
            <div className={styles.dismissed}>
              Ранее отклонено в r{earlierDismissal.revision}: {earlierDismissal.reason}. После новой ревизии сервис снова нашёл проблему.
            </div>
          )}

          {pending && auditId && !dismissing && (
            <div className={styles.actions}>
              {selectable && (
                <button type="button" className={styles.primary} disabled={repairPending} onClick={() => onRepair(issues)}>
                  {multiple ? `Исправить все (${count})` : 'Исправить'}
                </button>
              )}
              <button type="button" className={styles.secondary} onClick={() => setDismissing(true)}>
                Отклонить…
              </button>
            </div>
          )}
          {pending && auditId && dismissing && (
            <DismissIssueForm
              issues={issues}
              auditId={auditId}
              initialReason={earlierDismissal?.reason ?? ''}
              onCancel={() => setDismissing(false)}
              onDismissed={(updated, reason) => {
                setDismissing(false)
                onDismissed(updated, reason)
              }}
            />
          )}

          <details className={styles.tech}>
            <summary>Технические детали</summary>
            <div className={styles.techBody}>
              <div className={styles.meta}>
                <Mono>{issue.rule_code}</Mono>
                <span>{CATEGORY_LABEL[meta.category]}</span>
                <span>{issue.deterministic ? 'D · правило в коде' : 'N · проверяет модель'}</span>
                {provenance && <Mono>{provenance}</Mono>}
              </div>
              <ul className={styles.raw} aria-label="Сообщения сервиса">
                {messages.map((message) => (
                  <li key={message}>{message}</li>
                ))}
              </ul>
              {evidence.length > 0 && (
                <ul className={styles.raw} aria-label="Доказательства">
                  {evidence.map((item, index) => (
                    <li key={`${item.kind}-${index}`}>
                      {item.kind && <Mono>{item.kind}</Mono>} {item.detail ?? item.ref}
                    </li>
                  ))}
                </ul>
              )}
              {shapeIds.length > 0 && <Mono className={styles.shapes}>shape_ids: {shapeIds.join(', ')}</Mono>}
            </div>
          </details>
        </div>
      )}
    </article>
  )
}

function sameViews(a: readonly IssueView[], b: readonly IssueView[]): boolean {
  return a.length === b.length && a.every((view, index) => view === b[index])
}

export const IssueCard = memo(IssueCardView, (prev, next) => {
  const { cluster: prevCluster, ...prevRest } = prev
  const { cluster: nextCluster, ...nextRest } = next
  if (!sameViews(prevCluster.views, nextCluster.views)) return false
  return (Object.keys(prevRest) as (keyof typeof prevRest)[]).every((key) => prevRest[key] === nextRest[key])
})

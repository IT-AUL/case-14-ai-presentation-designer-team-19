import { clsx } from 'clsx'
import type { ReactNode } from 'react'
import { SEVERITY } from '@/entities/audit'
import { formatDateTime, formatIndex, formatNumber, formatPercent, pluralize } from '@/shared/lib/format'
import { AlertTriangle, Icon, Meter } from '@/shared/ui'
import type { PassportView } from '../model/passportView'
import styles from './QualityPassport.module.css'

interface QualityPassportProps {
  view: PassportView
  revisionLabel: string | null
  notice?: ReactNode
  actions?: ReactNode
}

interface Section {
  key: string
  title: ReactNode
  body: ReactNode
}

const STAGE_COLORS = ['var(--ink)', 'var(--ink-2)', 'var(--ink-3)', 'var(--line-2)']

const CLAIM_FORMS = ['утверждение', 'утверждения', 'утверждений'] as const
const NUMBER_FORMS = ['число', 'числа', 'чисел'] as const

function number(value: number | null): string {
  return value === null ? '—' : formatNumber(value)
}

function StyleBody({ view }: { view: PassportView }) {
  if (view.style.status === 'soon') return null
  return (
    <div className={styles.scores}>
      {view.style.scores.map((score) => (
        <div key={score.key} className={styles.score}>
          <div className={styles.scoreLabel}>{score.label}</div>
          <div className={styles.scoreValue}>{score.value}</div>
          <Meter value={score.fraction} height={4} label={score.label} />
        </div>
      ))}
    </div>
  )
}

function SourcesBody({ view }: { view: PassportView }) {
  const sources = view.sources
  if (!sources) return null
  const { supported, unsupported, total, numbersVerified, numbersFailed } = sources
  return (
    <p className={styles.text}>
      {numbersVerified !== null && (
        <>
          <b>{formatNumber(numbersVerified)}</b> {pluralize(numbersVerified, NUMBER_FORMS)} сверено с контентом
          {numbersFailed !== null && numbersFailed > 0 && (
            <>
              {' '}
              · <b>{formatNumber(numbersFailed)}</b> не совпало
            </>
          )}
          {(supported !== null || unsupported !== null) && ' · '}
        </>
      )}
      {supported !== null && (
        <>
          <b>{total !== null ? `${formatNumber(supported)} из ${formatNumber(total)}` : formatNumber(supported)}</b>{' '}
          {pluralize(total ?? supported, CLAIM_FORMS)} со ссылкой на фрагмент контента
        </>
      )}
      {supported !== null && unsupported !== null && ' · '}
      {unsupported !== null && (
        <>
          <b>{formatNumber(unsupported)}</b> без источника
        </>
      )}
      .
    </p>
  )
}

function ReadabilityBody({ view }: { view: PassportView }) {
  const { contrastFailures, overflowCount, avgOccupancy } = view.readability
  return (
    <p className={styles.text}>
      <b>{number(contrastFailures)}</b> провалов контраста · <b>{number(overflowCount)}</b> переполнений текста
      {avgOccupancy !== null && (
        <>
          {' '}
          · заполненность слайдов в среднем <b>{formatPercent(avgOccupancy)}</b>
        </>
      )}
      .
    </p>
  )
}

function IssuesBody({ view }: { view: PassportView }) {
  const { counts, total, unresolved } = view.issues
  return (
    <div className={styles.issues}>
      <div className={styles.issueCounts}>
        {counts.map(({ severity, count }) => (
          <span key={severity} className={clsx(styles.issueCount, count === 0 && styles.zero)}>
            <span className={styles.dot} style={{ background: SEVERITY[severity].color }} aria-hidden />
            {SEVERITY[severity].label} <span className={styles.mono}>{formatNumber(count)}</span>
          </span>
        ))}
      </div>
      <div className={styles.muted}>
        Всего {formatNumber(total)}
        {unresolved !== null && ` · не исправлено ${formatNumber(unresolved)}`}.
      </div>
    </div>
  )
}

function TimingTitle({ view }: { view: PassportView }) {
  const { timing, usage } = view
  return (
    <span className={styles.titleRow}>
      Время сборки
      {timing.total !== null && (
        <span className={styles.timingValue}>
          {timing.total} из {timing.budget}
        </span>
      )}
      {usage && <span className={styles.titleNote}>· {usage.map((item) => `${item.value} ${item.label.toLowerCase()}`).join(' · ')}</span>}
    </span>
  )
}

function TimingBody({ view }: { view: PassportView }) {
  const { timing } = view
  if (timing.totalSeconds === null) return <p className={styles.muted}>Время прогона не записано.</p>
  return (
    <div className={styles.timing}>
      {timing.stages.length > 0 ? (
        <>
          <div className={styles.stageBar}>
            {timing.stages.map((stage, index) => (
              <div
                key={stage.key}
                title={`${stage.label} · ${stage.seconds}`}
                className={styles.stageSegment}
                style={{ width: `${stage.fraction * 100}%`, background: STAGE_COLORS[index % STAGE_COLORS.length] }}
              />
            ))}
          </div>
          <div className={styles.stageLegend}>
            {timing.stages.map((stage, index) => (
              <span key={stage.key} className={styles.legendItem}>
                <span className={styles.legendSwatch} style={{ background: STAGE_COLORS[index % STAGE_COLORS.length] }} aria-hidden />
                {stage.label} <span className={styles.mono}>{stage.seconds}</span>
              </span>
            ))}
          </div>
        </>
      ) : (
        <>
          <Meter value={timing.fraction} height={14} label="Время прогона от бюджета 5 минут" color={timing.withinBudget === false ? 'var(--error)' : 'var(--ink)'} />
          <div className={styles.muted}>
            {timing.withinBudget ? 'В пределах 5 минут на колоду.' : 'Дольше 5 минут.'}
          </div>
        </>
      )}
    </div>
  )
}

function NoteList({ items }: { items: PassportView['fallbacks'] }) {
  return (
    <ul className={styles.fallbacks}>
      {items.map((fallback, index) => (
        <li key={`${fallback.label}-${index}`} className={styles.fallback}>
          <span className={styles.fallbackDot} aria-hidden>
            <Icon as={AlertTriangle} size={13} />
          </span>
          <span>
            <b>{fallback.label}</b>
            {fallback.detail && `: ${fallback.detail}`}
          </span>
        </li>
      ))}
    </ul>
  )
}

function FallbacksBody({ view }: { view: PassportView }) {
  if (view.fallbacks.length === 0) return <p className={styles.muted}>Упрощений не было.</p>
  return <NoteList items={view.fallbacks} />
}

function AutoFixesBody({ view }: { view: PassportView }) {
  return <NoteList items={view.autoFixes} />
}

function ProvenanceBody({ view }: { view: PassportView }) {
  return (
    <dl className={styles.provenance}>
      {view.provenance.map((row) => (
        <div key={row.key} className={styles.provenanceRow}>
          <dt className={styles.provenanceLabel}>{row.label}</dt>
          <dd className={clsx(styles.provenanceValue, row.muted && styles.provenanceMuted)}>
            {row.value}
            {row.note && <span className={styles.provenanceNote}> · {row.note}</span>}
          </dd>
        </div>
      ))}
    </dl>
  )
}

export function QualityPassport({ view, revisionLabel, notice, actions }: QualityPassportProps) {
  const sections: Section[] = [
    ...(view.style.status === 'ready' ? [{ key: 'style', title: 'Соответствие шаблону', body: <StyleBody view={view} /> }] : []),
    ...(view.sources ? [{ key: 'sources', title: 'Опора на контент', body: <SourcesBody view={view} /> }] : []),
    { key: 'readability', title: 'Читаемость', body: <ReadabilityBody view={view} /> },
    { key: 'issues', title: 'Итоги аудита', body: <IssuesBody view={view} /> },
    { key: 'timing', title: <TimingTitle view={view} />, body: <TimingBody view={view} /> },
    ...(view.autoFixes.length > 0 ? [{ key: 'autoFixes', title: 'Исправлено до показа', body: <AutoFixesBody view={view} /> }] : []),
    ...(view.fallbacks.length > 0 ? [{ key: 'fallbacks', title: 'Упрощения', body: <FallbacksBody view={view} /> }] : []),
    { key: 'provenance', title: 'Происхождение', body: <ProvenanceBody view={view} /> },
  ]

  const meta = [revisionLabel && `паспорт ${revisionLabel}`, view.generatedAt && formatDateTime(view.generatedAt)]
    .filter(Boolean)
    .join(' · ')

  return (
    <section className={styles.root} aria-labelledby="quality-passport-title">
      <header className={styles.header}>
        <h2 id="quality-passport-title" className={styles.title}>
          Паспорт качества
        </h2>
        {meta && <div className={styles.meta}>{meta}</div>}
        {actions && <div className={styles.actions}>{actions}</div>}
      </header>
      {view.skillVersion && (
        <div className={styles.versionBanner}>
          <span className={styles.versionLabel}>Результат привязан к версии</span>
          <span className={styles.versionValue}>скилл {view.skillVersion}</span>
          {view.pipelineVersion && <span className={styles.versionValue}>{view.pipelineVersion}</span>}
        </div>
      )}
      {notice}
      {sections.map((section, index) => (
        <div key={section.key} className={styles.section}>
          <div className={styles.number}>{formatIndex(index + 1)}</div>
          <div className={styles.body}>
            <div className={styles.sectionTitle}>{section.title}</div>
            {section.body}
          </div>
        </div>
      ))}
    </section>
  )
}

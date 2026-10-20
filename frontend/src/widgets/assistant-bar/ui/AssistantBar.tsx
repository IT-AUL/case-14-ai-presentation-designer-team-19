import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { Check, Icon, Sparkles } from '@/shared/ui'
import type { AssistantStepView } from '../model/steps'
import styles from './AssistantBar.module.css'

interface AssistantBarProps {
  steps: readonly AssistantStepView[]
  note?: ReactNode
  actions?: ReactNode
}

function Marker({ step }: { step: AssistantStepView }) {
  return (
    <span className={styles.marker} data-state={step.state} aria-hidden="true">
      {step.state === 'done' || step.state === 'skipped' ? <Icon as={Check} size={12} strokeWidth={3} /> : step.number}
    </span>
  )
}

function StepItem({ step }: { step: AssistantStepView }) {
  const content = (
    <>
      <Marker step={step} />
      <span className={styles.label}>{step.label}</span>
    </>
  )
  const title = step.hint ?? step.label
  if (step.href && step.state !== 'current') {
    return (
      <Link to={step.href} state={{ fromStepBar: true }} className={styles.step} data-state={step.state} title={title}>
        {content}
      </Link>
    )
  }
  return (
    <span className={styles.step} data-state={step.state} aria-current={step.state === 'current' ? 'step' : undefined} aria-disabled={step.href ? undefined : true} title={title}>
      {content}
    </span>
  )
}

export function AssistantBar({ steps, note, actions }: AssistantBarProps) {
  return (
    <div className={styles.bar}>
      <div className={styles.row}>
        <nav aria-label="Шаги" className={styles.nav}>
          <ol className={styles.steps}>
            {steps.map((step) => (
              <li key={step.number} className={styles.item} data-state={step.state}>
                <StepItem step={step} />
              </li>
            ))}
          </ol>
        </nav>
        {actions && <div className={styles.actions}>{actions}</div>}
      </div>
      {note && (
        <p className={styles.note} aria-live="polite">
          <Icon as={Sparkles} size={14} />
          <span key={typeof note === 'string' ? note : undefined} className={styles.noteText}>
            {note}
          </span>
        </p>
      )}
    </div>
  )
}

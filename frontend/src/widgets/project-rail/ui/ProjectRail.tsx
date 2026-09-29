import { Link } from 'react-router'
import type { ProjectStep } from '@/shared/config'
import { buildProofChain, buildRailSteps, type ProjectProgress, type RailStep } from '../model/progress'
import styles from './ProjectRail.module.css'
import { Check, Icon } from '@/shared/ui'

interface ProjectRailProps {
  progress: ProjectProgress
  current: ProjectStep | undefined
  compact: boolean
}

function StepContent({ step, index, compact }: { step: RailStep; index: number; compact: boolean }) {
  return (
    <>
      <span className={styles.dot} data-state={step.state}>
        {step.state === 'done' ? <Icon as={Check} size={12} strokeWidth={3} /> : index + 1}
      </span>
      {!compact && (
        <span className={styles.text}>
          <span className={styles.label}>{step.label}</span>
          <span className={styles.meta}>{step.meta}</span>
        </span>
      )}
    </>
  )
}

export function ProjectRail({ progress, current, compact }: ProjectRailProps) {
  const steps = buildRailSteps(progress, current)

  return (
    <nav aria-label="Шаги проекта" className={styles.rail} data-compact={compact}>
      <ol className={styles.steps}>
        {steps.map((step, index) => (
          <li key={step.id}>
            {step.href && step.state !== 'locked' ? (
              <Link
                to={step.href}
                className={styles.step}
                data-state={step.state}
                aria-current={step.state === 'current' ? 'step' : undefined}
                aria-label={compact ? step.label : undefined}
                title={step.label}
              >
                <StepContent step={step} index={index} compact={compact} />
              </Link>
            ) : (
              <span className={styles.step} data-state={step.state} aria-disabled="true" title={step.label}>
                <StepContent step={step} index={index} compact={compact} />
              </span>
            )}
          </li>
        ))}
      </ol>
      {!compact && (
        <section className={styles.proof} aria-label="Цепочка доказательств">
          <h2 className={styles.proofTitle}>Цепочка доказательств</h2>
          <ul className={styles.proofList}>
            {buildProofChain(progress).map((link) => (
              <li key={link.label} className={styles.proofItem} data-done={link.done}>
                <span className={styles.proofDot} />
                {link.label}
              </li>
            ))}
          </ul>
        </section>
      )}
    </nav>
  )
}

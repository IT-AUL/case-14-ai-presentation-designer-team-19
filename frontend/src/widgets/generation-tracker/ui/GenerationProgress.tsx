import { useState } from 'react'
import type { GenerationTracker } from '@/entities/generation'
import { describeFailure } from '../lib/failure'
import { autoSelectedKey } from '../lib/scene'
import { variantCards } from '../lib/variants'
import { DeckChoice, type AuditHref } from './DeckChoice'
import styles from './GenerationProgress.module.css'
import { StageScene } from './StageScene'
import { VariantTabs } from './VariantTabs'

interface GenerationProgressProps {
  tracker: GenerationTracker
  auditHref?: AuditHref
}

export function GenerationProgress({ tracker, auditHref }: GenerationProgressProps) {
  const { phase, generation } = tracker
  const cards = variantCards(phase, generation?.variants)
  const [pickedKey, setPickedKey] = useState<string | null>(null)
  const failure = phase === 'failed' ? describeFailure(tracker) : null

  if (cards.length === 0) return null
  if (phase === 'completed' && auditHref && cards.some((card) => card.state === 'done')) return <DeckChoice cards={cards} auditHref={auditHref} />

  const selectedKey = cards.some((card) => card.key === pickedKey) ? pickedKey : autoSelectedKey(cards)
  const selected = cards.find((card) => card.key === selectedKey) ?? cards[0]

  return (
    <div className={styles.root}>
      {selected && <StageScene card={selected} failureMessage={failure?.message ?? null} auditHref={auditHref ?? null} />}
      <VariantTabs cards={cards} selectedKey={selected?.key ?? null} onSelect={setPickedKey} />
    </div>
  )
}

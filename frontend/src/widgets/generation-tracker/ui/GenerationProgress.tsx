import { useState } from 'react'
import type { JobEvent } from '@/entities/assistant'
import type { DeckPlan, GenerationTracker } from '@/entities/generation'
import { useVariantSlides } from '@/entities/variant'
import { describeFailure } from '../lib/failure'
import { buildFilmstripSlots, latestAssistantNote } from '../lib/filmstrip'
import { autoSelectedKey } from '../lib/scene'
import { variantCards } from '../lib/variants'
import { DeckChoice, type AuditHref } from './DeckChoice'
import styles from './GenerationProgress.module.css'
import { SlideFilmstrip } from './SlideFilmstrip'
import { StageScene } from './StageScene'
import { VariantTabs } from './VariantTabs'

interface GenerationProgressProps {
  tracker: GenerationTracker
  auditHref?: AuditHref
  events?: readonly JobEvent[]
  deckPlan?: DeckPlan | null
  requestedStrategies?: readonly string[]
}

export function GenerationProgress({
  tracker,
  auditHref,
  events = [],
  deckPlan,
  requestedStrategies,
}: GenerationProgressProps) {
  const { phase, generation } = tracker
  const cards = variantCards(phase, generation?.variants, requestedStrategies)
  const [pickedKey, setPickedKey] = useState<string | null>(null)
  const [pickedSlideIndex, setPickedSlideIndex] = useState<number | null>(null)
  const failure = phase === 'failed' ? describeFailure(tracker) : null

  const selectedKey = cards.some((card) => card.key === pickedKey) ? pickedKey : autoSelectedKey(cards)
  const selected = cards.find((card) => card.key === selectedKey) ?? cards[0]
  const selectedVariantId = selected?.variant?.id ?? null
  const selectedVariantState = selected?.state ?? 'running'
  const { data: variantSlides } = useVariantSlides(selectedVariantId)

  const plan = deckPlan ?? generation?.deck_plan ?? null
  const slots = buildFilmstripSlots({
    plan,
    variantSlides,
    events,
    variantId: selectedVariantId,
    variantState: selectedVariantState,
  })

  const readySlots = slots.filter((slot) => slot.state === 'ready')
  const latestReadyIndex = readySlots.at(-1)?.index ?? null
  const activeSlideIndex =
    pickedSlideIndex !== null && slots[pickedSlideIndex]?.state === 'ready' ? pickedSlideIndex : latestReadyIndex
  const activeSlot = activeSlideIndex !== null ? (slots[activeSlideIndex] ?? null) : null

  const assistantNote = latestAssistantNote(events, selectedVariantId, slots.length)

  const hasReadyEvents = events.some((event) => event.type === 'slide.ready')

  if (cards.length === 0) return null
  if (phase === 'completed' && auditHref && cards.some((card) => card.state === 'done')) {
    return (
      <div className={styles.root}>
        <DeckChoice cards={cards} auditHref={auditHref} />
        {hasReadyEvents && slots.length > 0 && (
          <SlideFilmstrip
            slots={slots}
            selectedIndex={activeSlideIndex}
            onSelectSlot={setPickedSlideIndex}
          />
        )}
      </div>
    )
  }

  return (
    <div className={styles.root}>
      {selected && (
        <StageScene
          card={selected}
          failureMessage={failure?.message ?? null}
          auditHref={auditHref ?? null}
          assistantNote={assistantNote}
          activeSlot={hasReadyEvents || pickedSlideIndex !== null ? activeSlot : null}
        />
      )}
      {cards.length > 1 && (
        <VariantTabs
          cards={cards}
          selectedKey={selected?.key ?? null}
          onSelect={(key) => {
            setPickedKey(key)
            setPickedSlideIndex(null)
          }}
        />
      )}
      {slots.length > 0 && (
        <SlideFilmstrip
          slots={slots}
          selectedIndex={activeSlideIndex}
          onSelectSlot={setPickedSlideIndex}
        />
      )}
    </div>
  )
}

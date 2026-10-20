import { useMemo, useState } from 'react'
import type { JobEvent } from '@/entities/assistant'
import { useSlideDrafts, type DeckPlan, type GenerationTracker } from '@/entities/generation'
import { useVariantSlides } from '@/entities/variant'
import { buildFilmstripSlots, buildStageSummary } from '../lib/filmstrip'
import { autoSelectedKey } from '../lib/scene'
import { variantCards, type VariantCard } from '../lib/variants'

export function useBuildCards(tracker: GenerationTracker, requestedStrategies?: readonly string[]): VariantCard[] {
  return variantCards(tracker.phase, tracker.generation?.variants, requestedStrategies)
}

export function useSelectedBuildKey(cards: readonly VariantCard[]): [string | null, (key: string) => void] {
  const [picked, setPicked] = useState<string | null>(null)
  const key = cards.some((card) => card.key === picked) ? picked : autoSelectedKey(cards)
  return [key, setPicked]
}

export function useBuildSlots({
  tracker,
  events,
  deckPlan,
  cards,
  selectedKey,
}: {
  tracker: GenerationTracker
  events: readonly JobEvent[]
  deckPlan: DeckPlan | null
  cards: readonly VariantCard[]
  selectedKey: string | null
}) {
  const selected = cards.find((card) => card.key === selectedKey) ?? cards[0]
  const variantId = selected?.variant?.id ?? null
  const isRunning =
    tracker.phase === 'running' ||
    tracker.phase === 'submitting' ||
    selected?.state === 'running' ||
    selected?.state === 'pending' ||
    selected?.state === 'queued'
  const { data: variantSlides } = useVariantSlides(variantId, isRunning)
  const { data: slideDraftsResponse } = useSlideDrafts(variantId, isRunning)
  const effectivePlan = deckPlan ?? tracker.generation?.deck_plan ?? null

  const slots = useMemo(
    () =>
      buildFilmstripSlots({
        plan: effectivePlan,
        variantSlides,
        slideDrafts: slideDraftsResponse?.slides ?? null,
        events,
        variantId,
        variantState: selected?.state ?? (tracker.phase === 'completed' ? 'done' : 'running'),
      }),
    [effectivePlan, variantSlides, slideDraftsResponse?.slides, events, variantId, selected?.state, tracker.phase],
  )

  const stageSummary = useMemo(
    () =>
      buildStageSummary({
        phase: tracker.phase,
        stage: selected?.variant?.stage ?? tracker.job?.stage ?? null,
        cardState: selected?.state,
        slots,
        hasPlan: Boolean(effectivePlan?.slides.length),
      }),
    [tracker.phase, selected?.variant?.stage, tracker.job?.stage, selected?.state, slots, effectivePlan?.slides.length],
  )

  return {
    selected,
    variantId,
    slots,
    stageSummary,
  }
}

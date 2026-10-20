import { useCallback, useState } from 'react'
import type { DeckPlan } from '@/entities/generation'
import { readJson, removeKey, writeJson } from '@/shared/lib/storage'
import {
  addSlide,
  addUnit,
  applyQuickAction,
  changeSlideChart,
  isPlanEdited,
  keepSlideOnTopic,
  moveSlide,
  moveUnit,
  removeSlide,
  removeUnit,
  updateKeyMessage,
  updateSlide,
  updateUnit,
  type QuickSlideAction,
  type SlidePatch,
} from '../lib/editPlan'

const STORAGE_PREFIX = 'deckdna.plan-draft.v1:'
const MAX_HISTORY = 20

interface StoredDraft {
  baseId: string
  plan: DeckPlan
}

interface DraftState {
  key: string
  baseId: string
  plan: DeckPlan | null
  history: DeckPlan[]
}

export interface PlanDraft {
  plan: DeckPlan
  edited: boolean
  canUndo: boolean
  move: (slideId: string, delta: -1 | 1) => void
  remove: (slideId: string) => void
  update: (slideId: string, patch: SlidePatch) => void
  add: () => void
  updateUnit: (slideId: string, position: number, text: string) => void
  addUnit: (slideId: string) => void
  removeUnit: (slideId: string, position: number) => void
  moveUnit: (slideId: string, position: number, delta: -1 | 1) => void
  changeChart: (slideId: string, chartType: string) => void
  quickAction: (slideId: string, action: QuickSlideAction) => void
  updateKeyMessage: (keyMessage: string) => void
  keepSlideOnTopic: (slideId: string) => void
  replace: (next: DeckPlan, recordHistory?: boolean) => void
  undo: () => void
  reset: () => void
}

function storageKey(key: string): string {
  return `${STORAGE_PREFIX}${key}`
}

function readDraft(key: string, base: DeckPlan): DeckPlan | null {
  const stored = readJson<StoredDraft | null>(storageKey(key), null)
  return stored && stored.baseId === base.id && Array.isArray(stored.plan?.slides) ? stored.plan : null
}

export function clearPlanDraft(key: string): void {
  removeKey(storageKey(key))
}

export function usePlanDraft(key: string, base: DeckPlan): PlanDraft {
  const [state, setState] = useState<DraftState>(() => ({ key, baseId: base.id, plan: readDraft(key, base), history: [] }))
  const current: DraftState =
    state.key === key && state.baseId === base.id ? state : { key, baseId: base.id, plan: readDraft(key, base), history: [] }
  if (current !== state) setState(current)
  const plan = current.plan ?? base

  const commit = useCallback(
    (edit: (plan: DeckPlan) => DeckPlan, recordHistory = true) => {
      setState((previous) => {
        const active = previous.key === key && previous.baseId === base.id ? previous : { key, baseId: base.id, plan: readDraft(key, base), history: [] }
        const source = active.plan ?? base
        const next = edit(source)
        if (next === source) return previous
        if (isPlanEdited(base, next)) writeJson(storageKey(key), { baseId: base.id, plan: next } satisfies StoredDraft)
        else clearPlanDraft(key)
        const history = recordHistory ? [...active.history.slice(-(MAX_HISTORY - 1)), source] : active.history
        return { key, baseId: base.id, plan: next, history }
      })
    },
    [key, base],
  )

  const undo = useCallback(() => {
    setState((previous) => {
      if (previous.key !== key || previous.baseId !== base.id || previous.history.length === 0) return previous
      const nextHistory = previous.history.slice(0, -1)
      const restored = previous.history[previous.history.length - 1] ?? base
      if (isPlanEdited(base, restored)) writeJson(storageKey(key), { baseId: base.id, plan: restored } satisfies StoredDraft)
      else clearPlanDraft(key)
      return { key, baseId: base.id, plan: restored, history: nextHistory }
    })
  }, [key, base])

  return {
    plan,
    edited: isPlanEdited(base, plan),
    canUndo: current.history.length > 0,
    move: (slideId, delta) => commit((draft) => moveSlide(draft, slideId, delta)),
    remove: (slideId) => commit((draft) => removeSlide(draft, slideId)),
    update: (slideId, patch) => commit((draft) => updateSlide(draft, slideId, patch)),
    add: () => commit((draft) => addSlide(draft)),
    updateUnit: (slideId, position, text) => commit((draft) => updateUnit(draft, slideId, position, text)),
    addUnit: (slideId) => commit((draft) => addUnit(draft, slideId)),
    removeUnit: (slideId, position) => commit((draft) => removeUnit(draft, slideId, position)),
    moveUnit: (slideId, position, delta) => commit((draft) => moveUnit(draft, slideId, position, delta)),
    changeChart: (slideId, chartType) => commit((draft) => changeSlideChart(draft, slideId, chartType)),
    quickAction: (slideId, action) => commit((draft) => applyQuickAction(draft, slideId, action)),
    updateKeyMessage: (keyMessage) => commit((draft) => updateKeyMessage(draft, keyMessage)),
    keepSlideOnTopic: (slideId) => commit((draft) => keepSlideOnTopic(draft, slideId)),
    replace: (next, recordHistory = false) => commit(() => next, recordHistory),
    undo,
    reset: () => {
      clearPlanDraft(key)
      setState({ key, baseId: base.id, plan: null, history: [] })
    },
  }
}

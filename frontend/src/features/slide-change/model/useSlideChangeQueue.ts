import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  cancelVariantChange,
  fetchVariantChanges,
  hasPendingVariantChanges,
  isSlideChangeActive,
  isSlideChangeCanceled,
  latestSlideChange,
  regenerateVariantSlide,
  slideChangeErrorMessage,
  slideChangeNote,
  submitVariantChange,
  useVariantChanges,
  variantKeys,
  type SlideChangeAction,
  type SlideChangeChartType,
  type SlideChangeItem,
  type VariantChangesSnapshot,
} from '@/entities/variant'
import { humanError, isApiError } from '@/shared/api'

export interface SubmitSlideChangeInput {
  slideId: string
  fallbackSlideId?: string | null
  action: SlideChangeAction | string
  chartType?: SlideChangeChartType | string | null
  text?: string | null
  fallbackRevision?: number | null
}

export interface FallbackSlideResult {
  slideId: string
  note: string
}

const CONFLICT_MESSAGE = 'Состояние слайда обновилось — перечитал очередь, попробуйте ещё раз.'

function fallbackNoteForAction(action: string, response: Record<string, unknown> | null | undefined): string {
  const fromResponse = typeof response?.assistant_note === 'string' ? response.assistant_note.trim() : ''
  if (fromResponse) return fromResponse
  return (
    slideChangeNote({
      change_id: 'fallback',
      slide_id: 'fallback',
      action,
      state: 'completed',
      base_revision: 1,
      target_revision: 2,
    }) ?? 'Пересобрал слайд в выбранном стиле.'
  )
}

function mergeChangeIntoSnapshot(
  current: VariantChangesSnapshot | null | undefined,
  variantId: string,
  item: SlideChangeItem,
): VariantChangesSnapshot {
  const base: VariantChangesSnapshot = current ?? {
    variant_id: variantId,
    revision: item.state === 'completed' ? item.target_revision : item.base_revision,
    applied_revision: item.state === 'completed' ? item.target_revision : item.base_revision,
    accepted_revision: item.target_revision,
    items: [],
  }
  const exists = base.items.some((entry) => entry.change_id === item.change_id)
  const items = exists
    ? base.items.map((entry) => (entry.change_id === item.change_id ? item : entry))
    : [...base.items, item]
  const activeOrDone = items.filter((entry) => !isSlideChangeCanceled(entry.state) && entry.state !== 'failed')
  const maxAccepted = activeOrDone.reduce((acc, entry) => Math.max(acc, entry.target_revision), base.applied_revision)
  const maxApplied = items
    .filter((entry) => entry.state === 'completed')
    .reduce((acc, entry) => Math.max(acc, entry.target_revision), base.applied_revision)

  return {
    variant_id: base.variant_id || variantId,
    revision: maxApplied,
    applied_revision: maxApplied,
    accepted_revision: isSlideChangeCanceled(item.state)
      ? maxAccepted
      : Math.max(base.accepted_revision, item.target_revision),
    items,
  }
}

export function useSlideChangeQueue(variantId: string | null | undefined, options?: { pollAlways?: boolean }) {
  const queryClient = useQueryClient()
  const changesQuery = useVariantChanges(variantId, options)
  const snapshot = changesQuery.data

  const [submittingSlideId, setSubmittingSlideId] = useState<string | null>(null)
  const [cancellingChangeId, setCancellingChangeId] = useState<string | null>(null)
  const [errorBySlide, setErrorBySlide] = useState<Record<string, string>>({})
  const [fallbackBySlide, setFallbackBySlide] = useState<Record<string, FallbackSlideResult>>({})
  const [unsupported, setUnsupported] = useState(false)

  const prevAppliedRevisionRef = useRef<number | null>(null)
  const watchedChangeIdsRef = useRef<Set<string>>(new Set())

  const invalidateVariantState = useCallback(
    (targetVariantId: string) => {
      void queryClient.invalidateQueries({ queryKey: variantKeys.detail(targetVariantId) })
      void queryClient.invalidateQueries({ queryKey: variantKeys.slides(targetVariantId) })
      void queryClient.invalidateQueries({ queryKey: ['audit', targetVariantId] })
      void queryClient.invalidateQueries({ queryKey: ['export'] })
      void queryClient.invalidateQueries({ queryKey: ['passport'] })
      void queryClient.invalidateQueries({ queryKey: ['assistant', 'generation'] })
      void queryClient.invalidateQueries({ queryKey: ['assistant', 'slide'] })
    },
    [queryClient],
  )

  useEffect(() => {
    prevAppliedRevisionRef.current = null
    watchedChangeIdsRef.current.clear()
  }, [variantId])

  useEffect(() => {
    if (!variantId || !snapshot) return
    let shouldInvalidate = false

    if (prevAppliedRevisionRef.current !== null && snapshot.applied_revision > prevAppliedRevisionRef.current) {
      shouldInvalidate = true
    }
    prevAppliedRevisionRef.current = snapshot.applied_revision

    for (const item of snapshot.items) {
      if (!watchedChangeIdsRef.current.has(item.change_id)) continue
      if (item.state === 'completed') {
        watchedChangeIdsRef.current.delete(item.change_id)
        shouldInvalidate = true
      } else if (isSlideChangeCanceled(item.state) || item.state === 'failed') {
        watchedChangeIdsRef.current.delete(item.change_id)
      }
    }

    if (shouldInvalidate) {
      invalidateVariantState(variantId)
    }
  }, [variantId, snapshot, invalidateVariantState])

  const submitChange = useCallback(
    async (input: SubmitSlideChangeInput): Promise<SlideChangeItem | FallbackSlideResult | null> => {
      if (!variantId) return null
      const slideKey = input.slideId
      setSubmittingSlideId(slideKey)
      setErrorBySlide((prev) => {
        if (!(slideKey in prev)) return prev
        return Object.fromEntries(Object.entries(prev).filter(([key]) => key !== slideKey))
      })

      try {
        let currentSnapshot = queryClient.getQueryData<VariantChangesSnapshot | null>(variantKeys.changes(variantId))
        if (currentSnapshot === undefined) {
          currentSnapshot = await queryClient.fetchQuery({
            queryKey: variantKeys.changes(variantId),
            queryFn: () => fetchVariantChanges(variantId),
          })
        }

        const expectedRevision = Math.max(1, currentSnapshot?.accepted_revision ?? input.fallbackRevision ?? 1)
        const created = await submitVariantChange(variantId, {
          slide_id: input.slideId,
          expected_revision: expectedRevision,
          action: input.action,
          chart_type: input.chartType ?? undefined,
          text: input.text ?? undefined,
        })

        if (created) {
          setFallbackBySlide((prev) => {
            if (!(slideKey in prev)) return prev
            return Object.fromEntries(Object.entries(prev).filter(([key]) => key !== slideKey))
          })
          if (isSlideChangeActive(created)) {
            watchedChangeIdsRef.current.add(created.change_id)
          }
          queryClient.setQueryData<VariantChangesSnapshot | null>(
            variantKeys.changes(variantId),
            (prev) => mergeChangeIntoSnapshot(prev ?? currentSnapshot, variantId, created),
          )
          if (created.state === 'completed') {
            invalidateVariantState(variantId)
          }
          return created
        }

        const legacySlideId = input.fallbackSlideId ?? input.slideId
        try {
          const response = await regenerateVariantSlide(variantId, legacySlideId, {
            action: input.action,
            chart_type: input.chartType ?? undefined,
            text: input.text ?? undefined,
          })
          const note = fallbackNoteForAction(input.action, response)
          const fallbackResult: FallbackSlideResult = { slideId: slideKey, note }
          setFallbackBySlide((prev) => ({
            ...prev,
            [slideKey]: fallbackResult,
            ...(input.fallbackSlideId ? { [input.fallbackSlideId]: fallbackResult } : {}),
          }))
          invalidateVariantState(variantId)
          return fallbackResult
        } catch (fallbackErr) {
          if (isApiError(fallbackErr) && (fallbackErr.status === 404 || fallbackErr.status === 405 || fallbackErr.status === 501)) {
            setUnsupported(true)
            return null
          }
          throw fallbackErr
        }
      } catch (err) {
        if (isApiError(err) && err.status === 409) {
          await queryClient.invalidateQueries({ queryKey: variantKeys.changes(variantId) })
          invalidateVariantState(variantId)
          setErrorBySlide((prev) => ({ ...prev, [slideKey]: CONFLICT_MESSAGE }))
          return null
        }
        setErrorBySlide((prev) => ({
          ...prev,
          [slideKey]: humanError(err, 'Не удалось поставить правку в очередь.'),
        }))
        return null
      } finally {
        setSubmittingSlideId((current) => (current === slideKey ? null : current))
      }
    },
    [variantId, queryClient, invalidateVariantState],
  )

  const cancelChange = useCallback(
    async (changeId: string): Promise<SlideChangeItem | null> => {
      if (!variantId) return null
      setCancellingChangeId(changeId)
      try {
        const cancelled = await cancelVariantChange(variantId, changeId)
        if (cancelled) {
          watchedChangeIdsRef.current.delete(changeId)
          queryClient.setQueryData<VariantChangesSnapshot | null>(
            variantKeys.changes(variantId),
            (prev) => mergeChangeIntoSnapshot(prev, variantId, cancelled),
          )
          void queryClient.invalidateQueries({ queryKey: variantKeys.changes(variantId) })
        }
        return cancelled
      } catch (err) {
        if (isApiError(err) && err.status === 409) {
          await queryClient.invalidateQueries({ queryKey: variantKeys.changes(variantId) })
          invalidateVariantState(variantId)
        }
        return null
      } finally {
        setCancellingChangeId((current) => (current === changeId ? null : current))
      }
    },
    [variantId, queryClient, invalidateVariantState],
  )

  const getSlideChange = useCallback(
    (slideIds: readonly (string | null | undefined)[]): SlideChangeItem | null => latestSlideChange(snapshot, slideIds),
    [snapshot],
  )

  const getSlideNote = useCallback(
    (slideIds: readonly (string | null | undefined)[]): string | null => {
      const change = latestSlideChange(snapshot, slideIds)
      const fromChange = slideChangeNote(change)
      if (fromChange) return fromChange
      for (const id of slideIds) {
        if (id && fallbackBySlide[id]) return fallbackBySlide[id].note
      }
      return null
    },
    [snapshot, fallbackBySlide],
  )

  const getSlideError = useCallback(
    (slideIds: readonly (string | null | undefined)[]): string | null => {
      for (const id of slideIds) {
        if (id && errorBySlide[id]) return errorBySlide[id]
      }
      const change = latestSlideChange(snapshot, slideIds)
      return slideChangeErrorMessage(change)
    },
    [snapshot, errorBySlide],
  )

  return {
    snapshot,
    hasPending: hasPendingVariantChanges(snapshot),
    submittingSlideId,
    cancellingChangeId,
    unsupported,
    submitChange,
    cancelChange,
    getSlideChange,
    getSlideNote,
    getSlideError,
  }
}

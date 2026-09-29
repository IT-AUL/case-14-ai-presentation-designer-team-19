import { useCallback, useMemo, useState } from 'react'
import { pruneSelection, toggleSelection } from '@/entities/audit'

export interface IssueSelection {
  selectedIds: ReadonlySet<string>
  count: number
  isSelected: (issueId: string) => boolean
  toggle: (issueId: string) => void
  setMany: (issueIds: readonly string[], selected: boolean) => void
  selectAll: () => void
  clear: () => void
}

const EMPTY: ReadonlySet<string> = new Set()

export function useIssueSelection(selectable: readonly string[]): IssueSelection {
  const [raw, setRaw] = useState<ReadonlySet<string>>(EMPTY)
  const selectableKey = selectable.join('\n')
  const allowed = useMemo(() => (selectableKey ? selectableKey.split('\n') : []), [selectableKey])
  const selectedIds = useMemo(() => pruneSelection(raw, allowed), [raw, allowed])

  const toggle = useCallback(
    (issueId: string) => {
      if (allowed.includes(issueId)) setRaw((current) => toggleSelection(pruneSelection(current, allowed), issueId))
    },
    [allowed],
  )

  const setMany = useCallback(
    (issueIds: readonly string[], selected: boolean) => {
      setRaw((current) => {
        const next = new Set(pruneSelection(current, allowed))
        for (const id of issueIds) {
          if (!allowed.includes(id)) continue
          if (selected) next.add(id)
          else next.delete(id)
        }
        return next
      })
    },
    [allowed],
  )

  const selectAll = useCallback(() => setRaw(new Set(allowed)), [allowed])
  const clear = useCallback(() => setRaw(EMPTY), [])

  return {
    selectedIds,
    count: selectedIds.size,
    isSelected: (issueId) => selectedIds.has(issueId),
    toggle,
    setMany,
    selectAll,
    clear,
  }
}

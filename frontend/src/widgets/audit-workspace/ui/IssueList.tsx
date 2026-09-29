import { useEffect, useRef } from 'react'
import { isPendingStatus, SEVERITY, selectionState, type IssueCluster, type IssueGrouping, type IssueViewGroup } from '@/entities/audit'
import { ChevronRight, Icon, Mono } from '@/shared/ui'
import { IssueCard, type IssueCardHandlers } from './IssueCard'
import { IssueCheck } from './IssueCheck'
import styles from './IssuePanel.module.css'

export interface ClusteredGroup extends IssueViewGroup {
  clusters: IssueCluster[]
}

export interface IssueListProps extends IssueCardHandlers {
  groups: readonly ClusteredGroup[]
  grouping: IssueGrouping
  expanded: ReadonlySet<string>
  onToggleGroup: (groupKey: string) => void
  selectedIds: ReadonlySet<string>
  activeKey: string | null
  hoverKey: string | null
}

const holds = (cluster: IssueCluster, key: string | null) => key !== null && cluster.views.some((view) => view.key === key)

export function IssueList({ groups, grouping, expanded, onToggleGroup, selectedIds, activeKey, hoverKey, ...handlers }: IssueListProps) {
  const listRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!activeKey) return
    const card = [...(listRef.current?.querySelectorAll<HTMLElement>('[data-cluster-keys]') ?? [])].find((element) =>
      element.dataset.clusterKeys?.split(' ').includes(activeKey),
    )
    card?.scrollIntoView?.({ block: 'nearest' })
  }, [activeKey])

  return (
    <div ref={listRef} className={styles.list} data-issue-list>
      {groups.length === 0 && <div className={styles.empty}>По этим фильтрам проблем нет.</div>}
      {groups.map((group) => {
        const open = expanded.has(group.key)
        const fixable = group.views.filter((view) => view.selectable)
        const manualOnly = fixable.length === 0 && group.views.some((view) => isPendingStatus(view.status))
        const state = selectionState(group.views, selectedIds)
        return (
          <section key={group.key} className={styles.group} aria-label={group.label}>
            <div className={styles.groupHead} data-severity={group.severity}>
              <span className={styles.groupCheck}>
                {fixable.length > 0 && (
                  <IssueCheck
                    state={state}
                    label={`Выбрать все исправимые в группе «${group.label}» (${fixable.length})`}
                    onChange={(selected) => handlers.onSelect(fixable.map((view) => view.issue.id), selected)}
                  />
                )}
              </span>
              <button type="button" className={styles.groupToggle} aria-expanded={open} onClick={() => onToggleGroup(group.key)}>
                <Icon as={ChevronRight} size={14} className={styles.chevron} />
                {group.severity && grouping === 'rule' && (
                  <span className={styles.groupDot} data-severity={group.severity} title={SEVERITY[group.severity].label} aria-hidden />
                )}
                <span className={styles.groupLabel}>{group.label}</span>
                <Mono className={styles.groupCount}>×{group.views.length}</Mono>
                {fixable.length > 0 && fixable.length < group.views.length && <span className={styles.groupFixable}>{fixable.length} испр.</span>}
                {manualOnly && <span className={styles.groupFixable}>вручную</span>}
              </button>
            </div>
            {open && (
              <div className={styles.groupBody}>
                {group.clusters.map((cluster) => (
                  <div key={cluster.key} data-cluster-keys={cluster.views.map((view) => view.key).join(' ')}>
                    <IssueCard
                      cluster={cluster}
                      context={grouping === 'rule' ? 'rule' : 'other'}
                      active={holds(cluster, activeKey)}
                      hovered={holds(cluster, hoverKey)}
                      selection={selectionState(cluster.views, selectedIds)}
                      {...handlers}
                    />
                  </div>
                ))}
              </div>
            )}
          </section>
        )
      })}
    </div>
  )
}

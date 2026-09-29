import { clsx } from 'clsx'
import { CATEGORY_LABEL, SEVERITY, SEVERITY_ORDER, type IssueGrouping, type IssueViewFilter } from '@/entities/audit'
import { Check, Icon, X } from '@/shared/ui'
import styles from './IssuePanel.module.css'

interface Option<T extends string> {
  value: T
  label: string
}

interface ChipGroupProps<T extends string> {
  label: string
  options: readonly Option<T>[]
  value: T
  onChange: (value: T) => void
  variant: 'segment' | 'chip'
}

function ChipGroup<T extends string>({ label, options, value, onChange, variant }: ChipGroupProps<T>) {
  return (
    <div role="radiogroup" aria-label={label} className={variant === 'segment' ? styles.segments : styles.chips}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          role="radio"
          aria-checked={option.value === value}
          className={clsx(variant === 'segment' ? styles.segment : styles.chip)}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

const SEVERITY_OPTIONS: readonly Option<IssueViewFilter['severity']>[] = [
  { value: 'all', label: 'Любая' },
  ...SEVERITY_ORDER.map((severity) => ({ value: severity, label: SEVERITY[severity].shortLabel })),
]

const GROUPING_OPTIONS: readonly Option<IssueGrouping>[] = [
  { value: 'rule', label: 'По правилам' },
  { value: 'slide', label: 'По слайдам' },
  { value: 'category', label: 'По категориям' },
]

const STATUS_OPTIONS: readonly Option<IssueViewFilter['status']>[] = [
  { value: 'all', label: 'Все статусы' },
  { value: 'open', label: 'Открытые' },
  { value: 'resolved', label: 'Решённые' },
]

export interface KindCounts {
  all: number
  D: number
  N: number
}

interface IssueFiltersProps {
  filter: IssueViewFilter
  onFilterChange: (filter: IssueViewFilter) => void
  grouping: IssueGrouping
  onGroupingChange: (grouping: IssueGrouping) => void
  counts: KindCounts
}

export function IssueFilters({ filter, onFilterChange, grouping, onGroupingChange, counts }: IssueFiltersProps) {
  const update = (patch: Partial<IssueViewFilter>) => onFilterChange({ ...filter, ...patch })
  const kindOptions: readonly Option<IssueViewFilter['kind']>[] = [
    { value: 'all', label: `Все ${counts.all}` },
    { value: 'D', label: `D · правило ${counts.D}` },
    { value: 'N', label: `N · модель ${counts.N}` },
  ]

  return (
    <div className={styles.filters}>
      <ChipGroup label="Тип проверки" variant="segment" options={kindOptions} value={filter.kind} onChange={(kind) => update({ kind })} />
      <ChipGroup label="Серьёзность" variant="chip" options={SEVERITY_OPTIONS} value={filter.severity} onChange={(severity) => update({ severity })} />
      <div className={styles.filterRow}>
        <ChipGroup label="Группировка" variant="chip" options={GROUPING_OPTIONS} value={grouping} onChange={onGroupingChange} />
        <span className={styles.divider} aria-hidden />
        <ChipGroup label="Статус" variant="chip" options={STATUS_OPTIONS} value={filter.status} onChange={(status) => update({ status })} />
        <button
          type="button"
          role="checkbox"
          aria-checked={filter.fixableOnly}
          className={styles.toggleChip}
          onClick={() => update({ fixableOnly: !filter.fixableOnly })}
        >
          <span className={styles.toggleBox} aria-hidden>
            {filter.fixableOnly && <Icon as={Check} size={10} strokeWidth={3} />}
          </span>
          только исправимые
        </button>
      </div>
      {filter.category !== 'all' && (
        <div className={styles.filterRow}>
          <button type="button" className={styles.categoryChip} onClick={() => update({ category: 'all' })} aria-label={`Снять фильтр категории ${CATEGORY_LABEL[filter.category]}`}>
            Категория: {CATEGORY_LABEL[filter.category]} <Icon as={X} size={12} />
          </button>
        </div>
      )}
    </div>
  )
}

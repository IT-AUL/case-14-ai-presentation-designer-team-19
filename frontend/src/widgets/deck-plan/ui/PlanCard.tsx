import { useLayoutEffect, useRef } from 'react'
import { hasChartData, isEditableUnit, slidePurposeLabel, type SlidePlan } from '@/entities/generation'
import { formatIndex, pluralize } from '@/shared/lib/format'
import { Icon, Plus, X } from '@/shared/ui'
import { parseSlideChartView, plannedVisual, SOURCE_KIND_LABEL, slideSourceKinds, VISUAL_LABEL } from '../lib/plan'
import { ChartMiniSvg } from './ChartReasoningBox'
import styles from './PlanCard.module.css'

const VISIBLE_POINTS = 3

interface PlanCardProps {
  slide: SlidePlan
  number: number
  selected?: boolean
  researching?: boolean
  offTopic?: boolean
  onOpen: () => void
  onTitleChange: (text: string) => void
  onUnitChange: (position: number, text: string) => void
  onUnitAdd: () => void
  onUnitRemove: (position: number) => void
  onChartPick?: (type: string) => void
}

interface AutoTextProps {
  value: string
  label: string
  placeholder: string
  className?: string
  onChange: (text: string) => void
}

function AutoText({ value, label, placeholder, className, onChange }: AutoTextProps) {
  const field = useRef<HTMLTextAreaElement>(null)

  useLayoutEffect(() => {
    const element = field.current
    if (!element) return
    const fit = () => {
      element.style.height = 'auto'
      element.style.height = `${element.scrollHeight}px`
    }
    fit()
    if (typeof ResizeObserver === 'undefined') return
    let width = element.clientWidth
    const observer = new ResizeObserver(() => {
      if (element.clientWidth === width) return
      width = element.clientWidth
      fit()
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [value])

  return <textarea ref={field} rows={1} className={className} value={value} placeholder={placeholder} aria-label={label} onChange={(event) => onChange(event.target.value)} />
}

function visualLine(slide: SlidePlan): string | null {
  const visual = plannedVisual(slide)
  if (!visual || visual === VISUAL_LABEL.none || visual === VISUAL_LABEL.chart) return null
  return visual
}

export function PlanCard({ slide, number, selected, researching, offTopic, onOpen, onTitleChange, onUnitChange, onUnitAdd, onUnitRemove, onChartPick }: PlanCardProps) {
  const points = slide.content_units.map((unit, position) => ({ unit, position })).filter(({ unit }) => isEditableUnit(unit))
  const shown = points.slice(0, VISIBLE_POINTS)
  const hidden = points.length - shown.length
  const chart = parseSlideChartView(slide)
  const sources = slideSourceKinds(slide)
  const other = chart ? null : visualLine(slide)
  const needsNumbers = !chart && slide.desired_visual === 'chart' && !hasChartData(slide)
  const role = slidePurposeLabel(slide.purpose) ?? 'Контент'
  const canAdd = points.length === 0 && slide.purpose !== 'title' && slide.purpose !== 'thank_you'

  return (
    <article className={styles.card} aria-label={`Слайд ${number}`} aria-busy={researching || undefined} data-selected={selected || undefined} data-off-topic={offTopic || undefined}>
      <header className={styles.head}>
        <span className={styles.number}>{formatIndex(number)}</span>
        <span className={styles.role}>{role}</span>
        {sources.length > 0 && (
          <span className={styles.sources} aria-label="Источник">
            {sources.map((kind) => (
              <span key={kind} className={styles.source} data-kind={kind}>
                {SOURCE_KIND_LABEL[kind]}
              </span>
            ))}
          </span>
        )}
        <button type="button" className={styles.open} aria-label={`Подробнее о слайде ${number}`} onClick={onOpen}>
          Подробнее
        </button>
      </header>

      <AutoText className={styles.title} value={slide.title_intent} label={`Заголовок слайда ${number}`} placeholder="Заголовок-вывод" onChange={onTitleChange} />

      {offTopic && <p className={styles.offTopic}>Уводит от главной мысли</p>}

      {shown.length > 0 && (
        <ul className={styles.points} aria-label={`Пункты слайда ${number}`}>
          {shown.map(({ unit, position }, order) => (
            <li key={position} className={styles.point}>
              <AutoText
                className={styles.pointInput}
                value={unit.text ?? ''}
                label={`Пункт ${order + 1} слайда ${number}`}
                placeholder="Пункт"
                onChange={(text) => onUnitChange(position, text)}
              />
              <button type="button" className={styles.remove} aria-label={`Убрать пункт ${order + 1} слайда ${number}`} onClick={() => onUnitRemove(position)}>
                <Icon as={X} size={12} />
              </button>
            </li>
          ))}
        </ul>
      )}

      {hidden > 0 && (
        <button type="button" className={styles.more} onClick={onOpen}>
          Ещё {hidden} {pluralize(hidden, ['пункт', 'пункта', 'пунктов'])}
        </button>
      )}

      {canAdd && (
        <button type="button" className={styles.add} onClick={onUnitAdd}>
          <Icon as={Plus} size={12} /> Пункт
        </button>
      )}

      {chart && (
        <button
          type="button"
          className={styles.chart}
          data-chart-type={chart.type}
          aria-label={`Подробнее о слайде ${number}: ${chart.label}`}
          onClick={onOpen}
        >
          <span className={styles.spark} aria-hidden="true">
            <ChartMiniSvg type={chart.type} />
          </span>
          <span className={styles.chartText}>
            <span className={styles.chartLabel}>
              {chart.type === 'table' || chart.type === 'kpi' ? chart.label : `График · ${chart.label}`}
            </span>
            <span className={styles.chartWhy}>{chart.why}</span>
          </span>
        </button>
      )}

      {chart && onChartPick && chart.options.length > 1 && (
        <div className={styles.chartTypes} role="group" aria-label={`Тип визуала слайда ${number}`}>
          {chart.options.map((option) => (
            <button
              key={option.type}
              type="button"
              className={styles.chartType}
              data-active={option.active || undefined}
              aria-pressed={option.active}
              title={option.reasons.length > 0 ? option.reasons.join(' ') : undefined}
              onClick={() => onChartPick(option.type)}
            >
              {option.label}
            </button>
          ))}
        </div>
      )}

      {other && <p className={styles.visual}>{other}</p>}
      {needsNumbers && <p className={styles.visual}>График · нужны числа</p>}
    </article>
  )
}

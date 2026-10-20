import { artifactUrl } from '@/shared/api'
import type { SlideChartView } from '../lib/plan'
import styles from './DeckPlanView.module.css'

interface ChartReasoningBoxProps {
  slideId: string
  number: string
  chart: SlideChartView
  onSelectChart?: (slideId: string, chartType: string) => void
  disabled?: boolean
  hideOptions?: boolean
}

export function ChartMiniSvg({ type }: { type: string }) {
  switch (type) {
    case 'line':
      return (
        <svg className={styles.sparkSvg} viewBox="0 0 110 40" aria-hidden="true">
          <polygon fill="var(--ok-bg)" points="6,34 22,30 38,26 54,23 70,17 86,11 102,6 102,36 6,36" />
          <polyline fill="none" stroke="var(--ink)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" points="6,34 22,30 38,26 54,23 70,17 86,11 102,6" />
          <circle cx="102" cy="6" r="3" fill="var(--ok)" />
        </svg>
      )
    case 'bar_horizontal':
      return (
        <svg className={styles.sparkSvg} viewBox="0 0 110 40" aria-hidden="true">
          <rect x="6" y="6" width="88" height="6" rx="2" fill="var(--ink)" />
          <rect x="6" y="16" width="54" height="6" rx="2" fill="var(--ink-2)" />
          <rect x="6" y="26" width="32" height="6" rx="2" fill="var(--line-2)" />
        </svg>
      )
    case 'pie':
    case 'doughnut':
    case 'donut':
      return (
        <svg className={styles.sparkSvg} viewBox="0 0 110 40" aria-hidden="true">
          <circle cx="34" cy="20" r="13" fill="none" stroke="var(--line-2)" strokeWidth="6" />
          <circle cx="34" cy="20" r="13" fill="none" stroke="var(--ink)" strokeWidth="6" strokeDasharray="48 40" strokeDashoffset="12" />
          <rect x="60" y="10" width="38" height="4" rx="2" fill="var(--ink)" />
          <rect x="60" y="18" width="28" height="4" rx="2" fill="var(--ink-3)" />
          <rect x="60" y="26" width="20" height="4" rx="2" fill="var(--line-2)" />
        </svg>
      )
    case 'table':
      return (
        <svg className={styles.sparkSvg} viewBox="0 0 110 40" aria-hidden="true">
          <rect x="6" y="6" width="98" height="7" rx="1.5" fill="var(--ink)" />
          <rect x="6" y="16" width="30" height="6" rx="1" fill="var(--line-2)" />
          <rect x="40" y="16" width="30" height="6" rx="1" fill="var(--sunken)" />
          <rect x="74" y="16" width="30" height="6" rx="1" fill="var(--sunken)" />
          <rect x="6" y="25" width="30" height="6" rx="1" fill="var(--line-2)" />
          <rect x="40" y="25" width="30" height="6" rx="1" fill="var(--sunken)" />
          <rect x="74" y="25" width="30" height="6" rx="1" fill="var(--sunken)" />
        </svg>
      )
    case 'scatter':
      return (
        <svg className={styles.sparkSvg} viewBox="0 0 110 40" aria-hidden="true">
          <circle cx="16" cy="30" r="3" fill="var(--ink-3)" />
          <circle cx="34" cy="24" r="3" fill="var(--ink-2)" />
          <circle cx="52" cy="22" r="3" fill="var(--ink)" />
          <circle cx="72" cy="14" r="3" fill="var(--ink)" />
          <circle cx="92" cy="9" r="3.5" fill="var(--ok)" />
        </svg>
      )
    case 'kpi':
      return (
        <svg className={styles.sparkSvg} viewBox="0 0 110 40" aria-hidden="true">
          <rect x="8" y="8" width="44" height="24" rx="4" fill="var(--sunken)" />
          <rect x="58" y="8" width="44" height="24" rx="4" fill="var(--ok-bg)" />
          <rect x="14" y="16" width="28" height="8" rx="2" fill="var(--ink)" />
          <rect x="64" y="16" width="28" height="8" rx="2" fill="var(--ok)" />
        </svg>
      )
    default:
      return (
        <svg className={styles.sparkSvg} viewBox="0 0 110 40" aria-hidden="true">
          <rect x="10" y="24" width="14" height="12" rx="2" fill="var(--line-2)" />
          <rect x="30" y="18" width="14" height="18" rx="2" fill="var(--ink-3)" />
          <rect x="50" y="13" width="14" height="23" rx="2" fill="var(--ink-2)" />
          <rect x="70" y="7" width="14" height="29" rx="2" fill="var(--ink)" />
          <rect x="90" y="11" width="12" height="25" rx="2" fill="var(--ok)" />
        </svg>
      )
  }
}

export function ChartReasoningBox({ slideId, number, chart, onSelectChart, disabled = false, hideOptions = false }: ChartReasoningBoxProps) {
  const alternativesWithReasons = chart.options.filter((option) => !option.active && option.reasons.length > 0)

  return (
    <div className={styles.chartBox} data-chart-type={chart.type} aria-label={`Почему такой график на слайде ${number}`}>
      <div className={styles.chartBoxMain}>
        <div className={styles.chartPreview} data-chart-type={chart.type} aria-label={`Мини-превью графика: ${chart.label}`}>
          {chart.previewArtifactId ? (
            <img className={styles.chartPreviewImg} src={artifactUrl(chart.previewArtifactId)} alt={chart.label} />
          ) : (
            <ChartMiniSvg type={chart.type} />
          )}
        </div>
        <div className={styles.chartWhyBlock}>
          <div className={styles.chartWhyLabel}>Почему такой график</div>
          <div className={styles.chartWhyText}>{chart.why}</div>
          {alternativesWithReasons.length > 0 && (
            <ul className={styles.chartAltReasons} aria-label={`Почему подходят альтернативы на слайде ${number}`}>
              {alternativesWithReasons.slice(0, 3).map((option) => (
                <li key={option.type} className={styles.chartAltReasonItem}>
                  <span className={styles.chartAltReasonName}>{option.label}:</span> {option.reasons.join(', ')}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
      {!hideOptions && chart.options.length > 0 && (
        <div className={styles.chartAlternatives} role="group" aria-label={`Альтернативы графика слайда ${number}`}>
          {chart.options.map((option) =>
            onSelectChart ? (
              <button
                key={option.type}
                type="button"
                className={styles.chartChip}
                aria-pressed={option.active}
                disabled={disabled}
                title={option.reasons.length > 0 ? option.reasons.join(', ') : undefined}
                onClick={() => {
                  if (!option.active) onSelectChart(slideId, option.type)
                }}
              >
                {option.active ? `● ${option.label}` : option.label}
              </button>
            ) : (
              <span key={option.type} className={styles.chartChip} data-active={option.active || undefined}>
                {option.active ? `● ${option.label}` : option.label}
              </span>
            ),
          )}
        </div>
      )}
    </div>
  )
}

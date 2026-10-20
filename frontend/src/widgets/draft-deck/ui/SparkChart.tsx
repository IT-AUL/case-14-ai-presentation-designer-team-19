import type { DataPreview } from '@/entities/assistant'
import { buildSparkGeometry } from '../lib/understanding'

const WIDTH = 220
const HEIGHT = 72
const SLICES = ['var(--ink)', 'var(--focus)', 'var(--ink-3)', 'var(--line-2)', 'var(--sunken)']

export interface SparkChartProps {
  preview: DataPreview | null
  chartType?: string | null
  label: string
}

export function SparkChart({ preview, chartType, label }: SparkChartProps) {
  const geo = buildSparkGeometry(preview, chartType, WIDTH, HEIGHT)
  if (geo.values.length === 0) return null
  return (
    <svg
      viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
      width="100%"
      role="img"
      aria-label={label}
      data-chart-type={geo.chartType}
      preserveAspectRatio="xMidYMax meet"
    >
      {geo.chartType === 'bar' &&
        geo.bars.map((bar, index) => (
          <rect
            key={`${bar.label}-${index}`}
            x={bar.x}
            y={bar.y}
            width={bar.width}
            height={bar.height}
            rx="1.5"
            fill={bar.isPeak ? 'var(--focus)' : 'var(--ink-3)'}
          />
        ))}
      {geo.chartType === 'histogram' && (
        <g data-chart="histogram">
          <line x1="8" y1={HEIGHT - 8} x2={WIDTH - 8} y2={HEIGHT - 8} stroke="var(--line-2)" strokeWidth="1" />
          {geo.bars.map((bar, index) => (
            <rect
              key={`${bar.label}-${index}`}
              x={bar.x}
              y={bar.y}
              width={bar.width}
              height={bar.height}
              rx="1"
              fill={bar.isPeak ? 'var(--focus)' : 'var(--ink-3)'}
              fillOpacity={bar.isPeak ? '0.95' : '0.72'}
            />
          ))}
        </g>
      )}
      {geo.chartType === 'scatter' && (
        <g data-chart="scatter">
          {geo.trendLinePath && (
            <path
              d={geo.trendLinePath}
              fill="none"
              stroke="var(--ink-3)"
              strokeWidth="1.5"
              strokeDasharray="4 3"
              strokeLinecap="round"
              data-trend-line
            />
          )}
          {geo.points.map((pt, index) => {
            const isPeak = geo.peakPoint?.x === pt.x && geo.peakPoint?.y === pt.y
            return (
              <circle
                key={`${pt.label}-${index}`}
                cx={pt.x}
                cy={pt.y}
                r={isPeak ? '4' : '3'}
                fill={isPeak ? 'var(--focus)' : 'var(--ink)'}
                fillOpacity={isPeak ? '1' : '0.72'}
              />
            )
          })}
        </g>
      )}
      {geo.chartType === 'pie' && (
        <g transform={`translate(${WIDTH / 2}, ${HEIGHT / 2})`}>
          {geo.segments.map((segment, index) => (
            <circle
              key={`${segment.label}-${index}`}
              r="24"
              fill="none"
              stroke={SLICES[index % SLICES.length]}
              strokeWidth="12"
              strokeDasharray={segment.dashArray}
              strokeDashoffset={segment.dashOffset}
              transform="rotate(-90)"
            />
          ))}
        </g>
      )}
      {(geo.chartType === 'line' || geo.chartType === 'kpi' || geo.chartType === 'table') && (
        <g>
          {geo.areaPath && <path d={geo.areaPath} fill="var(--focus)" fillOpacity="0.08" />}
          {geo.linePath && <path d={geo.linePath} fill="none" stroke="var(--ink)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />}
          {geo.peakPoint && <circle cx={geo.peakPoint.x} cy={geo.peakPoint.y} r="3.5" fill="var(--focus)" />}
        </g>
      )}
    </svg>
  )
}

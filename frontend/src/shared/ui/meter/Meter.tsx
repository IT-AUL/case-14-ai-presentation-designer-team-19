import { clsx } from 'clsx'
import styles from './Meter.module.css'

interface MeterProps {
  value: number
  label?: string
  height?: 4 | 6 | 8 | 10 | 14
  color?: string
  marker?: number
  className?: string
}

export function Meter({ value, label, height = 6, color = 'var(--ink)', marker, className }: MeterProps) {
  const percent = Math.max(0, Math.min(1, value)) * 100
  return (
    <div
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(percent)}
      className={clsx(styles.track, className)}
      style={{ height, borderRadius: height / 2 }}
    >
      <div className={styles.fill} style={{ width: `${percent}%`, background: color, borderRadius: height / 2 }} />
      {marker !== undefined && <div className={styles.marker} style={{ left: `${marker * 100}%` }} />}
    </div>
  )
}

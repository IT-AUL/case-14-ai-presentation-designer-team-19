import { formatBytes, formatPercent } from '@/shared/lib/format'
import styles from './UploadProgress.module.css'

const MB = 1024 * 1024

const ANALYSIS_GROUPS = ['Тема и палитра', 'Шрифты заявленные и фактические', 'Сетка, поля и якоря', 'Макеты и роли слайдов']

interface UploadProgressProps {
  fileName: string
  totalBytes: number
  phase: 'uploading' | 'analyzing'
  progress?: number
}

function loadedLabel(fraction: number, total: number): string {
  const loaded = total * fraction
  if (total < MB) return `${formatBytes(Math.round(loaded))} из ${formatBytes(total)}`
  return `${(loaded / MB).toFixed(1).replace('.', ',')} из ${formatBytes(total)}`
}

function extensionOf(name: string): string {
  return name.split('.').pop()?.toUpperCase().slice(0, 4) ?? 'PPTX'
}

export function UploadProgress({ fileName, totalBytes, phase, progress = 0 }: UploadProgressProps) {
  const fraction = phase === 'analyzing' ? 1 : Math.max(0, Math.min(1, progress))
  const percent = formatPercent(fraction)

  return (
    <section className={styles.card} aria-label={phase === 'uploading' ? 'Загрузка шаблона' : 'Разбор шаблона'} aria-busy>
      <div className={styles.head}>
        <div className={styles.file} aria-hidden>
          {extensionOf(fileName)}
        </div>
        <div className={styles.meta}>
          <div className={styles.name}>{fileName}</div>
          <div className={styles.bytes}>{phase === 'uploading' ? loadedLabel(fraction, totalBytes) : formatBytes(totalBytes)}</div>
        </div>
        {phase === 'uploading' ? (
          <div className={styles.percent}>Загрузка · {percent}</div>
        ) : (
          <div className={styles.pulse}>Разбираю мастер, макеты и слайды…</div>
        )}
      </div>
      <div
        className={styles.track}
        role="progressbar"
        aria-label={phase === 'uploading' ? 'Загружено' : 'Анализ шаблона'}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={phase === 'uploading' ? Math.round(fraction * 100) : undefined}
      >
        <div className={phase === 'analyzing' ? styles.fillAnalyzing : styles.fill} style={{ width: `${fraction * 100}%` }} />
      </div>
      <ul className={styles.groups}>
        {ANALYSIS_GROUPS.map((group) => (
          <li key={group}>{group}</li>
        ))}
      </ul>
    </section>
  )
}

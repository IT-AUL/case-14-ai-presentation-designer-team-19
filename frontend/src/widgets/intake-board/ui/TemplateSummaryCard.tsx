import type { TemplateSummary } from '@/entities/assistant'
import { artifactUrl } from '@/shared/api'
import { pluralize } from '@/shared/lib/format'
import { ArrowRight, Badge, Card } from '@/shared/ui'
import { roleLabel } from '../lib/kinds'
import styles from './SummaryCards.module.css'

interface TemplateSummaryCardProps {
  summary: TemplateSummary
  onOpenDna: () => void
}

export function TemplateSummaryCard({ summary, onOpenDna }: TemplateSummaryCardProps) {
  const roles = Object.entries(summary.roles)
  return (
    <Card
      title={summary.filename ?? 'Шаблон'}
      meta={summary.slides !== null ? `${summary.slides} ${pluralize(summary.slides, ['слайд', 'слайда', 'слайдов'])}` : undefined}
      actions={
        <Badge tone="ok" dot>
          ДНК извлечена
        </Badge>
      }
      className={styles.card}
    >
      {roles.length > 0 && (
        <ul className={styles.roleChips} aria-label="Состав слайдов">
          {roles.map(([role, count]) => (
            <li key={role} className={styles.roleChip}>
              {roleLabel(role)}: {count}
            </li>
          ))}
        </ul>
      )}
      {summary.preview_artifact_ids.length > 0 && (
        <ul className={styles.previews} aria-label="Образцы слайдов шаблона">
          {summary.preview_artifact_ids.slice(0, 4).map((artifactId) => (
            <li key={artifactId}>
              <img className={styles.preview} src={artifactUrl(artifactId)} alt="Образец слайда шаблона" loading="lazy" />
            </li>
          ))}
        </ul>
      )}
      {summary.palette.length > 0 && (
        <ul className={styles.swatches} aria-label="Палитра шаблона">
          {summary.palette.map((hex) => (
            <li key={hex} className={styles.swatch} title={hex}>
              <span className={styles.swatchColor} style={{ background: hex }} />
              <span className={styles.swatchHex}>{hex}</span>
            </li>
          ))}
        </ul>
      )}
      {(summary.heading_font || summary.body_font) && (
        <p className={styles.fonts}>
          {summary.heading_font && <span>Заголовки: {summary.heading_font}</span>}
          {summary.heading_font && summary.body_font && <span aria-hidden> · </span>}
          {summary.body_font && <span>Текст: {summary.body_font}</span>}
        </p>
      )}
      <div className={styles.cardFoot}>
        <button type="button" className={styles.dnaLink} onClick={onOpenDna}>
          Все детали ДНК
          <ArrowRight size={14} aria-hidden />
        </button>
      </div>
    </Card>
  )
}

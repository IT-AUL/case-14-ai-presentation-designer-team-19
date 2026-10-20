import type { BrandbookSummary, BrandPriority } from '@/entities/assistant'
import { Badge, Button, Card, Segmented } from '@/shared/ui'
import styles from './SummaryCards.module.css'

interface BrandbookSummaryCardProps {
  summary: BrandbookSummary
  priority: BrandPriority
  onPriority: (priority: BrandPriority) => void
  onAnalyze?: () => void
  analyzing?: boolean
  pending?: boolean
}

const PRIORITY_OPTIONS: readonly { value: BrandPriority; label: string }[] = [
  { value: 'brandbook', label: 'Брендбук' },
  { value: 'template', label: 'Шаблон' },
]

const PRIORITY_LABEL: Record<BrandPriority, string> = {
  brandbook: 'Приоритет: брендбук',
  template: 'Приоритет: шаблон',
}

const PRIORITY_HINT: Record<BrandPriority, string> = {
  brandbook: 'Геометрия макетов — из шаблона, цвета и шрифты — из брендбука',
  template: 'Цвета, шрифты и макеты — из шаблона, брендбук только для сверки',
}

function parseSourceReading(item: Record<string, unknown>, fallbackQuote?: string | null): { page: string | null; quote: string | null } {
  const source = typeof item.source === 'string' ? item.source : ''
  const pageMatch = /(?:page=|стр\.?\s*)(\d+)/i.exec(source)
  const page = typeof item.page === 'number' ? `стр. ${item.page}` : pageMatch?.[1] ? `стр. ${pageMatch[1]}` : null
  const visionMatch = /:vision:\s*(.+)$/i.exec(source)
  const rawQuote = typeof item.quote === 'string' ? item.quote : visionMatch?.[1] ?? (page ? fallbackQuote ?? null : null)
  const quote = rawQuote ? rawQuote.trim() || null : null
  return { page, quote }
}

export function BrandbookSummaryCard({ summary, priority, onPriority, onAnalyze, analyzing, pending }: BrandbookSummaryCardProps) {
  const heading = summary.fonts.find((font) => font.role === 'heading')?.family ?? null
  const body = summary.fonts.find((font) => font.role === 'body')?.family ?? null
  const logoNames = summary.logos.map((logo) => logo.file_name).filter((name): name is string => Boolean(name))
  const meta = summary.conflicts.length ? `Расхождения с шаблоном: ${summary.conflicts.length}` : 'Совпадает с шаблоном'
  return (
    <Card
      title={summary.name ?? 'Брендбук'}
      meta={meta}
      actions={
        <>
          {onAnalyze && (
            <Button variant="secondary" size="sm" disabled={analyzing} onClick={onAnalyze}>
              Разобрать брендбук
            </Button>
          )}
          <Segmented options={PRIORITY_OPTIONS} value={priority} onChange={onPriority} label="Приоритет оформления" size="sm" />
        </>
      }
      className={styles.card}
    >
      {pending && <p className={styles.busy} role="status">Сохраняю приоритет…</p>}
      {summary.palette.length > 0 && (
        <ul className={styles.swatches} aria-label="Палитра брендбука">
          {summary.palette.map((color, index) => {
            const reading = parseSourceReading(color as Record<string, unknown>, color.name)
            return (
              <li key={`${color.hex ?? color.name ?? index}`} className={styles.swatch} title={color.name ?? color.hex ?? ''}>
                <span className={styles.swatchColor} style={{ background: color.hex ?? 'transparent' }} />
                <span className={styles.swatchHex}>
                  {color.hex ?? color.name}
                  {color.role ? ` · ${color.role}` : ''}
                  {reading.quote ? ` · «${reading.quote}»` : ''}
                  {reading.page ? ` (${reading.page})` : ''}
                </span>
              </li>
            )
          })}
        </ul>
      )}
      {summary.fonts.length > 0 && (
        <div className={styles.fonts}>
          {(heading || body) && (
            <p className={styles.fonts}>
              {heading && <span>Заголовки: {heading}</span>}
              {heading && body && <span aria-hidden> · </span>}
              {body && <span>Текст: {body}</span>}
            </p>
          )}
          {summary.fonts.map((font, index) => {
            const reading = parseSourceReading(font as Record<string, unknown>, null)
            if (!reading.quote && !reading.page) return null
            return (
              <p key={`${font.family ?? index}`} className={styles.fonts}>
                {font.family}
                {reading.quote ? ` — «${reading.quote}»` : ''}
                {reading.page ? ` (${reading.page})` : ''}
              </p>
            )
          })}
        </div>
      )}
      {logoNames.length > 0 && (
        <ul className={styles.logos} aria-label="Логотипы брендбука">
          {logoNames.map((name) => (
            <li key={name}>{name}</li>
          ))}
        </ul>
      )}
      {summary.warnings.length > 0 && (
        <ul className={styles.warnings} aria-label="Замечания по брендбуку">
          {summary.warnings.map((warning, index) => (
            <li key={index}>{warning}</li>
          ))}
        </ul>
      )}
      {summary.conflicts.length > 0 && (
        <ul className={styles.conflicts} aria-label="Расхождения с шаблоном">
          {summary.conflicts.map((conflict, index) => (
            <li key={index} className={styles.conflict} title={conflict.note ?? undefined}>
              <span className={styles.conflictField}>{conflict.field ?? 'поле'}</span>
              <span className={styles.conflictPair}>
                {conflict.template && (
                  <span className={styles.conflictFrom}>
                    {conflict.kind === 'color' && <span className={styles.swatchColor} style={{ background: conflict.template }} />}
                    {conflict.template}
                  </span>
                )}
                {conflict.template && conflict.brandbook && <span aria-hidden>→</span>}
                {conflict.brandbook && (
                  <span className={styles.conflictTo}>
                    {conflict.kind === 'color' && <span className={styles.swatchColor} style={{ background: conflict.brandbook }} />}
                    {conflict.brandbook}
                  </span>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
      <div className={styles.priority}>
        <span className={styles.priorityHint}>{PRIORITY_HINT[priority]}</span>
        <Badge tone={priority === 'brandbook' ? 'warn' : 'neutral'}>{PRIORITY_LABEL[priority]}</Badge>
      </div>
    </Card>
  )
}

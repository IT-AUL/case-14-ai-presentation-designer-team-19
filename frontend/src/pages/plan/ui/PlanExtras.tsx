import {
  formatFactsByOriginText,
  type ContentFillMode,
  type ContentFillSummary,
  type PlanSufficiency,
  type ResearchSource,
} from '@/entities/generation'
import { pluralize } from '@/shared/lib/format'
import { Button, Icon, X } from '@/shared/ui'
import styles from './PlanPage.module.css'

export interface PlanAdjustment {
  contentFill?: ContentFillMode
  slideCount?: number
}

interface PlanExtrasProps {
  sufficiency: PlanSufficiency | null
  contentFill?: ContentFillSummary | null
  sources: readonly ResearchSource[]
  targetSlides: number
  acceptedShortage?: boolean
  disabled: boolean
  onAdjust: (adjustment: PlanAdjustment) => void
  onRemoveSource: (sourceId: string) => void
  onAddFiles?: () => void
  onAnswerQuestions?: () => void
}

export function PlanExtras({
  sufficiency,
  contentFill = null,
  sources,
  targetSlides,
  acceptedShortage = false,
  disabled,
  onAdjust,
  onRemoveSource,
  onAddFiles,
  onAnswerQuestions,
}: PlanExtrasProps) {
  const hasEstimate = sufficiency !== null || Boolean(contentFill)
  const content = contentFill?.capacity ?? sufficiency?.contentSlides ?? null
  const rawRequested = contentFill?.requested ?? sufficiency?.targetSlides ?? targetSlides
  const requested = acceptedShortage && content !== null && targetSlides <= content ? targetSlides : rawRequested
  const isShort =
    hasEstimate &&
    content !== null &&
    requested > 0 &&
    content < requested &&
    (sufficiency?.status === 'short' ||
      (contentFill?.capacity !== null &&
        contentFill?.capacity !== undefined &&
        contentFill?.requested !== null &&
        contentFill?.requested !== undefined &&
        contentFill.capacity < contentFill.requested))
  const isEnough =
    hasEstimate &&
    !isShort &&
    content !== null &&
    content >= targetSlides &&
    (sufficiency?.status === 'short' || sufficiency?.status === 'ok' || sufficiency?.status === 'excess' || Boolean(contentFill))
  const reserve = content !== null && content > targetSlides ? `, запас — до ${content}` : ''
  const factsLine = formatFactsByOriginText(contentFill)
  const effectiveSources = sources.length > 0 ? sources : (contentFill?.webSources ?? [])

  return (
    <>
      {isShort && content !== null && (
        <div className={styles.extra} role="status">
          <p className={styles.extraText}>
            Материалов хватит примерно на {content} {pluralize(content, ['слайд', 'слайда', 'слайдов'])} из {requested}.
          </p>
          {contentFill?.shortageReason && <p className={styles.hint}>{contentFill.shortageReason}</p>}
          <div className={styles.inline}>
            {onAddFiles && (
              <Button variant="secondary" size="sm" disabled={disabled} onClick={onAddFiles}>
                Добавить файлы
              </Button>
            )}
            {onAnswerQuestions && (
              <Button variant="secondary" size="sm" disabled={disabled} onClick={onAnswerQuestions}>
                Ответить на вопросы
              </Button>
            )}
            <Button variant="secondary" size="sm" disabled={disabled} onClick={() => onAdjust({ contentFill: 'keep', slideCount: content })}>
              Собрать {content} {pluralize(content, ['слайд', 'слайда', 'слайдов'])}
            </Button>
            <Button variant="ghost" size="sm" disabled={disabled} onClick={() => onAdjust({ contentFill: 'expand' })}>
              Дополнить
            </Button>
          </div>
        </div>
      )}
      {isEnough && (
        <div className={styles.extra}>
          <p className={styles.extraText}>
            Материала достаточно для {targetSlides} {pluralize(targetSlides, ['слайда', 'слайдов', 'слайдов'])}
            {reserve}.
            {sufficiency?.status === 'excess' ? ' Самое важное оставлю, остальное сожму.' : ''}
          </p>
        </div>
      )}
      {!isEnough && sufficiency?.status === 'excess' && (
        <div className={styles.extra}>
          <p className={styles.extraText}>
            Материала больше, чем помещается в {targetSlides} {pluralize(targetSlides, ['слайд', 'слайда', 'слайдов'])}. Самое важное оставлю, остальное сожму.
          </p>
        </div>
      )}
      {factsLine && (
        <div className={styles.extra}>
          <p className={styles.hint}>{factsLine}</p>
        </div>
      )}
      {effectiveSources.length > 0 && (
        <div className={styles.extra}>
          <p className={styles.extraText}>Часть фактов нашёл в интернете:</p>
          <ul className={styles.sources}>
            {effectiveSources.map((source) => (
              <li key={source.id} className={styles.source}>
                {source.url ? (
                  <a href={source.url} target="_blank" rel="noreferrer noopener">
                    {source.site ?? source.title}
                  </a>
                ) : (
                  <span>{source.site ?? source.title}</span>
                )}
                <button type="button" className={styles.iconButton} disabled={disabled} aria-label={`Не использовать ${source.site ?? source.title}`} onClick={() => onRemoveSource(source.id)}>
                  <Icon as={X} size={14} />
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  )
}


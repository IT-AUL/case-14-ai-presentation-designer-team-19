import { useState } from 'react'
import { useSlideExplain } from '@/entities/assistant'
import { useContentPack } from '@/entities/content-pack'
import {
  formatFactsByOriginText,
  formatShortageExplanationText,
  parseContentFill,
  parseResearch,
  useGeneration,
} from '@/entities/generation'
import { useVariantSlides } from '@/entities/variant'
import { useSlideChangeQueue } from '@/features/slide-change'
import { Button } from '@/shared/ui'
import { ChartReasoningBox, contentPackSourceMap } from '@/widgets/deck-plan'
import {
  buildExplainView,
  buildReworkChips,
  buildSlideChartViewFromExplain,
  buildSlideNotesView,
  chartTypeLabel,
  findPlanSlide,
  findSlideAt,
  type ReworkChip,
} from '../model/slideModel'
import styles from './SlidePanel.module.css'

interface SlidePanelProps {
  runId: string
  variantId: string
  strategy: string
  slideNumber: number
}

export function SlidePanel({ runId, variantId, strategy, slideNumber }: SlidePanelProps) {
  const slides = useVariantSlides(variantId)
  const generation = useGeneration(runId)
  const queue = useSlideChangeQueue(variantId)
  const slide = findSlideAt(slides.data ?? [], slideNumber)
  const planSlide = findPlanSlide(generation.data?.deck_plan, slide, slideNumber)
  const explain = useSlideExplain(slide?.id)
  const view = buildExplainView(explain.data, planSlide, slide)
  const chartBoxView = buildSlideChartViewFromExplain(view, planSlide)
  const chips = buildReworkChips(strategy, view.alternatives, view.hasChart)
  const contentPack = useContentPack(generation.data?.content_pack_id)
  const headings = contentPackSourceMap(contentPack.data, generation.data?.deck_plan)
  const explainDatasetLabel = explain.data?.dataset?.title?.trim() || explain.data?.dataset?.file?.trim()
  if (explainDatasetLabel) {
    if (explain.data?.dataset?.id) headings.set(explain.data.dataset.id, explainDatasetLabel)
    if (!headings.has('ds:*')) headings.set('ds:*', explainDatasetLabel)
  }
  const contentFill = parseContentFill(
    (generation.data as unknown as Record<string, unknown> | undefined)?.content_fill,
    generation.data,
  )
  const research =
    parseResearch(
      (generation.data?.deck_plan as unknown as Record<string, unknown> | undefined)?.research,
      (generation.data as unknown as Record<string, unknown> | undefined)?.content_fill,
    )?.sources ?? []
  const notes = buildSlideNotesView(planSlide, view, headings, research)
  const factsLine = formatFactsByOriginText(contentFill)
  const shortageLine = formatShortageExplanationText(
    contentFill,
    generation.data?.deck_plan?.slides.length ?? slides.data?.length ?? null,
  )
  const webSources = contentFill && contentFill.webSources.length > 0 ? contentFill.webSources : []
  const [pendingKey, setPendingKey] = useState<string | null>(null)

  const planSlideId = planSlide?.id ?? slide?.slide_plan_id ?? slide?.id ?? null
  const fallbackSlideId = slide?.id ?? planSlide?.id ?? null
  const candidateSlideIds = [planSlide?.id, slide?.slide_plan_id, slide?.id]

  const slideChange = queue.getSlideChange(candidateSlideIds)
  const note = queue.getSlideNote(candidateSlideIds)
  const error = queue.getSlideError(candidateSlideIds)
  const isSubmitting = Boolean(planSlideId && queue.submittingSlideId === planSlideId)
  const changeState = slideChange?.state ?? (note ? 'completed' : null)
  const isBusy = isSubmitting || changeState === 'queued' || changeState === 'running'

  const rework = async (chip: ReworkChip) => {
    if (!planSlideId || isBusy) return
    setPendingKey(chip.key)
    try {
      await queue.submitChange({
        slideId: planSlideId,
        fallbackSlideId,
        action: chip.action,
        chartType: chip.chartType ?? null,
        fallbackRevision: slide?.revision ?? 1,
      })
    } finally {
      setPendingKey(null)
    }
  }

  const slideTitle = slide?.title ?? null
  const why = view.hasChart
    ? (view.whySummary ?? 'График подобран под структуру данных и задачу сравнения.')
    : (notes.keyMessage
        ? `Ключевой тезис: «${notes.keyMessage}». Макет подобран под роль слайда и плотность пунктов с сохранением шрифтовой шкалы шаблона.`
        : view.fallbackNote)

  return (
    <div className={styles.panel}>
      <section className={styles.section} aria-labelledby="slide-rework">
        <h3 id="slide-rework" className={styles.heading}>
          Переделать слайд {slideNumber}
        </h3>
        <p className={styles.text}>
          {slideTitle ? `«${slideTitle}» — пересоберу только его, остальные слайды не изменятся.` : 'Остальные слайды не изменятся.'}
        </p>
        {queue.unsupported ? (
          <p className={styles.text} role="status">
            Сервер пока не умеет пересобирать один слайд.
          </p>
        ) : (
          <div className={styles.actions}>
            {chips.map((chip) => (
              <Button
                key={chip.key}
                size="sm"
                variant="secondary"
                disabled={isBusy}
                aria-busy={pendingKey === chip.key || undefined}
                title={chip.title}
                onClick={() => void rework(chip)}
              >
                {pendingKey === chip.key ? 'Переделываю…' : chip.label}
              </Button>
            ))}
          </div>
        )}
        {changeState === 'queued' && (
          <div className={styles.queueStatusRow} role="status">
            <span className={styles.queueBadge} data-state="queued">
              Правка в очереди
            </span>
            {slideChange && (
              <Button
                size="sm"
                variant="ghost"
                disabled={queue.cancellingChangeId === slideChange.change_id}
                onClick={() => void queue.cancelChange(slideChange.change_id)}
              >
                Отменить
              </Button>
            )}
          </div>
        )}
        {(changeState === 'running' || (isSubmitting && !changeState)) && (
          <div className={styles.queueStatusRow} role="status">
            <span className={styles.queueBadge} data-state="running">
              Применяю
            </span>
          </div>
        )}
        {changeState === 'completed' && (
          <div className={styles.queueStatusRow} role="status">
            <span className={styles.queueBadge} data-state="completed">
              Готово
            </span>
            {note && <span className={styles.note}>{note}</span>}
          </div>
        )}
        {error && (
          <p className={styles.error} role="alert">
            {error}
          </p>
        )}
      </section>

      <details className={styles.explainDrawer}>
        <summary className={styles.explainSummary}>Почему так и заметки докладчика</summary>
        <div className={styles.explainBody}>
          <section className={styles.section} aria-labelledby="slide-why">
            <h3 id="slide-why" className={styles.heading}>
              Почему так
            </h3>
            {view.exemplarLabel && <p className={styles.badge}>{view.exemplarLabel}</p>}
            {chartBoxView ? (
              <ChartReasoningBox
                slideId={planSlideId ?? String(slideNumber)}
                number={String(slideNumber).padStart(2, '0')}
                chart={chartBoxView}
                disabled={isBusy}
                onSelectChart={(_, chartType) =>
                  void rework({
                    key: `other_chart:${chartType}`,
                    action: 'other_chart',
                    chartType,
                    label: `График: ${chartTypeLabel(chartType)}`,
                    title: `Поменять график на «${chartTypeLabel(chartType)}»`,
                  })
                }
              />
            ) : (
              <p className={styles.text}>{why}</p>
            )}
            {view.hasChart && view.chosenLabel && (
              <p className={styles.muted}>
                Вид визуализации: {view.chosenLabel}
                {view.taskText ? ` · ${view.taskText}` : ''}
              </p>
            )}
            {view.datasetLabel && <p className={styles.muted}>Данные: {view.datasetLabel}</p>}
            {view.reasons.length > 0 && (
              <ul className={styles.list}>
                {view.reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            )}
            {view.rejected.length > 0 && (
              <details className={styles.details}>
                <summary>Почему не другие графики</summary>
                <ul>
                  {view.rejected.map((item) => (
                    <li key={item.type}>
                      {item.label}: {item.reason ?? 'не подходит под структуру данных'}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </section>

          <section className={styles.section} aria-labelledby="slide-notes">
            <h3 id="slide-notes" className={styles.heading}>
              Заметки докладчика
            </h3>
            {notes.origins.length > 0 && (
              <div className={styles.origins}>
                {notes.origins.map((origin) => (
                  <span key={origin.kind} className={styles.badge}>
                    {origin.label}
                  </span>
                ))}
              </div>
            )}
            <p className={styles.text}>
              {notes.speakerNote ??
                (notes.keyMessage
                  ? `Сделайте акцент на главном выводе: ${notes.keyMessage}`
                  : 'Отдельные заметки для этого слайда не заданы — опирайтесь на заголовок и тезисы слайда.')}
            </p>
            {notes.sources.length > 0 && (
              <p className={styles.muted}>Опирается на: {notes.sources.map((source) => source.label).join(', ')}</p>
            )}
            {(shortageLine || factsLine || webSources.length > 0) && (
              <div className={styles.contentFillBox} aria-label="Материалы и источники">
                {shortageLine && <p className={styles.muted}>{shortageLine}</p>}
                {factsLine && <p className={styles.muted}>{factsLine}</p>}
                {webSources.length > 0 && (
                  <ul className={styles.sourceLinks} aria-label="Открытые источники">
                    {webSources.map((source) => (
                      <li key={source.id}>
                        {source.url ? (
                          <a href={source.url} target="_blank" rel="noreferrer noopener" className={styles.sourceLink}>
                            {source.site ?? source.title}
                          </a>
                        ) : (
                          <span>{source.site ?? source.title}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </section>
        </div>
      </details>
    </div>
  )
}

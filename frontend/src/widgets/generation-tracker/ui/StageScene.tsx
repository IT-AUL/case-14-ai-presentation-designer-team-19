import { clsx } from 'clsx'
import { Link } from 'react-router'
import { formatIndex, formatNumber, formatPercent, pluralize } from '@/shared/lib/format'
import { ArrowRight, Badge, Icon, Mono } from '@/shared/ui'
import type { FilmstripSlot } from '../lib/filmstrip'
import { resultSummary, sceneProgress, type ResultSummary } from '../lib/scene'
import type { VariantCard } from '../lib/variants'
import { SlideFan } from './SlideFan'
import styles from './StageScene.module.css'

interface StageSceneProps {
  card: VariantCard
  failureMessage: string | null
  auditHref: ((variantId: string, slideNumber?: number) => string) | null
  assistantNote?: string | null
  activeSlot?: FilmstripSlot | null
}

function resultParts({ styleFidelity, autoFixed, openIssues }: ResultSummary): string[] {
  return [
    styleFidelity !== null && `${formatPercent(styleFidelity)} в стиле шаблона`,
    autoFixed !== null && autoFixed > 0 && `${formatNumber(autoFixed)} ${pluralize(autoFixed, ['правка сделана', 'правки сделаны', 'правок сделано'])} сразу`,
    openIssues !== null &&
      (openIssues === 0
        ? 'замечаний нет'
        : `${formatNumber(openIssues)} ${pluralize(openIssues, ['замечание ждёт', 'замечания ждут', 'замечаний ждут'])} вашего решения`),
  ].filter((part): part is string => Boolean(part))
}

function Progress({ card, assistantNote }: { card: VariantCard; assistantNote?: string | null }) {
  const { phrase, steps } = sceneProgress(card)
  return (
    <>
      <p className={styles.phrase} aria-live="polite">
        {assistantNote || phrase}
      </p>
      <ol className={styles.steps} aria-label="Этапы сборки">
        {steps.map((step) => (
          <li key={step.stage} className={clsx(styles.step, styles[step.state])} aria-current={step.state === 'active' ? 'step' : undefined}>
            <span className={styles.bar} aria-hidden />
            <span className={styles.stepLabel}>{step.label}</span>
          </li>
        ))}
      </ol>
    </>
  )
}

function Result({ card, auditHref }: { card: VariantCard; auditHref: StageSceneProps['auditHref'] }) {
  const variant = card.variant
  if (!variant) return null
  const summary = resultSummary(variant)
  const parts = resultParts(summary)
  return (
    <div className={styles.result}>
      <p className={styles.resultText}>
        {parts.length > 0 ? parts.join(' · ') : 'Колода собрана'}
        {summary.opensCleanly === false && <span className={styles.warn}> · файл открылся с ошибками</span>}
      </p>
      {auditHref && (
        <Link className={styles.open} to={auditHref(variant.id)}>
          Открыть и проверить
          <Icon as={ArrowRight} size={14} />
        </Link>
      )}
    </div>
  )
}

export function StageScene({ card, failureMessage, auditHref, assistantNote, activeSlot }: StageSceneProps) {
  const { strategy, state, variant } = card
  const showActiveSlide = activeSlot?.state === 'ready' && Boolean(activeSlot.imageUrl)

  return (
    <section className={styles.scene} aria-label={`Сборка варианта «${strategy.name}»`}>
      <header className={styles.head}>
        <h2 className={styles.name}>{strategy.name}</h2>
        <p className={styles.axis}>{strategy.axis}</p>
      </header>
      {showActiveSlide && activeSlot?.imageUrl ? (
        <div className={styles.activeSlide} data-testid="stage-active-slide">
          <div className={styles.activeSlideFrame}>
            <img
              className={styles.activeSlideImg}
              src={activeSlot.imageUrl}
              alt={`Превью слайда ${activeSlot.slideNumber}: ${activeSlot.title}`}
            />
          </div>
          <div className={styles.activeSlideCaption}>
            <Badge tone="ok">✓ Готов</Badge>
            <Mono>Слайд {formatIndex(activeSlot.slideNumber)}</Mono>
            <span>{activeSlot.title}</span>
          </div>
        </div>
      ) : (
        <SlideFan key={`${card.key}:${state}`} state={state} variant={variant} name={strategy.name} />
      )}
      {state === 'done' ? <Result card={card} auditHref={auditHref} /> : <Progress card={card} assistantNote={assistantNote} />}
      {state === 'error' && failureMessage && <p className={styles.error}>{failureMessage}</p>}
    </section>
  )
}

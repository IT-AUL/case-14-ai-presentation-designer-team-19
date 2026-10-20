import { useNow } from '@/shared/lib/time'
import { Button, Check, ExternalLink, Icon, Search, X } from '@/shared/ui'
import type { PlanProgress } from '@/entities/generation'
import type { PlanBuildPhase } from '../model/usePlanBuild'
import styles from './PlanBuildProgress.module.css'

interface PlanBuildProgressProps {
  progress: PlanProgress
  phase: PlanBuildPhase | null
  startedAt: number | null
  onCancel: () => void
}

type StepState = 'done' | 'active' | 'waiting'

const RESEARCH_STAGES = ['research', 'search', 'web']

function isResearchStage(stage: string | null): boolean {
  return Boolean(stage && RESEARCH_STAGES.some((key) => stage.toLowerCase().includes(key)))
}

function formatElapsed(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  const rest = seconds % 60
  return `${minutes}:${String(rest).padStart(2, '0')}`
}

function steps(progress: PlanProgress, phase: PlanBuildPhase | null): { label: string; state: StepState }[] {
  const researching = progress.queries.length > 0 || progress.sources.length > 0 || isResearchStage(progress.stage)
  const drafting = progress.slides.length > 0
  const planning = phase === 'plan'
  return [
    { label: 'Разбираю материалы', state: planning ? 'done' : 'active' },
    ...(researching ? [{ label: 'Ищу факты и источники', state: (drafting ? 'done' : 'active') as StepState }] : []),
    { label: 'Собираю структуру слайдов', state: planning && (drafting || !researching) ? 'active' : 'waiting' },
  ]
}

export function PlanBuildProgress({ progress, phase, startedAt, onCancel }: PlanBuildProgressProps) {
  const now = useNow(1000, startedAt !== null)
  const elapsed = startedAt === null ? 0 : Math.max(0, Math.round((now - startedAt) / 1000))
  const { queries, sources, slides } = progress

  return (
    <section className={styles.panel} aria-live="polite" aria-busy="true" aria-label="Построение плана">
      <header className={styles.head}>
        <div>
          <h2 className={styles.title}>Строю план колоды</h2>
          <p className={styles.note}>Обычно от 10 секунд до минуты. План можно будет поправить до вёрстки.</p>
        </div>
        <span className={styles.timer}>{formatElapsed(elapsed)}</span>
        <Button variant="secondary" size="md" onClick={onCancel}>
          <Icon as={X} size={14} />
          Отменить
        </Button>
      </header>
      <ol className={styles.steps}>
        {steps(progress, phase).map((step) => (
          <li key={step.label} className={styles.step} data-state={step.state}>
            <span className={styles.stepMark} aria-hidden="true">
              {step.state === 'done' && <Icon as={Check} size={12} />}
            </span>
            {step.label}
          </li>
        ))}
      </ol>
      {queries.length > 0 && (
        <div className={styles.block}>
          <h3 className={styles.blockTitle}>Поисковые запросы</h3>
          <ul className={styles.queries}>
            {queries.map((query) => (
              <li key={query} className={styles.query}>
                <Icon as={Search} size={12} />
                {query}
              </li>
            ))}
          </ul>
        </div>
      )}
      {sources.length > 0 && (
        <div className={styles.block}>
          <h3 className={styles.blockTitle}>Найденные источники · {sources.length}</h3>
          <ul className={styles.sources}>
            {sources.map((source) => (
              <li key={source.id}>
                {source.url ? (
                  <a className={styles.source} href={source.url} target="_blank" rel="noreferrer noopener">
                    <span className={styles.sourceTitle}>{source.title}</span>
                    {source.site && <span className={styles.sourceSite}>{source.site}</span>}
                    <Icon as={ExternalLink} size={12} />
                  </a>
                ) : (
                  <span className={styles.source}>
                    <span className={styles.sourceTitle}>{source.title}</span>
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {slides.length > 0 && (
        <div className={styles.block}>
          <h3 className={styles.blockTitle}>Слайды · {slides.length}</h3>
          <ol className={styles.slides}>
            {slides.map((slide, index) => (
              <li key={slide.id} className={styles.slide}>
                <span className={styles.slideNumber}>{String(index + 1).padStart(2, '0')}</span>
                <span className={styles.slideTitle}>{slide.title_intent || 'Без заголовка'}</span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </section>
  )
}

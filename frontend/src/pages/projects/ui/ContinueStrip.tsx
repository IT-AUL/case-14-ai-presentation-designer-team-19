import { useState } from 'react'
import { Link } from 'react-router'
import { useGeneration, type GenerationDetail } from '@/entities/generation'
import type { Project } from '@/entities/project'
import { slidePreviewUrl, useVariantSlides } from '@/entities/variant'
import { pluralize } from '@/shared/lib/format'
import { formatRelativeDate } from '@/widgets/project-list'
import { projectView } from '../model/projectView'
import styles from './ContinueStrip.module.css'

const STRATEGY_PRIORITY = ['balanced', 'faithful', 'visual']

function coverVariantId(generation: GenerationDetail | undefined): string | null {
  const ready = (generation?.variants ?? []).filter((variant) => variant.status === 'completed')
  const ranked = [...ready].sort((a, b) => STRATEGY_PRIORITY.indexOf(a.strategy) - STRATEGY_PRIORITY.indexOf(b.strategy))
  return ranked[0]?.id ?? null
}

function useProjectCover(project: Project): { cover: string | null; slideCount: number | null } {
  const view = projectView(project)
  const generation = useGeneration(view.filter === 'ready' ? view.runId : null)
  const slides = useVariantSlides(coverVariantId(generation.data))
  const first = slides.data?.find((slide) => slide.preview_artifact_id)
  return { cover: slidePreviewUrl(first), slideCount: generation.data?.deck_plan?.slides.length ?? null }
}

function ContinueCard({ project }: { project: Project }) {
  const view = projectView(project)
  const { cover, slideCount } = useProjectCover(project)
  const [broken, setBroken] = useState(false)
  return (
    <li className={styles.item}>
      <Link to={view.href} className={styles.card}>
        <span className={styles.frame}>
          {cover && !broken ? (
            <img className={styles.image} src={cover} alt="" loading="lazy" onError={() => setBroken(true)} />
          ) : (
            <span className={styles.placeholder} aria-hidden>
              <span className={styles.placeholderLines}>
                <span />
                <span />
              </span>
            </span>
          )}
        </span>
        <span className={styles.meta}>
          <span className={styles.name}>{project.name}</span>
          <span className={styles.status}>
            <span className={styles.dot} data-tone={view.tone} aria-hidden />
            {view.label}
            {slideCount !== null && <span className={styles.count}>{`${slideCount} ${pluralize(slideCount, ['слайд', 'слайда', 'слайдов'])}`}</span>}
            <span className={styles.time}>{formatRelativeDate(project.updated_at)}</span>
          </span>
        </span>
      </Link>
    </li>
  )
}

export function ContinueStrip({ projects }: { projects: readonly Project[] }) {
  return (
    <section className={styles.section} aria-labelledby="continue-title">
      <h2 id="continue-title" className={styles.heading}>
        Продолжить
      </h2>
      <ul className={styles.grid} aria-label="Недавние проекты">
        {projects.map((project) => (
          <ContinueCard key={project.id} project={project} />
        ))}
      </ul>
    </section>
  )
}

import { useState } from 'react'
import { useParams } from 'react-router'
import { useAssistantSession, useUnderstanding } from '@/entities/assistant'
import { useContentPack } from '@/entities/content-pack'
import { isEditableUnit, type DeckPlan, type SlidePlan } from '@/entities/generation'
import { useSlideResearchOutcome, type QuickSlideAction, type SlidePatch } from '@/features/edit-deck-plan'
import { Button, Icon, LinkButton, X } from '@/shared/ui'
import {
  availableQuickActions,
  ChartReasoningBox,
  contentPackSourceMap,
  EDITOR_VISUALS,
  parseEvidenceRef,
  parseSlideChartView,
  slideSources,
  VISUAL_LABEL,
  type SourcePackLike,
} from '@/widgets/deck-plan'
import styles from './PlanPage.module.css'

const EDITOR_PURPOSES = ['title', 'summary', 'data', 'comparison', 'roadmap', 'closing'] as const
export type EditorSlidePurpose = (typeof EDITOR_PURPOSES)[number]

const EDITOR_PURPOSE_LABEL: Record<EditorSlidePurpose, string> = {
  title: 'Титул',
  summary: 'Вывод',
  data: 'Данные',
  comparison: 'Сравнение',
  roadmap: 'План',
  closing: 'Финал',
}

export type SlideEditorPatch = SlidePatch & { purpose?: SlidePlan['purpose'] | EditorSlidePurpose }

function resolveEditorPurpose(purpose: string): EditorSlidePurpose | null {
  if (purpose === 'title') return 'title'
  if (purpose === 'summary' || purpose === 'overview') return 'summary'
  if (purpose === 'data') return 'data'
  if (purpose === 'comparison') return 'comparison'
  if (purpose === 'roadmap' || purpose === 'agenda' || purpose === 'timeline' || purpose === 'process') return 'roadmap'
  if (purpose === 'closing' || purpose === 'thank_you' || purpose === 'cta') return 'closing'
  return null
}

const ACTION_LABEL: Partial<Record<QuickSlideAction, string>> = {
  shorten: 'Сократить',
  split: 'Разбить на два',
  merge_next: 'Объединить со следующим',
}

function findPlanPackId(plan: DeckPlan): string | null {
  for (const item of plan.slides) {
    for (const raw of item.evidence_ids) {
      const { packId } = parseEvidenceRef(raw)
      if (packId && packId !== 'ds') return packId
    }
  }
  return null
}

interface SlideEditorProps {
  plan: DeckPlan
  slide: SlidePlan
  first: boolean
  last: boolean
  researching: boolean
  canResearch: boolean
  researchResult?: string | null
  contentPack?: SourcePackLike | null
  onUpdate: (patch: SlideEditorPatch) => void
  onUnitChange: (position: number, text: string) => void
  onUnitAdd: () => void
  onUnitRemove: (position: number) => void
  onMove: (delta: -1 | 1) => void
  onChart: (type: string) => void
  onAction: (action: QuickSlideAction) => void
  onResearch: () => void
  onKeep: () => void
}

export function SlideEditor(props: SlideEditorProps) {
  const { plan, slide, first, last, researching, canResearch, researchResult, contentPack: packProp, onUpdate, onUnitChange, onUnitAdd, onUnitRemove, onMove, onChart, onAction, onResearch, onKeep } = props
  const { projectId } = useParams<{ projectId: string }>()
  const session = useAssistantSession(packProp !== undefined ? null : projectId)
  const packId = packProp !== undefined ? null : (session.data?.artifacts.content_pack_id ?? findPlanPackId(plan))
  const fetchedPack = useContentPack(packId)
  const fetchedUnderstanding = useUnderstanding(packId)
  const sourceMap = contentPackSourceMap(packProp ?? fetchedPack.data ?? fetchedUnderstanding.data, plan)
  const sources = slideSources(slide.evidence_ids, sourceMap)
  const units = slide.content_units.map((unit, position) => ({ unit, position })).filter(({ unit }) => isEditableUnit(unit))
  const chart = parseSlideChartView(slide)
  const actions = availableQuickActions(plan, slide.id).filter((action) => ACTION_LABEL[action as QuickSlideAction])
  const visual = slide.desired_visual ?? 'none'
  const activePurpose = resolveEditorPurpose(slide.purpose)
  const storedOutcome = useSlideResearchOutcome(slide.id)
  const [localRequested, setLocalRequested] = useState<{ slideId: string; baselineTexts: string[] } | null>(null)
  const currentTexts = units.map(({ unit }) => unit.text?.trim() ?? '').filter(Boolean)
  const localOutcomeMessage = (() => {
    if (!localRequested || localRequested.slideId !== slide.id) return null
    const added = currentTexts.slice(localRequested.baselineTexts.length)
    return added.length > 0 ? `Нашёл и добавил: ${added.join(', ')}` : 'Новых цифр в материалах не нашлось'
  })()
  const outcomeMessage = researchResult ?? storedOutcome?.message ?? localOutcomeMessage
  const handleResearch = () => {
    setLocalRequested({ slideId: slide.id, baselineTexts: currentTexts })
    onResearch()
  }

  return (
    <div className={styles.editor}>
      {sources.length > 0 && (
        <div className={styles.field}>
          <span>Опирается на</span>
          <p className={styles.hint}>{sources.map((source) => source.label).join(', ')}</p>
        </div>
      )}
      {slide.supports_key_message === false && (
        <div className={styles.offTopic}>
          <p>Этот слайд уводит от главной мысли.</p>
          <div className={styles.inline}>
            <Button variant="secondary" size="sm" onClick={() => onAction('remove')}>
              Убрать
            </Button>
            <Button variant="ghost" size="sm" onClick={onKeep}>
              Оставить
            </Button>
          </div>
        </div>
      )}

      <div className={styles.field}>
        <span>Роль слайда</span>
        <div className={styles.chips} role="radiogroup" aria-label="Роль слайда">
          {EDITOR_PURPOSES.map((role) => (
            <button key={role} type="button" role="radio" aria-checked={activePurpose === role} className={styles.chip} onClick={() => onUpdate({ purpose: role })}>
              {EDITOR_PURPOSE_LABEL[role]}
            </button>
          ))}
        </div>
      </div>

      <label className={styles.field}>
        <span>Заголовок — вывод слайда</span>
        <input className={styles.input} value={slide.title_intent} onChange={(event) => onUpdate({ title_intent: event.target.value })} />
      </label>

      <div className={styles.field}>
        <span>Пункты</span>
        <ul className={styles.units}>
          {units.map(({ unit, position }, order) => (
            <li key={position} className={styles.unit}>
              <textarea
                className={styles.input}
                rows={2}
                value={unit.text ?? ''}
                aria-label={`Пункт ${order + 1}`}
                onChange={(event) => onUnitChange(position, event.target.value)}
              />
              <button type="button" className={styles.iconButton} aria-label={`Убрать пункт ${order + 1}`} onClick={() => onUnitRemove(position)}>
                <Icon as={X} size={14} />
              </button>
            </li>
          ))}
        </ul>
        <LinkButton onClick={onUnitAdd}>
          Добавить пункт
        </LinkButton>
        {!researching && outcomeMessage && (
          <p className={styles.hint} role="status">
            {outcomeMessage}
          </p>
        )}
      </div>

      <div className={styles.field}>
        <span>Что показать</span>
        <div className={styles.chips} role="radiogroup" aria-label="Что показать на слайде">
          {EDITOR_VISUALS.map((option) => (
            <button key={option} type="button" role="radio" aria-checked={visual === option} className={styles.chip} onClick={() => onUpdate({ desired_visual: option })}>
              {VISUAL_LABEL[option] ?? option}
            </button>
          ))}
        </div>
      </div>

      {chart && (
        <div className={styles.field}>
          <span>График</span>
          <ChartReasoningBox slideId={slide.id} number={String(slide.index + 1).padStart(2, '0')} chart={chart} hideOptions={chart.options.length > 1} />
          {chart.options.length > 1 && (
            <div className={styles.chips} role="radiogroup" aria-label="Тип графика">
              {chart.options.map((option) => (
                <button
                  key={option.type}
                  type="button"
                  role="radio"
                  aria-checked={option.active}
                  title={option.reasons.length > 0 ? option.reasons.join(', ') : undefined}
                  className={styles.chip}
                  onClick={() => !option.active && onChart(option.type)}
                >
                  {option.label}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {visual === 'chart' && !chart && canResearch && (
        <div className={styles.field}>
          <span>Для графика нужны числа</span>
          <Button variant="secondary" size="sm" pending={researching} disabled={researching} onClick={handleResearch}>
            Найти цифры
          </Button>
        </div>
      )}

      <div className={styles.inline}>
        {actions.map((action) => (
          <Button key={action} variant="secondary" size="sm" onClick={() => onAction(action as QuickSlideAction)}>
            {ACTION_LABEL[action as QuickSlideAction]}
          </Button>
        ))}
        {canResearch && !(visual === 'chart' && !chart) && (
          <Button variant="secondary" size="sm" disabled={researching} onClick={handleResearch}>
            {researching ? 'Ищу…' : 'Поискать в материалах'}
          </Button>
        )}
      </div>

      <div className={styles.inline}>
        <Button variant="ghost" size="sm" disabled={first} onClick={() => onMove(-1)}>
          Вверх
        </Button>
        <Button variant="ghost" size="sm" disabled={last} onClick={() => onMove(1)}>
          Вниз
        </Button>
        <Button variant="ghost" size="sm" onClick={() => onAction('remove')}>
          Убрать слайд
        </Button>
      </div>
    </div>
  )
}

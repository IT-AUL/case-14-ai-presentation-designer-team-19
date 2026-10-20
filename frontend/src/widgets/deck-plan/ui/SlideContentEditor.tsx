import { clsx } from 'clsx'
import { useLayoutEffect, useRef } from 'react'
import { hasChartData, isEditableUnit, isUnitLocked, unitOrigin, unitSourceId, type ResearchSource, type SlidePlan, type UnitOrigin } from '@/entities/generation'
import { ArrowDown, ArrowUp, ExternalLink, Icon, Lock, Plus, X } from '@/shared/ui'
import { EDITOR_VISUALS, ORIGIN_LABEL, VISUAL_LABEL } from '../lib/plan'
import type { PlanEditor } from './DeckPlanView'
import styles from './DeckPlanView.module.css'

interface SlideContentEditorProps {
  slide: SlidePlan
  number: string
  editor: PlanEditor
  sources: ReadonlyMap<string, ResearchSource>
}

function OriginBadge({ origin, source }: { origin: UnitOrigin; source: ResearchSource | null }) {
  const label = ORIGIN_LABEL[origin]
  if (origin === 'web' && source?.url) {
    return (
      <a className={styles.origin} data-origin={origin} href={source.url} target="_blank" rel="noreferrer noopener" title={source.title}>
        {source.site ?? label}
        <Icon as={ExternalLink} size={10} />
      </a>
    )
  }
  return (
    <span className={styles.origin} data-origin={origin}>
      {label}
    </span>
  )
}

function UnitField({ value, label, onChange }: { value: string; label: string; onChange: (value: string) => void }) {
  const field = useRef<HTMLTextAreaElement>(null)

  useLayoutEffect(() => {
    const element = field.current
    if (!element) return
    element.style.height = 'auto'
    element.style.height = `${element.scrollHeight}px`
  }, [value])

  return <textarea ref={field} rows={1} className={styles.unitInput} value={value} placeholder="Пункт" aria-label={label} onChange={(event) => onChange(event.target.value)} />
}

export function SlideContentEditor({ slide, number, editor, sources }: SlideContentEditorProps) {
  const editable = slide.content_units.map((unit, position) => ({ unit, position })).filter(({ unit }) => isEditableUnit(unit))
  const assets = slide.content_units.filter((unit) => !isEditableUnit(unit) && unit.kind !== 'title')
  const visual = slide.desired_visual ?? 'none'
  const missingChartData = visual === 'chart' && !hasChartData(slide)

  return (
    <div className={styles.content}>
      {editable.length > 0 && (
        <ol className={styles.units} aria-label={`Пункты слайда ${number}`}>
          {editable.map(({ unit, position }, order) => {
            const origin = unitOrigin(unit)
            const sourceId = unitSourceId(unit)
            return (
              <li key={position} className={styles.unit}>
                <UnitField value={unit.text ?? ''} label={`Пункт ${order + 1} слайда ${number}`} onChange={(text) => editor.onUnitChange(slide.id, position, text)} />
                {isUnitLocked(unit) && (
                  <span className={styles.locked} title="Ваша формулировка: вёрстка её только сократит">
                    <Icon as={Lock} size={11} />
                  </span>
                )}
                {origin && <OriginBadge origin={origin} source={sourceId ? (sources.get(sourceId) ?? null) : null} />}
                <span className={styles.unitActions}>
                  <button type="button" className={clsx(styles.iconButton, styles.live)} aria-label={`Поднять пункт ${order + 1}`} disabled={order === 0} onClick={() => editor.onUnitMove(slide.id, position, -1)}>
                    <Icon as={ArrowUp} size={12} />
                  </button>
                  <button
                    type="button"
                    className={clsx(styles.iconButton, styles.live)}
                    aria-label={`Опустить пункт ${order + 1}`}
                    disabled={order === editable.length - 1}
                    onClick={() => editor.onUnitMove(slide.id, position, 1)}
                  >
                    <Icon as={ArrowDown} size={12} />
                  </button>
                  <button type="button" className={clsx(styles.iconButton, styles.live, styles.remove)} aria-label={`Удалить пункт ${order + 1}`} onClick={() => editor.onUnitRemove(slide.id, position)}>
                    <Icon as={X} size={12} />
                  </button>
                </span>
              </li>
            )
          })}
        </ol>
      )}
      {assets.length > 0 && (
        <div className={styles.assets}>
          {assets.map((unit, index) => (
            <span key={`${unit.kind}-${index}`} className={styles.asset}>
              {VISUAL_LABEL[unit.kind] ?? unit.kind}
            </span>
          ))}
        </div>
      )}
      <div className={styles.contentFooter}>
        <button type="button" className={clsx(styles.addUnit, styles.live)} onClick={() => editor.onUnitAdd(slide.id)}>
          <Icon as={Plus} size={12} /> Пункт
        </button>
        <div className={styles.visuals} role="group" aria-label={`Визуал слайда ${number}`}>
          {EDITOR_VISUALS.map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={visual === option}
              className={styles.visualChip}
              onClick={() => {
                if (option !== visual) editor.onUpdate(slide.id, { desired_visual: option })
              }}
            >
              {VISUAL_LABEL[option]}
            </button>
          ))}
        </div>
      </div>
      {missingChartData && (
        <p className={styles.chartHint} role="note">
          Нет данных для графика: добавьте цифры в пункты
          {editor.onResearch && (
            <>
              ,{' '}
              <button type="button" className={styles.hintAction} disabled={editor.researchingSlideId === slide.id} onClick={() => editor.onResearch?.(slide.id)}>
                найдите цифры
              </button>
            </>
          )}{' '}
          или выберите{' '}
          <button type="button" className={styles.hintAction} onClick={() => editor.onUpdate(slide.id, { desired_visual: 'table' })}>
            таблицу
          </button>{' '}
          или{' '}
          <button type="button" className={styles.hintAction} onClick={() => editor.onUpdate(slide.id, { desired_visual: 'diagram' })}>
            схему
          </button>
          .
        </p>
      )}
    </div>
  )
}

import { clsx } from 'clsx'
import type { AnchorView, FontSizeStep, LayoutBlueprint, MasterBranch, NormalizedBox, SpacingSummary } from '@/entities/template'
import { formatNumber, formatPercent } from '@/shared/lib/format'
import { ANCHOR_LABEL, countLabel, formatFraction, formatPt, formatShare, FORMS, isTitlePlaceholder, OF_FORMS, layoutTypeLabel, ofCountLabel, partName, placeholderLabel, textRoleLabel } from '../model/labels'
import styles from './DesignDna.module.css'

const BLUEPRINT_LIMIT = 8
const USAGE_LIMIT = 10

function boxStyle(box: NormalizedBox) {
  return { left: `${box.x * 100}%`, top: `${box.y * 100}%`, width: `${box.w * 100}%`, height: `${box.h * 100}%` }
}

function aspectLabel(ratio: number): string {
  if (Math.abs(ratio - 16 / 9) < 0.01) return '16:9'
  if (Math.abs(ratio - 4 / 3) < 0.01) return '4:3'
  if (Math.abs(ratio - 16 / 10) < 0.01) return '16:10'
  return ratio.toFixed(2).replace('.', ',')
}

export function FontScaleSection({ scale, className }: { scale: FontSizeStep[]; className?: string }) {
  const steps = scale.filter((step) => !step.fractional).map((step) => formatPt(step.pt))
  return (
    <section className={clsx(styles.section, className)} aria-labelledby="dna-scale">
      <div className={styles.sectionHead}>
        <h2 id="dna-scale" className={styles.sectionTitle}>
          Шкала кеглей
        </h2>
        <div className={styles.sectionMeta}>pt · частота · роль</div>
      </div>
      <ul className={styles.bars}>
        {scale.map((step) => (
          <li key={step.pt} className={clsx(styles.scaleRow, step.fractional && styles.scaleMuted)}>
            <span className={styles.mono}>{formatPt(step.pt)}</span>
            <div className={styles.scaleBar}>
              <div className={styles.scaleFill} style={{ width: `${Math.max(3, step.weight * 100)}%` }} />
            </div>
            <span className={styles.barLabel}>{step.roles.length ? step.roles.map(textRoleLabel).join(', ') : step.fractional ? 'дробный кегль' : '—'}</span>
            <span className={clsx(styles.mono, styles.scaleCount)}>{formatNumber(step.count)}</span>
          </li>
        ))}
      </ul>
      {steps.length > 0 && (
        <div className={clsx(styles.note, styles.divided)}>
          <span>
            Ступени шкалы: <span className={styles.mono}>{steps.join(' · ')}</span>
            {steps.length < scale.length ? '. Дробные значения — вероятные следы автоподбора текста.' : ''}
          </span>
        </div>
      )}
    </section>
  )
}

interface GridSectionProps {
  spacing: SpacingSummary | null
  anchors: AnchorView[]
  aspectRatio: number
  className?: string
}

export function GridSection({ spacing, anchors, aspectRatio, className }: GridSectionProps) {
  const margin = spacing?.margins[0]
  const inset = margin !== undefined ? { x: margin, y: margin * aspectRatio } : null
  const stats = [
    spacing?.margins.length ? { label: 'Поля', value: spacing.margins.slice(0, 3).map(formatFraction).join(' / ') } : null,
    spacing?.gaps.length ? { label: 'Промежутки', value: spacing.gaps.slice(0, 3).map(formatFraction).join(' / ') } : null,
    spacing && spacing.linesX.length + spacing.linesY.length
      ? { label: 'Линии выравнивания', value: [spacing.linesX.length && `${spacing.linesX.length} вертик.`, spacing.linesY.length && `${spacing.linesY.length} гориз.`].filter(Boolean).join(' · ') }
      : null,
    ...anchors.slice(0, 2).map((anchor) => ({ label: ANCHOR_LABEL[anchor.kind], value: `${formatFraction(anchor.box.x)}; ${formatFraction(anchor.box.y)}` })),
  ].filter((stat): stat is { label: string; value: string } => stat !== null)

  return (
    <section className={clsx(styles.section, className)} aria-labelledby="dna-grid">
      <div className={styles.sectionHead}>
        <h2 id="dna-grid" className={styles.sectionTitle}>
          {spacing ? 'Сетка, поля и якоря' : 'Якоря'}
        </h2>
        <div className={styles.sectionMeta}>в долях слайда {aspectLabel(aspectRatio)}</div>
      </div>
      <div className={styles.sheet} style={{ aspectRatio }} role="img" aria-label="Схема полей, линий выравнивания и якорей на слайде">
        {inset && inset.x < 0.5 && inset.y < 0.5 && (
          <div className={styles.marginGuide} style={{ left: `${inset.x * 100}%`, right: `${inset.x * 100}%`, top: `${inset.y * 100}%`, bottom: `${inset.y * 100}%` }} />
        )}
        {spacing?.linesX.map((x) => <div key={`x${x}`} className={styles.lineX} style={{ left: `${x * 100}%` }} />)}
        {spacing?.linesY.map((y) => <div key={`y${y}`} className={styles.lineY} style={{ top: `${y * 100}%` }} />)}
        {anchors.map((anchor) => (
          <div key={`${anchor.kind}-${anchor.box.x}-${anchor.box.y}`} className={styles.anchor} style={boxStyle(anchor.box)} title={`${ANCHOR_LABEL[anchor.kind]} · ${formatPercent(anchor.coverage)} слайдов`}>
            {ANCHOR_LABEL[anchor.kind]} · {formatPercent(anchor.coverage)}
          </div>
        ))}
      </div>
      {stats.length > 0 && (
        <div className={styles.stats}>
          {stats.slice(0, 4).map((stat) => (
            <div key={stat.label} className={styles.stat}>
              <span className={styles.statLabel}>{stat.label}</span>
              <span className={styles.mono}>{stat.value}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}

export function MasterTreeSection({ tree, className }: { tree: MasterBranch[]; className?: string }) {
  return (
    <section className={clsx(styles.section, className)} aria-labelledby="dna-tree">
      <h2 id="dna-tree" className={styles.sectionTitle}>
        Мастер → макеты → слайды
      </h2>
      <div className={styles.tree}>
        {tree.map((master, index) => (
          <div key={master.part} className={styles.tree}>
            <div className={styles.treeMaster} title={partName(master.part)}>
              <span>Мастер {index + 1}</span>
              <span className={styles.treeCount}>
                {master.layouts} · {master.slides}
              </span>
            </div>
            <div className={styles.treeLeaf}>
              <span>Макеты со слайдами</span>
              <span className={styles.treeCount}>{master.usedLayouts}</span>
            </div>
            <div className={clsx(styles.treeLeaf, styles.treeLeafMuted)}>
              <span>Макеты без слайдов</span>
              <span className={styles.treeCount}>{master.layouts - master.usedLayouts}</span>
            </div>
          </div>
        ))}
      </div>
      <div className={styles.note}>Цифры у мастера: макетов · слайдов.</div>
    </section>
  )
}

function layoutTitle(blueprint: LayoutBlueprint): string {
  return blueprint.name ?? `Макет ${blueprint.key}`
}

function Blueprint({ blueprint, aspectRatio }: { blueprint: LayoutBlueprint; aspectRatio: number }) {
  return (
    <li className={styles.blueprint}>
      <div className={styles.sheet} style={{ aspectRatio }} role="img" aria-label={`Схема макета ${layoutTitle(blueprint)}`}>
        {blueprint.placeholders.map((placeholder, index) => (
          <div key={index} className={clsx(styles.placeholder, isTitlePlaceholder(placeholder.type) && styles.placeholderTitle)} style={boxStyle(placeholder.box)}>
            {placeholderLabel(placeholder.type)}
          </div>
        ))}
        {!blueprint.placeholders.length && <div className={styles.sheetEmpty}>без плейсхолдеров</div>}
      </div>
      <div className={styles.blueprintName}>
        <span className={styles.blueprintTitle} title={layoutTitle(blueprint)}>
          {layoutTitle(blueprint)}
        </span>
        <span className={styles.blueprintCount}>×{blueprint.slides}</span>
      </div>
      {blueprint.type && <div className={styles.blueprintType}>{layoutTypeLabel(blueprint.type)}</div>}
    </li>
  )
}

interface LayoutsSectionProps {
  blueprints: LayoutBlueprint[]
  totalLayouts: number
  aspectRatio: number
  className?: string
}

export function LayoutsSection({ blueprints, totalLayouts, aspectRatio, className }: LayoutsSectionProps) {
  const drawable = blueprints.filter((blueprint) => blueprint.placeholders.length)
  const used = blueprints.filter((blueprint) => blueprint.slides > 0)
  const usedLabel = totalLayouts ? `${used.length} из ${ofCountLabel(totalLayouts, OF_FORMS.layout)} используются на слайдах` : countLabel(used.length, FORMS.layout)

  if (drawable.length) {
    const shown = blueprints.filter((blueprint) => blueprint.placeholders.length || blueprint.slides > 0).slice(0, BLUEPRINT_LIMIT)
    return (
      <section className={clsx(styles.section, className)} aria-labelledby="dna-layouts">
        <div className={styles.sectionHead}>
          <h2 id="dna-layouts" className={styles.sectionTitle}>
            Макеты
          </h2>
          <div className={styles.sectionMeta}>
            чертежи плейсхолдеров · {shown.length} из {totalLayouts || blueprints.length}
          </div>
        </div>
        <ul className={styles.blueprints}>
          {shown.map((blueprint) => (
            <Blueprint key={blueprint.part ?? blueprint.key} blueprint={blueprint} aspectRatio={aspectRatio} />
          ))}
        </ul>
      </section>
    )
  }

  const top = used.slice(0, USAGE_LIMIT)
  const rest = used.slice(USAGE_LIMIT)
  const restSlides = rest.reduce((sum, blueprint) => sum + blueprint.slides, 0)
  return (
    <section className={clsx(styles.section, className)} aria-labelledby="dna-layouts">
      <div className={styles.sectionHead}>
        <h2 id="dna-layouts" className={styles.sectionTitle}>
          Макеты
        </h2>
        <div className={styles.sectionMeta}>{usedLabel}</div>
      </div>
      <ul className={styles.usageList}>
        {top.map((blueprint, index) => (
          <li key={blueprint.part ?? blueprint.key} className={styles.usageRow}>
            <span className={styles.barLabel} title={layoutTitle(blueprint)}>
              {layoutTitle(blueprint)}
            </span>
            <div className={styles.track}>
              <div className={clsx(styles.fill, index > 0 && styles.fillMuted)} style={{ width: `${Math.max(blueprint.share * 100, 1.5)}%` }} />
            </div>
            <span className={styles.usageRowValue}>
              {countLabel(blueprint.slides, FORMS.slide)} · {formatShare(blueprint.share)}
            </span>
          </li>
        ))}
      </ul>
      {rest.length > 0 && (
        <div className={styles.note}>
          Ещё {countLabel(rest.length, FORMS.layout)} — {countLabel(restSlides, FORMS.slide)} вместе.
        </div>
      )}
    </section>
  )
}

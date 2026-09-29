import { clsx } from 'clsx'
import type { ReactNode } from 'react'
import type { DesignSystem, DnaGroup, Understanding, UnderstandingConflict, UnderstandingGap } from '@/entities/template'
import { formatNumber, formatPercent, pluralize } from '@/shared/lib/format'
import { countLabel, formatShare, FORMS, GROUP_LABEL, OF_FORMS, ofCountLabel } from '../model/labels'
import type { DnaFacts } from '../model/facts'
import styles from './DesignDna.module.css'
import { ArrowRightLeft, Check, Icon } from '@/shared/ui'

interface UnderstandingPanelProps {
  system: DesignSystem
  understanding: Understanding
  facts: DnaFacts
}

function coverageValue(group: DnaGroup, system: DesignSystem, facts: DnaFacts): string {
  switch (group) {
    case 'palette':
      return [system.palette.length && countLabel(system.palette.length, FORMS.slot), system.usedColors.length && countLabel(system.usedColors.length, FORMS.color)]
        .filter(Boolean)
        .join(' · ')
    case 'fonts':
      return [system.declaredFonts.length && `${system.declaredFonts.length} в теме`, system.observedFonts.length && `${system.observedFonts.length} на слайдах`]
        .filter(Boolean)
        .join(' · ')
    case 'sizes':
      return countLabel(facts.scale.length, FORMS.size)
    case 'spacing': {
      const lines = (facts.spacing?.linesX.length ?? 0) + (facts.spacing?.linesY.length ?? 0)
      return [facts.spacing?.margins.length && countLabel(facts.spacing.margins.length, FORMS.margin), lines && countLabel(lines, FORMS.line)].filter(Boolean).join(' · ')
    }
    case 'anchors':
      return countLabel(facts.anchors.length, FORMS.anchor)
    case 'layouts':
      return system.counts.layouts ? `${system.layoutUsage.length} из ${system.counts.layouts} в работе` : countLabel(system.layoutUsage.length, FORMS.layout)
    case 'roles':
      return countLabel(facts.roles.length, FORMS.role)
  }
}

function Strong({ children }: { children: ReactNode }) {
  return <b>{children}</b>
}

function joinNames(names: string[]): ReactNode {
  return names.map((name, index) => (
    <span key={name}>
      {index > 0 && (index === names.length - 1 ? ' и ' : ', ')}
      <Strong>{name}</Strong>
    </span>
  ))
}

function ConflictCard({ conflict }: { conflict: UnderstandingConflict }) {
  if (conflict.kind === 'font') {
    return (
      <div className={styles.conflict}>
        <div className={styles.conflictTitle}>
          <Icon as={ArrowRightLeft} size={13} /> Шрифт
        </div>
        <div>
          Заявлен {joinNames(conflict.declared)}, на слайдах{' '}
          {conflict.observed.map((font, index) => (
            <span key={font.name}>
              {index > 0 && ' и '}
              <Strong>{font.name}</Strong> ({formatShare(font.share)})
            </span>
          ))}
          .
        </div>
      </div>
    )
  }
  return (
    <div className={styles.conflict}>
      <div className={styles.conflictTitle}>
        <Icon as={ArrowRightLeft} size={13} /> Цвет
      </div>
      <div>
        <Strong>{conflict.color.hex}</Strong> — {formatPercent(conflict.color.share)} употреблений, но в теме такого цвета нет.
      </div>
      <div className={styles.note}>Всего вне палитры темы — {formatShare(conflict.offShare)} цветовых употреблений.</div>
    </div>
  )
}

function gapText(gap: UnderstandingGap): ReactNode {
  switch (gap.kind) {
    case 'dominant_layout':
      return (
        <>
          {formatNumber(gap.slides)} из {ofCountLabel(gap.total, OF_FORMS.slide)} собраны на одном макете <Strong>{gap.layout}</Strong>.
        </>
      )
    case 'unused_layouts':
      return `${formatNumber(gap.unused)} из ${ofCountLabel(gap.total, OF_FORMS.layout)} не используются ни на одном слайде.`
    case 'missing':
      return null
    case 'warning':
      return gap.text
  }
}

export function UnderstandingPanel({ system, understanding, facts }: UnderstandingPanelProps) {
  const gaps = understanding.gaps.filter((gap) => gap.kind !== 'missing')
  const headline = system.counts.slides || system.counts.layouts
  const headlineForms = system.counts.slides ? FORMS.slide : FORMS.layout
  const rest = [
    system.counts.masters && countLabel(system.counts.masters, FORMS.master),
    system.counts.slides && system.counts.layouts && countLabel(system.counts.layouts, FORMS.layout),
  ].filter(Boolean)

  return (
    <section aria-label="Что система поняла" className={styles.understanding}>
      <div className={styles.column}>
        <div className={styles.eyebrow}>Что система поняла</div>
        <div className={clsx(styles.bigNumber, styles.mono)}>{formatNumber(headline)}</div>
        <div className={styles.bigCaption}>
          {pluralize(headline, headlineForms)}
          {rest.length ? `, ${rest.join(' и ')}` : ''} разобраны без подготовки шаблона
        </div>
        <div className={styles.footnote}>Правила взяты из мастера, макетов и обычных слайдов.</div>
      </div>

      <div className={styles.column}>
        <div className={styles.subTitle}>Что извлечено</div>
        <ul className={styles.coverage}>
          {understanding.coverage
            .filter(({ available }) => available)
            .map(({ group }) => (
              <li key={group} className={styles.coverageRow}>
                <span className={styles.coverageMark} aria-hidden>
                  <Icon as={Check} size={10} strokeWidth={3} />
                </span>
                <span className={styles.coverageLabel}>{GROUP_LABEL[group]}</span>
                <span className={styles.coverageValue}>{coverageValue(group, system, facts)}</span>
              </li>
            ))}
        </ul>
      </div>

      <div className={styles.column}>
        <div className={styles.countHead}>
          <div className={styles.subTitle}>Конфликты</div>
          <div className={clsx(styles.count, understanding.conflicts.length ? styles.countWarn : styles.countNeutral)}>{understanding.conflicts.length}</div>
        </div>
        {understanding.conflicts.length ? (
          understanding.conflicts.map((conflict) => <ConflictCard key={conflict.kind} conflict={conflict} />)
        ) : (
          <div className={styles.calm}>Заявленные правила совпадают с тем, что на слайдах.</div>
        )}
      </div>

      <div className={styles.column}>
        <div className={styles.countHead}>
          <div className={styles.subTitle}>Особенности</div>
          <div className={clsx(styles.count, styles.countNeutral)}>{gaps.length}</div>
        </div>
        {gaps.length ? (
          gaps.map((gap, index) => (
            <div key={`${gap.kind}-${index}`} className={styles.gap}>
              {gapText(gap)}
            </div>
          ))
        ) : (
          <div className={styles.calm}>Шаблон используется равномерно.</div>
        )}
      </div>
    </section>
  )
}

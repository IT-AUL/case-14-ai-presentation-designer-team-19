import { useMemo } from 'react'
import type { DesignDna, TemplateDetail } from '@/entities/template'
import { pluralize } from '@/shared/lib/format'
import { buildDnaView } from '@/widgets/design-dna'
import { cleanLayoutName, colorName } from '../lib/passport'
import styles from './TemplateRules.module.css'

const ROLE_NAME: Record<string, string> = {
  title: 'обложки',
  section: 'разделы',
  content: 'рабочие слайды',
  closing: 'финал',
  divider: 'разделители',
  toc: 'Оглавление',
  contact: 'Контакты',
}

function percent(share: number): string {
  return `${Math.round(share * 100)}%`
}

export function TemplateRules({ detail, dna }: { detail: TemplateDetail; dna: DesignDna | undefined }) {
  const { system, facts, understanding } = useMemo(() => buildDnaView(detail, dna), [detail, dna])
  const { counts } = system
  const observed = system.observedFonts.slice(0, 3)
  const topLayout = system.layoutUsage[0]
  const topBlueprint = topLayout ? facts.blueprints.find((item) => item.key === topLayout.layout) : undefined
  const topLayoutName = topLayout ? cleanLayoutName(topBlueprint?.name || topLayout.layout) : ''
  const roles = facts.roles.filter((role) => role.slides > 0 && role.role in ROLE_NAME)
  const palette = useMemo(() => {
    const seen = new Set<string>()
    return system.palette.flatMap((slot) => {
      const name = colorName(slot.hex)
      if (seen.has(name)) return []
      seen.add(name)
      return [{ ...slot, name }]
    })
  }, [system.palette])
  const textSizes = useMemo(() => facts.scale.filter((step) => step.pt <= 96).slice(0, 6), [facts.scale])
  const notes = [
    ...understanding.conflicts.map((conflict) =>
      conflict.kind === 'font'
        ? `В теме заявлены ${conflict.declared.join(', ')}, а на слайдах набрано ${conflict.observed.map((font) => font.name).join(', ')}.`
        : `Цвет «${colorName(conflict.color.hex)}» встречается на слайдах, но его нет в палитре темы.`,
    ),
    ...understanding.gaps.flatMap((gap) => {
      if (gap.kind === 'unused_layouts') return [`${gap.unused} из ${gap.total} макетов не используются ни на одном слайде.`]
      if (gap.kind === 'dominant_layout') return [`Почти все слайды (${gap.slides} из ${gap.total}) собраны на одном макете.`]
      if (gap.kind === 'warning') return [gap.text]
      return []
    }),
  ]

  return (
    <div className={styles.rules}>
      <p className={styles.lead}>
        {system.fileName}: {system.aspectLabel}, {counts.slides} {pluralize(counts.slides, ['слайд', 'слайда', 'слайдов'])},{' '}
        {counts.layouts} {pluralize(counts.layouts, ['макет', 'макета', 'макетов'])}.
      </p>

      <section className={styles.section}>
        <h3 className={styles.heading}>Цвета</h3>
        <ul className={styles.swatches} aria-label="Палитра темы">
          {palette.map((slot) => (
            <li key={`${slot.slot}-${slot.hex}`} className={styles.swatchItem}>
              <span className={styles.swatch} style={{ background: slot.hex }} role="img" aria-label={slot.name} />
              <span className={styles.hex}>{slot.name}</span>
            </li>
          ))}
        </ul>
      </section>

      <section className={styles.section}>
        <h3 className={styles.heading}>Шрифты</h3>
        <p className={styles.text}>
          {observed.length > 0
            ? observed.map((font) => `${font.name} (${percent(font.share)})`).join(', ')
            : system.declaredFonts.join(', ') || 'Не нашёл шрифтов в шаблоне.'}
        </p>
        {textSizes.length > 0 && (
          <p className={styles.muted}>
            Размеры текста: {textSizes.map((step) => `${step.pt}`).join(', ')} пт
          </p>
        )}
      </section>

      {(roles.length > 0 || (topLayout && topLayoutName)) && (
        <section className={styles.section}>
          <h3 className={styles.heading}>Раскладки</h3>
          {roles.length > 0 && <p className={styles.text}>{roles.map((role) => `${ROLE_NAME[role.role] ?? role.role} — ${role.slides}`).join(', ')}</p>}
          {topLayout && topLayoutName && (
            <p className={styles.muted}>
              Чаще всего — «{topLayoutName}», {topLayout.slides} {pluralize(topLayout.slides, ['слайд', 'слайда', 'слайдов'])}.
            </p>
          )}
        </section>
      )}

      {notes.length > 0 && (
        <section className={styles.section}>
          <h3 className={styles.heading}>Что я учту</h3>
          <ul className={styles.notes}>
            {notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}

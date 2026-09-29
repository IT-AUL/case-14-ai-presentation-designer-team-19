import { clsx } from 'clsx'
import { isLightColor, type DesignSystem, type UsedColor } from '@/entities/template'
import { countLabel, FORMS, formatShare } from '../model/labels'
import styles from './DesignDna.module.css'

const LEGEND_LIMIT = 12

function matchTag(color: UsedColor): { text: string; className?: string } {
  if (color.match.kind === 'theme') return { text: color.match.slot ?? 'тема' }
  if (color.match.kind === 'near') return { text: `≈ ${color.match.slot}`, className: styles.tagNear }
  return { text: 'вне темы', className: styles.tagOff }
}

export function PaletteSection({ system, className }: { system: DesignSystem; className?: string }) {
  const { palette, paletteName, usedColors } = system
  const legend = usedColors.slice(0, LEGEND_LIMIT)
  const meta = [
    palette.length && `${countLabel(palette.length, FORMS.slot)} темы${paletteName ? ` «${paletteName}»` : ''}`,
    usedColors.length && `${countLabel(usedColors.length, FORMS.color)} реально используется`,
  ]
    .filter(Boolean)
    .join(' · ')

  return (
    <section className={clsx(styles.section, className)} aria-labelledby="dna-palette">
      <div className={styles.sectionHead}>
        <h2 id="dna-palette" className={styles.sectionTitle}>
          Палитра
        </h2>
        <div className={styles.sectionMeta}>{meta}</div>
      </div>

      {palette.length > 0 && (
        <ul className={styles.swatches} aria-label="Цвета темы">
          {palette.map((entry) => (
            <li key={entry.slot} className={styles.swatch}>
              <div className={clsx(styles.swatchColor, isLightColor(entry.hex) && styles.swatchColorLight)} style={{ background: entry.hex }} />
              <div className={styles.swatchText}>
                <span className={styles.swatchSlot}>{entry.slot}</span>
                <span className={styles.mono}>{entry.hex}</span>
              </div>
            </li>
          ))}
        </ul>
      )}

      {usedColors.length > 0 && (
        <div className={palette.length ? styles.divided : styles.column}>
          <div className={styles.subTitle}>Фактически на слайдах</div>
          <div className={styles.usageBar} role="img" aria-label="Доли цветов на слайдах">
            {usedColors.map((color) => (
              <div key={color.hex} style={{ width: `${color.share * 100}%`, background: color.hex }} />
            ))}
          </div>
          <ul className={styles.usageLegend}>
            {legend.map((color) => {
              const tag = matchTag(color)
              return (
                <li key={color.hex} className={styles.usageItem}>
                  <span className={styles.usageChip} style={{ background: color.hex }} aria-hidden />
                  <span className={styles.mono}>{color.hex}</span>
                  <span className={styles.usageShare}>{formatShare(color.share)}</span>
                  <span className={clsx(styles.usageTag, tag.className)}>{tag.text}</span>
                </li>
              )
            })}
          </ul>
          {usedColors.length > legend.length && (
            <div className={styles.note}>Ещё {countLabel(usedColors.length - legend.length, FORMS.color)} с малой долей.</div>
          )}
        </div>
      )}
    </section>
  )
}

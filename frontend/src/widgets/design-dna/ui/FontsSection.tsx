import { clsx } from 'clsx'
import type { DesignSystem } from '@/entities/template'
import { formatNumber } from '@/shared/lib/format'
import { countLabel, FORMS, formatShare } from '../model/labels'
import styles from './DesignDna.module.css'
import { ArrowRightLeft, Icon } from '@/shared/ui'

const BAR_LIMIT = 5

function sampleFont(name: string) {
  return { fontFamily: `"${name.replace(/"/g, '')}", sans-serif` }
}

export function FontsSection({ system, className }: { system: DesignSystem; className?: string }) {
  const { declaredFonts, observedFonts, primaryFont, fontConflict } = system
  const fragments = observedFonts.reduce((sum, font) => sum + font.count, 0)
  const declared = declaredFonts[0]
  const declaredUsage = declared ? observedFonts.find((font) => font.name === declared) : undefined

  return (
    <section className={clsx(styles.section, className)} aria-labelledby="dna-fonts">
      <div className={styles.sectionHead}>
        <h2 id="dna-fonts" className={styles.sectionTitle}>
          Шрифты
        </h2>
        {fontConflict && <span className={styles.warnPill}>
            <Icon as={ArrowRightLeft} size={12} /> заявлено ≠ фактически
          </span>}
      </div>

      <div className={clsx(styles.fontCompare, !(declared && primaryFont && fragments > 0) && styles.fontCompareSingle)}>
        {declared && (
          <div className={styles.fontCard}>
            <div className={styles.fontCardLabel}>Заявлено в теме</div>
            <div className={styles.fontSample} style={sampleFont(declared)}>
              Аа Бб
            </div>
            <div className={styles.fontCaption}>{declaredFonts.join(', ')} · шрифт темы</div>
          </div>
        )}
        {declared && primaryFont && fragments > 0 && (
          <div className={clsx(styles.fontSign, fontConflict && styles.fontSignConflict)} aria-label={fontConflict ? 'не совпадает' : 'совпадает'}>
            {fontConflict ? '≠' : '='}
          </div>
        )}
        {primaryFont && fragments > 0 && (
          <div className={clsx(styles.fontCard, fontConflict && styles.fontCardConflict)}>
            <div className={styles.fontCardLabel}>Фактически · {countLabel(fragments, FORMS.fragment)}</div>
            <div className={styles.fontSample} style={sampleFont(primaryFont)}>
              Аа Бб
            </div>
            <div className={styles.fontCaption}>{primaryFont} · основной</div>
          </div>
        )}
      </div>

      {observedFonts.length > 0 && (
        <ul className={styles.bars} aria-label="Шрифты на слайдах">
          {observedFonts.slice(0, BAR_LIMIT).map((font, index) => (
            <li key={font.name} className={styles.barRow}>
              <span className={styles.barLabel} style={sampleFont(font.name)} title={font.name}>
                {font.name}
              </span>
              <div className={styles.track}>
                <div className={clsx(styles.fill, index > 0 && styles.fillMuted)} style={{ width: `${Math.max(font.share * 100, 1)}%` }} />
              </div>
              <span className={styles.barValue}>{formatShare(font.share)}</span>
            </li>
          ))}
        </ul>
      )}

      {fontConflict && declared && (
        <div className={styles.note}>
          {declaredUsage
            ? `На ${declared} приходится ${countLabel(declaredUsage.count, FORMS.fragment)} текста из ${formatNumber(fragments)}.`
            : `${declared} не встречается в тексте слайдов.`}
        </div>
      )}
    </section>
  )
}

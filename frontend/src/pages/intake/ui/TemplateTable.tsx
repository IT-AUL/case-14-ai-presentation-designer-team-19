import { useState, type ReactNode } from 'react'
import type { BrandbookSummary, BrandPriority, TemplateSummary } from '@/entities/assistant'
import type { DesignSystem } from '@/entities/template'
import { artifactUrl } from '@/shared/api'
import { formatPercent, pluralize } from '@/shared/lib/format'
import { Button, PassportSkeleton, SlideTile } from '@/shared/ui'
import { colorName, normalizeHex, passportBrandDetails, passportColors, passportConflicts, passportFonts, passportRoles } from '../lib/passport'
import type { PassportConflict } from '../lib/passport'
import styles from './IntakePage.module.css'

interface TemplateTableProps {
  summary: TemplateSummary | null
  system: DesignSystem | null
  previews: string[]
  aspectRatio: number
  analyzing: boolean
  brandbook: BrandbookSummary | null
  priority: BrandPriority
  priorityControl: ReactNode
  onOpenDetails?: () => void
  onDownload?: () => void
  onAnalyzeBrandbook?: () => void
  analyzingBrandbook?: boolean
}

function Swatch({ hex, label }: { hex: string; label: string }) {
  return <span className={styles.swatch} style={{ background: hex }} role="img" aria-label={label} />
}

function Previews({ ids, aspectRatio }: { ids: string[]; aspectRatio: number }) {
  const [failed, setFailed] = useState<Set<string>>(new Set())
  const shown = ids.filter((id) => !failed.has(id))
  if (shown.length === 0) return null
  return (
    <ul className={styles.previews} aria-label="Слайды шаблона">
      {shown.map((id) => (
        <li key={id} className={styles.previewItem} style={{ aspectRatio }}>
          <img
            className={styles.previewImage}
            src={artifactUrl(id)}
            alt="Слайд шаблона"
            loading="lazy"
            onError={() => setFailed((current) => new Set(current).add(id))}
          />
        </li>
      ))}
    </ul>
  )
}

function ConflictValue({ conflict, side }: { conflict: PassportConflict; side: 'template' | 'brandbook' }) {
  const value = conflict[side]
  const hex = conflict.kind === 'color' ? normalizeHex(value) : null
  const chosen = conflict.chosen === side
  return (
    <span className={styles.conflictSide} data-chosen={chosen || undefined}>
      <span className={styles.conflictSideLabel}>{side === 'template' ? 'Шаблон' : 'Брендбук'}</span>
      <span className={styles.conflictValue}>
        {hex ? (
          <>
            <Swatch hex={hex} label={colorName(hex)} />
            {colorName(hex)}
          </>
        ) : (
          value
        )}
      </span>
      {chosen && <span className={styles.conflictChosen}>беру это</span>}
    </span>
  )
}

export function TemplateTable({
  summary,
  system,
  previews,
  aspectRatio,
  analyzing,
  brandbook,
  priority,
  priorityControl,
  onOpenDetails,
  onDownload,
  onAnalyzeBrandbook,
  analyzingBrandbook,
}: TemplateTableProps) {
  if (!summary && !brandbook) {
    if (analyzing && !brandbook) {
      return (
        <div className={styles.tableEmpty}>
          <PassportSkeleton label="Читаю шаблон" />
        </div>
      )
    }
    return (
      <div className={styles.tableEmpty}>
        <SlideTile pending={false} fill>
          <span className={styles.ghost}>Здесь появится паспорт вашего шаблона</span>
        </SlideTile>
      </div>
    )
  }

  const colors = summary ? passportColors(summary, system) : []
  const fonts = summary ? passportFonts(summary, system) : { declared: [], observed: [], conflict: false }
  const roles = summary ? passportRoles(summary) : []
  const conflicts = passportConflicts(brandbook, priority)
  const brandDetails = passportBrandDetails(brandbook)
  const divergent = brandbook?.conflicts.length ?? 0
  const slides = summary?.slides ?? system?.counts.slides ?? 0
  const layouts = system?.counts.layouts ?? 0
  const usedLayouts = system?.layoutUsage.length ?? 0
  const facts = [
    slides > 0 && `${slides} ${pluralize(slides, ['слайд', 'слайда', 'слайдов'])}`,
    system && system.aspectLabel !== '—' && system.aspectLabel,
    layouts > 0 && `${layouts} ${pluralize(layouts, ['макет', 'макета', 'макетов'])}`,
  ].filter(Boolean)
  const heading = summary?.heading_font ?? null
  const body = summary?.body_font ?? null

  return (
    <article className={styles.passport} aria-label="Паспорт шаблона">
      {summary && (
        <>
          <header className={styles.passportHead}>
            <div className={styles.passportTitle}>
              <h2 className={styles.passportName}>{summary.filename ?? 'Шаблон'}</h2>
              {facts.length > 0 && <p className={styles.passportFacts}>{facts.join(' · ')}</p>}
            </div>
            <div className={styles.passportActions}>
              {onDownload && (
                <Button variant="secondary" size="sm" onClick={onDownload}>
                  Скачать правила (JSON)
                </Button>
              )}
              {onOpenDetails && (
                <Button variant="ghost" size="sm" onClick={onOpenDetails}>
                  Все правила шаблона
                </Button>
              )}
            </div>
          </header>

          <Previews ids={previews} aspectRatio={aspectRatio} />

          <div className={styles.passportGrid}>
            {colors.length > 0 && (
              <section className={styles.block} aria-labelledby="passport-colors">
                <h3 id="passport-colors" className={styles.blockTitle}>
                  Палитра
                </h3>
                <ul className={styles.palette}>
                  {colors.map((color) => (
                    <li key={color.hex} className={styles.paletteItem}>
                      <span className={styles.paletteChip} style={{ background: color.hex }} role="img" aria-label={color.name} />
                      {color.share !== null && <span className={styles.paletteShare}>{formatPercent(color.share)}</span>}
                    </li>
                  ))}
                </ul>
                <p className={styles.blockNote}>{colors.some((color) => color.share !== null) ? 'Доли — по тому, как цвета встречаются на слайдах.' : 'Цвета темы шаблона.'}</p>
              </section>
            )}

            {(fonts.declared.length > 0 || fonts.observed.length > 0 || heading || body) && (
              <section className={styles.block} aria-labelledby="passport-fonts">
                <h3 id="passport-fonts" className={styles.blockTitle}>
                  Шрифты
                </h3>
                <dl className={styles.fontList}>
                  <div className={styles.fontRow}>
                    <dt>Заявлено в теме</dt>
                    <dd>{fonts.declared.length > 0 ? fonts.declared.join(', ') : [heading, body].filter(Boolean).join(', ')}</dd>
                  </div>
                  {fonts.observed.length > 0 && (
                    <div className={styles.fontRow}>
                      <dt>На слайдах</dt>
                      <dd>{fonts.observed.map((font) => `${font.name} ${formatPercent(font.share)}`).join(', ')}</dd>
                    </div>
                  )}
                </dl>
                {fonts.conflict && <p className={styles.blockWarn}>Заявленный шрифт и набранный на слайдах не совпадают.</p>}
              </section>
            )}
          </div>

          {roles.length > 0 && (
            <section className={styles.block} aria-labelledby="passport-roles">
              <h3 id="passport-roles" className={styles.blockTitle}>
                Макеты по ролям
              </h3>
              <ul className={styles.roles}>
                {roles.map((role) => (
                  <li key={role.role} className={styles.role}>
                    <span className={styles.roleCount}>{role.slides}</span>
                    <span className={styles.roleLabel}>{role.label}</span>
                  </li>
                ))}
              </ul>
              {layouts > 0 && usedLayouts > 0 && (
                <p className={styles.blockNote}>
                  В файле {layouts} {pluralize(layouts, ['макет', 'макета', 'макетов'])}, на слайдах используется {usedLayouts}.
                </p>
              )}
            </section>
          )}
        </>
      )}

      {brandbook && (
        <section className={styles.block} aria-labelledby="passport-brand">
          <div className={styles.brandHead}>
            <h3 id="passport-brand" className={styles.blockTitle}>
              Брендбук {brandbook.name ? `«${brandbook.name}»` : ''}
            </h3>
            {onAnalyzeBrandbook && (
              <Button variant="secondary" size="sm" disabled={analyzingBrandbook} onClick={onAnalyzeBrandbook}>
                Разобрать брендбук
              </Button>
            )}
          </div>
          {summary && (
            <p className={styles.brandText}>
              {divergent > 0
                ? `Расходится с шаблоном в ${divergent} ${pluralize(divergent, ['месте', 'местах', 'местах'])}. Что важнее?`
                : 'Совпадает с шаблоном.'}
            </p>
          )}
          {brandDetails.colors.length > 0 && (
            <div className={styles.brandGroup}>
              <span className={styles.brandGroupTitle}>Цвета брендбука</span>
              <ul className={styles.brandReadings} aria-label="Цвета брендбука">
                {brandDetails.colors.map((item, idx) => (
                  <li key={`${item.hex}-${idx}`} className={styles.brandReading}>
                    <span className={styles.brandReadingMain}>
                      <Swatch hex={item.hex} label={item.name} />
                      <span className={styles.brandReadingName}>{item.name}</span>
                      {item.role && <span className={styles.brandReadingRole}>{item.role}</span>}
                    </span>
                    {(item.quote || item.page) && (
                      <span className={styles.brandReadingMeta}>
                        {item.quote && <span className={styles.brandQuote}>«{item.quote}»</span>}
                        {item.page && <span className={styles.brandPage}>{item.page}</span>}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {brandDetails.fonts.length > 0 && (
            <div className={styles.brandGroup}>
              <span className={styles.brandGroupTitle}>Шрифты брендбука</span>
              <ul className={styles.brandReadings} aria-label="Шрифты брендбука">
                {brandDetails.fonts.map((item, idx) => (
                  <li key={`${item.family}-${item.role ?? idx}`} className={styles.brandReading}>
                    <span className={styles.brandReadingMain}>
                      <span className={styles.brandReadingName}>{item.family}</span>
                      {item.role && <span className={styles.brandReadingRole}>{item.role}</span>}
                    </span>
                    {(item.quote || item.page) && (
                      <span className={styles.brandReadingMeta}>
                        {item.quote && <span className={styles.brandQuote}>«{item.quote}»</span>}
                        {item.page && <span className={styles.brandPage}>{item.page}</span>}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {divergent > 0 && (
            <>
              {priorityControl}
              <ul className={styles.conflicts}>
                {conflicts.map((conflict) => (
                  <li key={conflict.field} className={styles.conflict}>
                    <span className={styles.conflictLabel}>{conflict.label}</span>
                    <ConflictValue conflict={conflict} side="template" />
                    <ConflictValue conflict={conflict} side="brandbook" />
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      )}
    </article>
  )
}

import type { CSSProperties } from 'react'
import { Link } from 'react-router'
import { slidePurposeLabel } from '@/entities/generation'
import { FEATURE_PATHS, useCapabilityFlag } from '@/entities/system'
import { slidePreviewUrl, type SlideInfo, type VariantSummary } from '@/entities/variant'
import { RequestPdfExport } from '@/features/request-pdf-export'
import { formatIndex, pluralize } from '@/shared/lib/format'
import { Mono, PdfPage, Skeleton } from '@/shared/ui'
import { alignRows, type AlignedRow } from '../lib/rows'
import { slideLabel, slideTitle } from '../lib/slides'
import { CollectColumns, type CompareColumn } from './CollectColumns'
import styles from './VariantCompare.module.css'

const SKELETON_ROWS = 4

function sharesPlan(columns: readonly CompareColumn[]): boolean {
  const plans = new Set(columns.map((column) => column.variant.deck_plan_id ?? null))
  return plans.size === 1 && !plans.has(null)
}

function shifted(row: AlignedRow<SlideInfo>): boolean {
  return new Set(row.cells.flatMap((cell) => (cell ? [cell.index] : []))).size > 1
}

interface VariantCompareProps {
  variants: readonly VariantSummary[]
  auditHref: (variantId: string, slideNumber?: number) => string
}

export function VariantCompare({ variants, auditHref }: VariantCompareProps) {
  return <CollectColumns variants={variants}>{(columns) => <CompareTable columns={columns} auditHref={auditHref} />}</CollectColumns>
}

interface CompareTableProps {
  columns: CompareColumn[]
  auditHref: (variantId: string, slideNumber?: number) => string
}

function CompareTable({ columns, auditHref }: CompareTableProps) {
  const rows = alignRows(
    columns.map((column) => column.slides),
    { sharedPlan: sharesPlan(columns) },
  )
  const loading = rows.length === 0 && columns.some((column) => column.isPending)
  const gridStyle = { '--columns': columns.length } as CSSProperties

  const failed = columns.filter((column) => column.error)

  return (
    <div className={styles.root}>
      {failed.length > 0 && (
        <div className={styles.errors} role="alert">
          {failed.map((column) => (
            <p key={column.variant.id}>
              «{column.name}»: {column.error?.message}
            </p>
          ))}
        </div>
      )}

      <section className={styles.table} style={gridStyle} role="table" aria-label="Слайд N во всех вариантах" aria-busy={loading}>
        <div className={styles.headRow} role="row">
          <div className={styles.headCell} role="columnheader">
            Раздел
          </div>
          {columns.map((column) => (
            <div key={column.variant.id} className={styles.headCell} role="columnheader">
              <span className={styles.columnName}>{column.name}</span>
              <Mono className={styles.columnCode}>{column.variant.strategy}</Mono>
              {column.slides !== undefined && column.slides.length > 0 && (
                <span className={styles.columnCount}>
                  {column.slides.length} {pluralize(column.slides.length, ['слайд', 'слайда', 'слайдов'])}
                </span>
              )}
              {!column.isPending && !column.pdfUrl && column.variant.status === 'completed' && (
                <span className={styles.columnAction}>
                  <RequestPdfExport variantId={column.variant.id} variantName={column.name} />
                </span>
              )}
            </div>
          ))}
        </div>

        {loading &&
          Array.from({ length: SKELETON_ROWS }, (_, row) => (
            <div key={row} className={styles.row} aria-hidden>
              <Skeleton className={styles.indexSkeleton} />
              {columns.map((column) => (
                <Skeleton key={column.variant.id} className={styles.cellSkeleton} delay={row * 0.1} />
              ))}
            </div>
          ))}

        {rows.map((row, position) => {
          const showNumbers = shifted(row) || row.cells.some((cell) => cell === null)
          const purpose = slidePurposeLabel(row.purpose)
          return (
            <div key={row.key} className={styles.row} role="row">
              <div className={styles.rowHeader} role="rowheader">
                <span className={styles.index}>{formatIndex(position + 1)}</span>
                {purpose && <span className={styles.purpose}>{purpose}</span>}
              </div>
              {row.cells.map((slide, column) => (
                <CompareCell
                  key={columns[column]?.variant.id ?? column}
                  column={columns[column]}
                  slide={slide}
                  showNumber={showNumbers}
                  auditHref={auditHref}
                />
              ))}
            </div>
          )
        })}
      </section>

      {!loading && rows.length === 0 && <p className={styles.empty}>В вариантах пока нет слайдов.</p>}
    </div>
  )
}

interface CompareCellProps {
  column: CompareColumn | undefined
  slide: SlideInfo | null
  showNumber: boolean
  auditHref: (variantId: string, slideNumber?: number) => string
}

function CompareCell({ column, slide, showNumber, auditHref }: CompareCellProps) {
  const renderPreviews = useCapabilityFlag(FEATURE_PATHS.pngPreviews)
  if (!column) return null
  if (!slide) {
    if (column.isPending) {
      return (
        <div role="cell" aria-busy="true">
          <Skeleton className={styles.cellSkeleton} />
        </div>
      )
    }
    return (
      <div className={styles.missing} role="cell">
        нет слайда
      </div>
    )
  }

  const label = slideLabel(slide, column.name)
  const href = auditHref(column.variant.id, slide.index + 1)

  return (
    <div role="cell">
      <Link className={styles.thumb} to={href} aria-label={`${label}. Открыть в аудите`} title={slideTitle(slide) ?? undefined}>
        {column.pdfUrl || slidePreviewUrl(slide, renderPreviews) ? (
          <PdfPage url={column.pdfUrl} imageUrl={slidePreviewUrl(slide, renderPreviews)} pageNumber={slide.index + 1} label={label} />
        ) : (
          <span className={styles.textThumb}>
            <span className={styles.textThumbTitle}>{slideTitle(slide) ?? `Слайд ${slide.index + 1}`}</span>
            <span className={styles.textThumbNote}>превью появится после PDF</span>
          </span>
        )}
      </Link>
      {showNumber && <span className={styles.slideNumber}>слайд {slide.index + 1}</span>}
    </div>
  )
}

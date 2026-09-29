import { useEffect, useMemo, type ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { reportOpenCounts, useAuditIssues, useVariantAudit } from '@/entities/audit'
import { useGeneration } from '@/entities/generation'
import { usePassport, type ExportFormat, type QualityPassport } from '@/entities/passport'
import { useProject } from '@/entities/project'
import { useCapabilities, useSkillManifest, useVersion } from '@/entities/system'
import { useTemplate } from '@/entities/template'
import { orderVariants, strategyInfo, useVariants, variantMetrics, type DeckFile, type DeckFiles, type VariantSummary } from '@/entities/variant'
import { DownloadButton, FORMAT_LABEL, isStaleFile } from '@/features/export-deck'
import { ReproduceCommand } from '@/features/reproduce-run'
import { useVariantFiles } from '@/features/variant-files'
import { buildPassportView, EditabilityProof, QualityPassport as PassportPanel } from '@/widgets/quality-passport'
import { GENERATION_BUDGET_SECONDS, routes } from '@/shared/config'
import { formatBytes, formatNumber, formatPercent, pluralize } from '@/shared/lib/format'
import { AlertTriangle, Button, Check, ChevronRight, EmptyState, Icon, PageHeader, Skeleton } from '@/shared/ui'
import { currentRevisionOf, exportFileName, htmlExportSupported, openCriticalCounts, openCriticalText, pickVariantId, serverSkillVersion, withPassportMeta } from '../lib/exportPage'
import { DeckPreview } from './DeckPreview'
import styles from './ExportPage.module.css'
import { VariantPicker } from './VariantPicker'

function revisionText(revision: number | null): string | null {
  return revision === null ? null : `r${revision}`
}

function PassportLoading() {
  return (
    <div className={styles.passportLoading} aria-busy="true" aria-label="Загружаем паспорт качества">
      <Skeleton style={{ height: 22, width: 200 }} />
      {[0, 1, 2, 3].map((index) => (
        <Skeleton key={index} style={{ height: 56 }} delay={index * 0.1} />
      ))}
    </div>
  )
}

interface ExportContentProps {
  projectId: string
  runId: string
  variant: VariantSummary
  variants: VariantSummary[]
}

interface Check {
  key: string
  tone: 'ok' | 'warn'
  text: ReactNode
}

function auditSlideLink(path: string, slideNumber?: number): string {
  return slideNumber ? `${path}?${new URLSearchParams({ slide: String(slideNumber) }).toString()}` : path
}

function FileSums({ files }: { files: DeckFiles | null }) {
  const rows = (['pptx', 'pdf', 'html', 'quality_passport'] as const).flatMap((format) => {
    const file = files?.[format]
    return file ? [{ format, file }] : []
  })
  if (rows.length === 0) return null
  return (
    <section className={styles.sums} aria-label="Контрольные суммы файлов">
      <h3 className={styles.detailsTitle}>Файлы и контрольные суммы</h3>
      <dl className={styles.sumList}>
        {rows.map(({ format, file }) => (
          <div key={format} className={styles.sumRow}>
            <dt>{FORMAT_LABEL[format]}</dt>
            <dd>
              {[file.sizeBytes !== null ? formatBytes(file.sizeBytes) : null, file.deckRevision !== null ? `r${file.deckRevision}` : null, file.sha256 ? `sha256 ${file.sha256}` : null]
                .filter(Boolean)
                .join(' · ')}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  )
}

function ExportContent({ projectId, runId, variant, variants }: ExportContentProps) {
  const navigate = useNavigate()
  const variantFiles = useVariantFiles(variant.id)
  const audit = useVariantAudit(variant.id)
  const auditIssues = useAuditIssues(audit.data?.id)
  const generation = useGeneration(runId)
  const project = useProject(projectId)
  const template = useTemplate(generation.data?.template_id)
  const capabilities = useCapabilities()
  const manifest = useSkillManifest()
  const version = useVersion()
  const passportQuery = usePassport(variantFiles.passportArtifactId)

  const files = variantFiles.files
  const passport: QualityPassport | null = passportQuery.data ?? null
  const currentRevision = currentRevisionOf(audit.data?.deck_revision, [files?.pdf ?? null, files?.quality_passport ?? null])
  const passportStale = isStaleFile(files?.quality_passport ?? null, currentRevision)
  const passportRevision = files?.quality_passport?.deckRevision ?? null
  const info = strategyInfo(variant.strategy)
  const htmlSupported = htmlExportSupported(capabilities.data)
  const openCritical = openCriticalCounts(auditIssues.data, audit.data)
  const openCriticalLabel = openCritical ? openCriticalText(openCritical) : null
  const openBlockers = openCritical?.blockers ?? null
  const openErrors = openCritical?.errors ?? null
  const auditPath = routes.audit(projectId, runId, variant.id)

  useEffect(() => {
    if (openBlockers !== null && openErrors !== null) reportOpenCounts(projectId, { blocker: openBlockers, error: openErrors }, runId)
  }, [openBlockers, openErrors, projectId, runId])

  const view = useMemo(
    () =>
      passport
        ? buildPassportView(passport, {
            budgetSeconds: GENERATION_BUDGET_SECONDS,
            planner: variant.planner,
            serverSkillVersion: serverSkillVersion(manifest.data, version.data),
            fallbackPei: variantMetrics(variant).editabilityLevel,
          })
        : null,
    [passport, variant, manifest.data, version.data],
  )

  const pptxFile = withPassportMeta(files?.pptx ?? null, passport, Boolean(passport) && !passportStale)
  const rasterOnly = passport?.editability.rasterOnlySlides ?? null
  const roundTrip = passport ? (passport.validity.roundTripOk ?? passport.validity.opensCleanly) : null
  const fidelity = variantMetrics(variant).styleFidelity
  const slideCount = generation.data?.deck_plan?.brief?.target_slide_count ?? project.data?.target_slide_count ?? null
  const templateFile = template.data?.filename ?? passport?.inputs.templateName ?? null
  const revisionLabel = revisionText(currentRevision)
  const fileName = (format: ExportFormat, file: DeckFile | null) =>
    exportFileName(variant.strategy, format, file?.deckRevision ?? currentRevision, file?.mimeType ?? null)

  const candidates: (Check | false | null)[] = [
    roundTrip !== null && {
      key: 'open',
      tone: roundTrip ? 'ok' : 'warn',
      text: roundTrip ? 'Открывается в PowerPoint без ошибок' : 'При повторном открытии найдены ошибки',
    },
    rasterOnly !== null && {
      key: 'edit',
      tone: rasterOnly === 0 ? 'ok' : 'warn',
      text:
        rasterOnly === 0
          ? 'Текст, таблицы и графики — редактируемые объекты'
          : `${formatNumber(rasterOnly)} ${pluralize(rasterOnly, ['слайд собран', 'слайда собраны', 'слайдов собраны'])} картинкой`,
    },
    fidelity !== null && { key: 'style', tone: 'ok', text: `${formatPercent(fidelity)} в стиле шаблона` },
    openCritical && {
      key: 'audit',
      tone: openCriticalLabel ? 'warn' : 'ok',
      text: (
        <>
          {openCriticalLabel ?? 'Критичных замечаний нет'} ·{' '}
          <Link className={styles.inlineLink} to={auditPath}>
            {openCriticalLabel ? 'исправить в аудите' : 'аудит'}
          </Link>
        </>
      ),
    },
  ]
  const checks = candidates.filter((check): check is Check => Boolean(check))

  return (
    <>
      <PageHeader eyebrow="Шаг 7 · Экспорт" title="Заберите колоду" description={`Вариант «${info.name}»${revisionLabel ? ` · ревизия ${revisionLabel}` : ''}`} />

      <div className={styles.grid}>
        <DeckPreview variant={variant} auditHref={(slideNumber) => auditSlideLink(auditPath, slideNumber)} />

        <aside className={styles.side} aria-label="Скачать колоду">
          {variants.length > 1 && (
            <div className={styles.block}>
              <span className={styles.blockLabel}>Вариант</span>
              <VariantPicker variants={variants} selectedId={variant.id} onSelect={(id) => navigate(routes.export(projectId, runId, id), { replace: true })} />
            </div>
          )}

          {variantFiles.isPending ? (
            <Skeleton style={{ height: 112, borderRadius: 12 }} />
          ) : (
            <div className={styles.downloads}>
              <DownloadButton
                key={`${variant.id}:pptx`}
                projectId={projectId}
                variantId={variant.id}
                format="pptx"
                file={pptxFile}
                currentRevision={currentRevision}
                supported
                fileName={fileName('pptx', pptxFile)}
                label="Скачать PPTX"
                primary
              />
              <div className={styles.secondaryDownloads}>
                <DownloadButton
                  key={`${variant.id}:pdf`}
                  projectId={projectId}
                  variantId={variant.id}
                  format="pdf"
                  file={files?.pdf ?? null}
                  currentRevision={currentRevision}
                  supported
                  fileName={fileName('pdf', files?.pdf ?? null)}
                  label="PDF"
                />
                <DownloadButton
                  key={`${variant.id}:html`}
                  projectId={projectId}
                  variantId={variant.id}
                  format="html"
                  file={files?.html ?? null}
                  currentRevision={currentRevision}
                  supported={htmlSupported}
                  fileName={fileName('html', files?.html ?? null)}
                  label="HTML"
                />
              </div>
            </div>
          )}
          {variantFiles.error && (
            <p className={styles.error} role="alert">
              Не удалось загрузить файлы варианта: {variantFiles.error.message}
            </p>
          )}

          {checks.length > 0 && (
            <ul className={styles.checks} aria-label="Проверки файла">
              {checks.map((check) => (
                <li key={check.key} className={styles.check} data-tone={check.tone}>
                  <Icon as={check.tone === 'ok' ? Check : AlertTriangle} size={16} className={styles.checkIcon} />
                  <span>{check.text}</span>
                </li>
              ))}
            </ul>
          )}
        </aside>
      </div>

      <details className={styles.details}>
        <summary className={styles.summary}>
          <Icon as={ChevronRight} size={16} className={styles.chevron} />
          <span className={styles.summaryTitle}>Паспорт качества</span>
          <span className={styles.summaryHint}>редактируемость, соответствие шаблону, опора на контент, время, версии и контрольные суммы</span>
        </summary>
        <div className={styles.detailsBody}>
          {view && <EditabilityProof cells={view.proof} />}
          {view ? (
            <PassportPanel
              view={view}
              revisionLabel={revisionText(passportRevision)}
              actions={
                <div className={styles.passportDownload}>
                  <DownloadButton
                    key={`${variant.id}:passport`}
                    projectId={projectId}
                    variantId={variant.id}
                    format="quality_passport"
                    file={files?.quality_passport ?? null}
                    currentRevision={currentRevision}
                    supported
                    fileName={fileName('quality_passport', files?.quality_passport ?? null)}
                    label="JSON"
                  />
                </div>
              }
              notice={passportStale && <p className={styles.notice}>Паспорт относится к ревизии {revisionText(passportRevision)}, текущая — {revisionLabel}.</p>}
            />
          ) : passportQuery.isPending && variantFiles.passportArtifactId ? (
            <PassportLoading />
          ) : (
            <div className={styles.passportEmpty}>
              <EmptyState
                title={passportQuery.error ? 'Паспорт качества не удалось прочитать' : 'Паспорт качества ещё не собран'}
                description={passportQuery.error ? passportQuery.error.message : undefined}
                actions={
                  !passportQuery.error && (
                    <DownloadButton
                      key={`${variant.id}:passport-empty`}
                      projectId={projectId}
                      variantId={variant.id}
                      format="quality_passport"
                      file={files?.quality_passport ?? null}
                      currentRevision={currentRevision}
                      supported
                      fileName={fileName('quality_passport', files?.quality_passport ?? null)}
                      label="Собрать паспорт"
                    />
                  )
                }
              />
            </div>
          )}
          <FileSums files={files} />
          <ReproduceCommand templateFile={templateFile} contentFile={null} strategy={variant.strategy} slideCount={slideCount} />
        </div>
      </details>
    </>
  )
}

export function ExportPage() {
  const { projectId = '', runId = '', variantId } = useParams()
  const variantsQuery = useVariants(runId)
  const variants = useMemo(() => orderVariants(variantsQuery.data ?? []), [variantsQuery.data])
  const selectedId = pickVariantId(variants, variantId)
  const selected = variants.find((variant) => variant.id === selectedId)

  let content
  if (variantsQuery.isPending) {
    content = (
      <div className={styles.loading} aria-busy="true" aria-label="Загружаем варианты">
        <Skeleton style={{ height: 64 }} />
        <Skeleton style={{ height: 104, borderRadius: 14 }} delay={0.1} />
        <Skeleton style={{ height: 320, borderRadius: 14 }} delay={0.2} />
      </div>
    )
  } else if (variantsQuery.error) {
    content = <EmptyState title="Не удалось загрузить варианты" description={variantsQuery.error.message} actions={<Button onClick={() => variantsQuery.refetch()}>Повторить</Button>} />
  } else if (!selected) {
    content = (
      <EmptyState
        title="Экспортировать пока нечего"
        description="Сначала сгенерируйте три варианта колоды."
        actions={
          <Link className={styles.emptyLink} to={routes.brief(projectId)}>
            К брифу
          </Link>
        }
      />
    )
  } else {
    content = <ExportContent key={selected.id} projectId={projectId} runId={runId} variant={selected} variants={variants} />
  }

  return <div className={styles.page}>{content}</div>
}

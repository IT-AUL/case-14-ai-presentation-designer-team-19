import { useMemo } from 'react'
import { usePassport, type ExportFormat, type QualityPassport } from '@/entities/passport'
import { useProjectEvidence } from '@/entities/project'
import { useSkillManifest, useVersion } from '@/entities/system'
import { strategyInfo, variantMetrics, type DeckFile, type DeckFiles, type VariantSummary } from '@/entities/variant'
import { DownloadButton, FORMAT_LABEL, isStaleFile } from '@/features/export-deck'
import { ReproduceCommand } from '@/features/reproduce-run'
import { buildPassportView, EditabilityProof, QualityPassport as PassportPanel } from '@/widgets/quality-passport'
import { humanError } from '@/shared/api'
import { GENERATION_BUDGET_SECONDS } from '@/shared/config'
import { formatBytes } from '@/shared/lib/format'
import { Drawer, EmptyState, Skeleton } from '@/shared/ui'
import { exportFileName } from '../model/slideModel'
import styles from './AuditPage.module.css'

interface PassportDrawerProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  projectId: string
  projectName?: string | null
  variant: VariantSummary
  files: DeckFiles | null
  passportArtifactId: string | null
  currentRevision: number
  templateFile: string | null
  slideCount: number | null
}

function readVersion(source: Record<string, unknown> | null | undefined, keys: readonly string[]): string | null {
  if (!source) return null
  for (const key of keys) {
    const value = source[key]
    if (typeof value === 'string' && value.trim()) return value.trim()
  }
  return null
}

function FileSums({ files }: { files: DeckFiles | null }) {
  const rows = (['pptx', 'pdf', 'html', 'quality_passport'] as const).flatMap((format) => {
    const file = files?.[format]
    return file ? [{ format, file }] : []
  })
  if (rows.length === 0) return null
  return (
    <section className={styles.sums} aria-label="Контрольные суммы файлов">
      <h3 className={styles.sumsTitle}>Файлы и контрольные суммы</h3>
      <dl className={styles.sumList}>
        {rows.map(({ format, file }) => (
          <div key={format} className={styles.sumRow}>
            <dt>{FORMAT_LABEL[format]}</dt>
            <dd>
              {[
                file.sizeBytes !== null ? formatBytes(file.sizeBytes) : null,
                file.deckRevision !== null ? `r${file.deckRevision}` : null,
                file.sha256 ? `sha256 ${file.sha256.slice(0, 12)}…` : null,
              ]
                .filter(Boolean)
                .join(' · ')}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  )
}

function EvidenceSummary({ projectId, hasDeck }: { projectId: string; hasDeck: boolean }) {
  const evidence = useProjectEvidence(projectId)
  const items = [
    { key: 'generated', label: 'Сборка колоды', done: Boolean(evidence.generated) || hasDeck },
    { key: 'repaired', label: 'Точечные правки и ремонт', done: Boolean(evidence.repaired) },
    { key: 'reaudited', label: 'Повторная проверка', done: Boolean(evidence.reaudited) },
    { key: 'exported', label: 'Скачивание PPTX', done: Boolean(evidence.exported) },
  ]
  return (
    <section className={styles.sums} aria-label="Прохождение этапов проверки">
      <h3 className={styles.sumsTitle}>Прохождение этапов проверки</h3>
      <dl className={styles.sumList}>
        {items.map((item) => (
          <div key={item.key} className={styles.sumRow}>
            <dt>{item.label}</dt>
            <dd>{item.done ? '✓ Выполнено' : 'По желанию'}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}

export function PassportDrawer(props: PassportDrawerProps) {
  const { open, onOpenChange, projectId, projectName, variant, files, passportArtifactId, currentRevision, templateFile, slideCount } = props
  const passportQuery = usePassport(passportArtifactId)
  const manifest = useSkillManifest()
  const version = useVersion()

  const passport: QualityPassport | null = passportQuery.data ?? null
  const passportFile: DeckFile | null = files?.quality_passport ?? null
  const passportRevision = passportFile?.deckRevision ?? null
  const passportStale = isStaleFile(passportFile, currentRevision)
  const skillVersion = readVersion(manifest.data, ['skill_version', 'version']) ?? readVersion(version.data, ['skill_version', 'skill'])

  const view = useMemo(
    () =>
      passport
        ? buildPassportView(passport, {
            budgetSeconds: GENERATION_BUDGET_SECONDS,
            planner: variant.planner,
            serverSkillVersion: skillVersion,
            fallbackPei: variantMetrics(variant).editabilityLevel,
          })
        : null,
    [passport, variant, skillVersion],
  )

  const fileName = (format: ExportFormat, file: DeckFile | null) =>
    exportFileName(variant.strategy, format, file?.deckRevision ?? currentRevision, file?.mimeType ?? null, projectName)

  return (
    <Drawer
      open={open}
      onOpenChange={onOpenChange}
      title="Паспорт качества"
      description={`Вариант «${strategyInfo(variant.strategy).name}» · ревизия r${currentRevision}`}
    >
      <div className={styles.passportDrawerBody}>
        {view ? (
          <>
            <EditabilityProof cells={view.proof} />
            <PassportPanel
              view={view}
              revisionLabel={passportRevision !== null ? `r${passportRevision}` : `r${currentRevision}`}
              actions={
                <DownloadButton
                  key={`${variant.id}:passport`}
                  projectId={projectId}
                  variantId={variant.id}
                  format="quality_passport"
                  file={passportFile}
                  currentRevision={currentRevision}
                  supported
                  fileName={fileName('quality_passport', passportFile)}
                  label="Скачать JSON"
                />
              }
              notice={
                passportStale ? (
                  <p className={styles.cardSub}>
                    Паспорт относится к ревизии r{passportRevision}, текущая ревизия — r{currentRevision}.
                  </p>
                ) : undefined
              }
            />
          </>
        ) : passportQuery.isPending && passportArtifactId ? (
          <div aria-busy="true" aria-label="Загружаем паспорт качества">
            <Skeleton style={{ height: 24, width: 200, marginBottom: 10 }} />
            <Skeleton style={{ height: 64, marginBottom: 8 }} />
            <Skeleton style={{ height: 64 }} delay={0.1} />
          </div>
        ) : (
          <EmptyState
            title={passportQuery.error ? 'Паспорт качества не удалось прочитать' : 'Паспорт качества ещё не собран'}
            description={passportQuery.error ? humanError(passportQuery.error) : 'Соберите паспорт, чтобы проверить редактируемость и метрики колоды.'}
            actions={
              !passportQuery.error ? (
                <DownloadButton
                  key={`${variant.id}:passport-empty`}
                  projectId={projectId}
                  variantId={variant.id}
                  format="quality_passport"
                  file={passportFile}
                  currentRevision={currentRevision}
                  supported
                  fileName={fileName('quality_passport', passportFile)}
                  label="Собрать паспорт"
                />
              ) : undefined
            }
          />
        )}
        <EvidenceSummary projectId={projectId} hasDeck={Boolean(variant.deck_artifact_id)} />
        <FileSums files={files} />
        <ReproduceCommand
          templateFile={templateFile ?? passport?.inputs.templateName ?? null}
          contentFile={null}
          strategy={variant.strategy}
          slideCount={slideCount}
        />
      </div>
    </Drawer>
  )
}

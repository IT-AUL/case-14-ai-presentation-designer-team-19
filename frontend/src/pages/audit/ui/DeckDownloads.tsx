import { useState } from 'react'
import { useVariantAudit } from '@/entities/audit'
import { usePassport, type ExportFormat } from '@/entities/passport'
import { useCapabilities } from '@/entities/system'
import { hasPendingVariantChanges, useVariantChanges, type DeckFile, type VariantSummary } from '@/entities/variant'
import { DownloadButton, isStaleFile } from '@/features/export-deck'
import { useVariantFiles } from '@/features/variant-files'
import { Button } from '@/shared/ui'
import { exportFileName, htmlExportSupported, withPassportMeta } from '../model/slideModel'
import styles from './AuditPage.module.css'
import { PassportDrawer } from './PassportDrawer'

interface DeckDownloadsProps {
  projectId: string
  projectName?: string | null
  variant: VariantSummary
  templateFile: string | null
  slideCount: number | null
}

export function DeckDownloads({ projectId, projectName, variant, templateFile, slideCount }: DeckDownloadsProps) {
  const [passportOpen, setPassportOpen] = useState(false)
  const variantFiles = useVariantFiles(variant.id)
  const passportQuery = usePassport(variantFiles.passportArtifactId)
  const capabilities = useCapabilities()
  const audit = useVariantAudit(variant.id)
  const changes = useVariantChanges(variant.id)
  const pendingChanges = hasPendingVariantChanges(changes.data)
  const files = variantFiles.files
  const current = Math.max(audit.data?.deck_revision ?? 1, files?.pdf?.deckRevision ?? 1, files?.pptx?.deckRevision ?? 1)
  const passport = passportQuery.data ?? null
  const pptx = withPassportMeta(files?.pptx ?? null, passport, Boolean(passport) && !isStaleFile(files?.quality_passport ?? null, current))
  const fileName = (format: ExportFormat, file: DeckFile | null) =>
    exportFileName(variant.strategy, format, file?.deckRevision ?? current, file?.mimeType ?? null, projectName)

  return (
    <div className={styles.downloads} role="group" aria-label="Скачать презентацию">
      <DownloadButton
        key={`${variant.id}:pptx`}
        projectId={projectId}
        variantId={variant.id}
        format="pptx"
        file={pptx}
        currentRevision={current}
        supported
        fileName={fileName('pptx', pptx)}
        label="Скачать PPTX"
        primary
        compact
        pendingChanges={pendingChanges}
      />
      <DownloadButton
        key={`${variant.id}:pdf`}
        projectId={projectId}
        variantId={variant.id}
        format="pdf"
        file={files?.pdf ?? null}
        currentRevision={current}
        supported
        fileName={fileName('pdf', files?.pdf ?? null)}
        label="PDF"
        compact
        pendingChanges={pendingChanges}
      />
      <DownloadButton
        key={`${variant.id}:html`}
        projectId={projectId}
        variantId={variant.id}
        format="html"
        file={files?.html ?? null}
        currentRevision={current}
        supported={htmlExportSupported(capabilities.data)}
        fileName={fileName('html', files?.html ?? null)}
        label="HTML"
        compact
        pendingChanges={pendingChanges}
      />
      {pendingChanges && (
        <span className={styles.downloadsPendingNote} role="status">
          Дождитесь завершения правок
        </span>
      )}
      <Button size="sm" variant="ghost" onClick={() => setPassportOpen(true)}>
        Паспорт качества
      </Button>
      <PassportDrawer
        open={passportOpen}
        onOpenChange={setPassportOpen}
        projectId={projectId}
        projectName={projectName}
        variant={variant}
        files={files}
        passportArtifactId={variantFiles.passportArtifactId}
        currentRevision={current}
        templateFile={templateFile}
        slideCount={slideCount}
      />
    </div>
  )
}

import { clsx } from 'clsx'
import { useMemo, type ReactNode } from 'react'
import type { DesignDna as DesignDnaData, TemplateDetail } from '@/entities/template'
import { formatBytes } from '@/shared/lib/format'
import { buildDnaView } from '../model/facts'
import { countLabel, FORMS } from '../model/labels'
import { FontsSection } from './FontsSection'
import { PaletteSection } from './PaletteSection'
import { RolesSection, UnsupportedSection } from './RoleSections'
import { FontScaleSection, GridSection, LayoutsSection, MasterTreeSection } from './StructureSections'
import { UnderstandingPanel } from './UnderstandingPanel'
import styles from './DesignDna.module.css'

interface DesignDnaProps {
  detail: TemplateDetail
  dna: DesignDnaData | undefined
}

type Row = [number, ReactNode | null][]

function spanClass(span: number) {
  return `span-${span}`
}

function GridRow({ cells }: { cells: Row }) {
  const present = cells.filter(([, node]) => node !== null)
  return present.map(([span, node], index) => (
    <div key={index} className={clsx(styles.cell, styles[spanClass(present.length === 1 ? 12 : span)])}>
      {node}
    </div>
  ))
}

export function DesignDna({ detail, dna }: DesignDnaProps) {
  const { system, facts, understanding } = useMemo(() => buildDnaView(detail, dna), [detail, dna])
  const available = new Map(understanding.coverage.map((item) => [item.group, item.available]))
  const { counts } = system
  const structure = [counts.masters && countLabel(counts.masters, FORMS.master), counts.layouts && countLabel(counts.layouts, FORMS.layout), counts.slides && countLabel(counts.slides, FORMS.slide)].filter(Boolean)

  const rows: Row[] = [
    [
      [7, available.get('palette') ? <PaletteSection system={system} /> : null],
      [5, available.get('fonts') ? <FontsSection system={system} /> : null],
    ],
    [
      [6, facts.scale.length ? <FontScaleSection scale={facts.scale} /> : null],
      [6, facts.spacing || facts.anchors.length ? <GridSection spacing={facts.spacing} anchors={facts.anchors} aspectRatio={facts.aspectRatio} /> : null],
    ],
    [
      [4, facts.tree.length ? <MasterTreeSection tree={facts.tree} /> : null],
      [8, available.get('layouts') ? <LayoutsSection blueprints={facts.blueprints} totalLayouts={counts.layouts} aspectRatio={facts.aspectRatio} /> : null],
    ],
    [
      [6, facts.roles.length ? <RolesSection roles={facts.roles} /> : null],
      [6, facts.unsupported.length ? <UnsupportedSection features={facts.unsupported} /> : null],
    ],
  ]

  return (
    <div className={styles.root}>
      <div className={styles.strip}>
        <div className={styles.stripName}>{system.fileName}</div>
        <div className={clsx(styles.stripMeta, styles.mono)}>{formatBytes(system.sizeBytes)}</div>
        {system.aspectLabel !== '—' && (
          <div className={styles.stripMeta}>
            {system.aspectLabel}
            {system.sizeLabel !== '—' ? ` · ${system.sizeLabel}` : ''}
          </div>
        )}
        {structure.length > 0 && <div className={styles.stripMeta}>{structure.join(' · ')}</div>}
        <div className={clsx(styles.stripMeta, styles.mono)} title={system.sha256}>
          sha256 {system.sha256.slice(0, 8)}
        </div>
      </div>

      <UnderstandingPanel system={system} understanding={understanding} facts={facts} />

      <div className={styles.grid}>
        {rows.map((cells, index) => (
          <GridRow key={index} cells={cells} />
        ))}
      </div>
    </div>
  )
}

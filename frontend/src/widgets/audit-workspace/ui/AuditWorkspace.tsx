import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useEffectEvent, useMemo, useRef, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router'
import {
  auditIssuesQuery,
  buildIssueViews,
  checkKind,
  clusterIssueViews,
  countOpenBySeverity,
  DEFAULT_VIEW_FILTER,
  dismissalEntry,
  filterIssueViews,
  groupIssueViews,
  isPendingStatus,
  issueGroupKey,
  reportOpenCounts,
  ruleMeta,
  selectableIds,
  SEVERITY,
  slideNumber,
  useAuditIssues,
  useContextualAudit,
  useVariantAudit,
  withSelection,
  type AuditIssue,
  type AuditRun,
  type IssueGrouping,
  type IssueView,
  type IssueViewFilter,
  type JournalEntry,
  type Severity,
} from '@/entities/audit'
import { markEvidence } from '@/entities/project'
import { useActiveProviderSession } from '@/entities/provider-session'
import { FEATURE_PATHS, useCapabilityFlag } from '@/entities/system'
import { orderVariants, slidePreviewUrl, useVariants, useVariantSlides } from '@/entities/variant'
import { RepairBar, selectionKey, useRepairPreview, useRepairSelected } from '@/features/repair-issues'
import { useIssueSelection } from '@/features/select-issues'
import { useVariantFiles } from '@/features/variant-files'
import { humanError } from '@/shared/api'
import { pluralize } from '@/shared/lib/format'
import { Button, EmptyState, Skeleton, useToast } from '@/shared/ui'
import { shouldIgnoreShortcut } from '../lib/keyboard'
import { useStableCallback } from '../lib/useStableCallback'
import { appendJournal, useVariantJournal } from '../model/journalStore'
import { localizeRuleTitle } from '../model/labels'
import { AuditSummary } from './AuditSummary'
import { IssueFilters } from './IssueFilters'
import { IssueList, type ClusteredGroup } from './IssueList'
import { IssuePanel, type PanelTab } from './IssuePanel'
import { JournalList } from './JournalList'
import { ReauditBanner } from './ReauditBanner'
import { SlideFilmstrip, type FilmstripSlide } from './SlideFilmstrip'
import styles from './AuditWorkspace.module.css'
import panelStyles from './IssuePanel.module.css'

export type StageMode = 'slide' | 'compare'

export interface AuditStageProps {
  pdfUrl: string | null
  pdfRevision: number | null
  imageUrl: string | null
  imageRevision: number | null
  revision: number
  slideNumber: number
  slideCount: number
  title: string | undefined
  views: readonly IssueView[]
  journal: readonly JournalEntry[]
  activeKey: string | null
  linkedKeys: readonly string[]
  hoverKey: string | null
  mode: StageMode
  onModeChange: (mode: StageMode) => void
  onPick: (key: string) => void
  onHover: (key: string | null) => void
  onSlideChange: (slideNumber: number) => void
}

interface AuditWorkspaceProps {
  projectId: string
  runId: string
  variantId: string
  slide: number | null
  onSlideChange: (slideNumber: number) => void
  renderStage: (stage: AuditStageProps) => ReactNode
  variantHref: (variantId: string, slideNumber?: number) => string
  actions?: ReactNode
  renderSlidePanel?: (slide: SlidePanelContext) => ReactNode
}

export interface SlidePanelContext {
  slideNumber: number
  slideId: string | null
  title: string | undefined
  revision: number
}

interface ReauditSnapshot {
  errorsBefore: number
}

const EMPTY_ISSUES: AuditIssue[] = []

const errorText = (error: unknown) => humanError(error)

const onSlide = (view: IssueView, number: number) => view.issue.slide_index === number - 1

function worstSeverity(views: readonly IssueView[]): Severity | null {
  return views.reduce<Severity | null>((worst, view) => {
    if (!worst || SEVERITY[view.issue.severity].order < SEVERITY[worst].order) return view.issue.severity
    return worst
  }, null)
}

const expandedByDefault = (grouping: IssueGrouping) => grouping !== 'rule'

function isSeverity(value: unknown): value is Severity {
  return value === 'blocker' || value === 'error' || value === 'warning' || value === 'info'
}

function mergeDeckLevelIssues(audit: AuditRun | undefined, issues: readonly AuditIssue[]): AuditIssue[] {
  if (!audit) return [...issues]
  const result: AuditIssue[] = [...issues]
  const rawFindings = (audit as Record<string, unknown>).deck_level_findings
  if (Array.isArray(rawFindings)) {
    rawFindings.forEach((entry, index) => {
      const record = typeof entry === 'object' && entry !== null ? (entry as Record<string, unknown>) : null
      const ruleCode = typeof record?.rule_code === 'string'
        ? record.rule_code
        : typeof record?.rule_id === 'string'
          ? record.rule_id
          : typeof record?.code === 'string'
            ? record.code
            : 'deck.finding'
      const id = typeof record?.id === 'string' ? record.id : `${audit.id}:deck:${index}`
      if (result.some((issue) => issue.id === id)) return
      const messageText = typeof entry === 'string'
        ? entry
        : typeof record?.message === 'string'
          ? record.message
          : typeof record?.description === 'string'
            ? record.description
            : typeof record?.title === 'string'
              ? record.title
              : localizeRuleTitle(ruleCode)
      result.push({
        schema_version: '1.0',
        id,
        audit_run_id: audit.id,
        deck_revision: audit.deck_revision,
        rule_code: ruleCode,
        deterministic: record?.deterministic !== false,
        severity: isSeverity(record?.severity) ? record.severity : 'warning',
        slide_id: null,
        slide_index: null,
        shape_ids: [],
        bbox: null,
        message: messageText,
        measured_value: typeof record?.measured_value === 'number' ? record.measured_value : 1,
        threshold: typeof record?.threshold === 'number' ? record.threshold : 0,
        evidence: [],
        confidence: typeof record?.confidence === 'number' ? record.confidence : 1,
        status: 'open',
        repairable: Boolean(record?.repairable),
        proposed_actions: [],
        provenance: {},
      })
    })
  }
  const summaryByType = audit.summary_by_type ?? {}
  for (const [ruleCode, count] of Object.entries(summaryByType)) {
    if (typeof count !== 'number' || count <= 0) continue
    if (result.some((issue) => issue.rule_code === ruleCode)) continue
    const fallbackSeverity: Severity = (audit.summary_by_severity?.blocker ?? 0) > 0
      ? 'blocker'
      : (audit.summary_by_severity?.error ?? 0) > 0
        ? 'error'
        : 'warning'
    result.push({
      schema_version: '1.0',
      id: `${audit.id}:deck-type:${ruleCode}`,
      audit_run_id: audit.id,
      deck_revision: audit.deck_revision,
      rule_code: ruleCode,
      deterministic: true,
      severity: fallbackSeverity,
      slide_id: null,
      slide_index: null,
      shape_ids: [],
      bbox: null,
      message: `${localizeRuleTitle(ruleCode)}: ${count} ${pluralize(count, ['замечание', 'замечания', 'замечаний'])} по всей презентации`,
      measured_value: count,
      threshold: 0,
      evidence: [],
      confidence: 1,
      status: 'open',
      repairable: false,
      proposed_actions: [],
      provenance: {},
    })
  }
  return result
}

export function AuditWorkspace({ projectId, runId, variantId, slide, onSlideChange, renderStage, variantHref, actions, renderSlidePanel }: AuditWorkspaceProps) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const toast = useToast()
  const session = useActiveProviderSession()
  const modelAuto = useCapabilityFlag(FEATURE_PATHS.modelAuto)
  const renderPreviews = useCapabilityFlag(FEATURE_PATHS.pngPreviews)
  const files = useVariantFiles(variantId)
  const variantsQuery = useVariants(runId)
  const auditQuery = useVariantAudit(variantId)
  const audit = auditQuery.data
  const issuesQuery = useAuditIssues(audit?.id)
  const slidesQuery = useVariantSlides(variantId)
  const contextual = useContextualAudit(variantId)
  const stored = useVariantJournal(variantId)

  const [filter, setFilter] = useState<IssueViewFilter>(DEFAULT_VIEW_FILTER)
  const [grouping, setGrouping] = useState<IssueGrouping>('rule')
  const [groupOverrides, setGroupOverrides] = useState<ReadonlyMap<string, boolean>>(new Map())
  const [tab, setTab] = useState<PanelTab>(() => (renderSlidePanel ? 'slide' : 'issues'))
  const [mode, setMode] = useState<StageMode>('slide')
  const [activeKey, setActiveKey] = useState<string | null>(null)
  const [hoverKey, setHoverKey] = useState<string | null>(null)
  const [reaudit, setReaudit] = useState<ReauditSnapshot | null>(null)
  const [bannerOpen, setBannerOpen] = useState(true)
  const [reauditPending, setReauditPending] = useState(false)

  const revision = audit?.deck_revision ?? 1
  const journal = useMemo(() => stored.entries.filter((entry) => entry.revision <= revision), [stored.entries, revision])
  const batches = stored.batches.filter((batch) => batch.revision <= revision)
  const rawIssues = issuesQuery.data ?? EMPTY_ISSUES
  const issues = useMemo(() => mergeDeckLevelIssues(audit, rawIssues), [audit, rawIssues])
  const baseViews = useMemo(() => buildIssueViews(issues, journal), [issues, journal])
  const selection = useIssueSelection(selectableIds(baseViews))
  const dryRunAvailable = useCapabilityFlag(FEATURE_PATHS.repairDryRun)
  const repairPreview = useRepairPreview(audit?.id)
  const views = useMemo(() => withSelection(baseViews, selection.selectedIds), [baseViews, selection.selectedIds])
  const selectedIssues = views.filter((view) => view.status === 'selected').map((view) => view.issue)

  const repair = useRepairSelected({
    projectId,
    variantId,
    auditId: audit?.id,
    fromRevision: revision,
  })

  const slideInfos = slidesQuery.data ?? []
  const lastIssueSlide = issues.reduce((max, issue) => Math.max(max, slideNumber(issue) ?? 0), 0)
  const slideCount = Math.max(slidesQuery.data ? slideInfos.length : lastIssueSlide, 1)
  const zeroBasedSlides = slideInfos.some((info) => info.index === 0)
  const slideAt = (number: number) => slideInfos.find((info) => info.index === (zeroBasedSlides ? number - 1 : number))
  const titleOf = (number: number) => slideAt(number)?.title ?? undefined

  const pending = views.filter((view) => isPendingStatus(view.status))
  const firstTroubled = pending.map((view) => slideNumber(view.issue)).find((number): number is number => number !== null)
  const currentSlide = Math.min(Math.max(slide ?? firstTroubled ?? 1, 1), slideCount)

  const previousSelection = useRef<{ variantId: string; slides: typeof slideInfos; id: string | null; number: number }>({ variantId, slides: slideInfos, id: null, number: currentSlide })
  useEffect(() => {
    if (!slidesQuery.isSuccess || slidesQuery.isFetching || slideInfos.length === 0) return
    const previous = previousSelection.current
    if (previous.variantId === variantId && previous.slides !== slideInfos && previous.id && previous.number === (slide ?? previous.number)) {
      const moved = slideInfos.find((item) => item.id === previous.id)
      const number = moved ? moved.index + (zeroBasedSlides ? 1 : 0) : Math.min(previous.number, slideCount)
      if (number !== slide) onSlideChange(number)
      previousSelection.current = { variantId, slides: slideInfos, id: moved?.id ?? slideAt(number)?.id ?? null, number }
    } else previousSelection.current = { variantId, slides: slideInfos, id: slideAt(currentSlide)?.id ?? null, number: currentSlide }
  })

  const filtered = filterIssueViews(views, filter)
  const groups: ClusteredGroup[] = groupIssueViews(filtered, grouping, titleOf).map((group) => ({
    ...group,
    label: grouping === 'rule' ? localizeRuleTitle(group.label) : group.label,
    clusters: clusterIssueViews(group.views),
  }))
  const clusters = groups.flatMap((group) => group.clusters)
  const statusScoped = filterIssueViews(views, { ...DEFAULT_VIEW_FILTER, status: filter.status })
  const effectiveActive = views.some((view) => view.key === activeKey) ? activeKey : null
  const activeCluster = effectiveActive ? clusters.find((cluster) => cluster.views.some((view) => view.key === effectiveActive)) : undefined
  const linkedKeys = activeCluster ? activeCluster.views.map((view) => view.key) : []
  const isExpanded = (groupKey: string) => groupOverrides.get(`${grouping}|${groupKey}`) ?? expandedByDefault(grouping)
  const expanded = new Set(groups.filter((group) => isExpanded(group.key)).map((group) => group.key))

  const severityCounts = countOpenBySeverity(views)
  const kindCounts = {
    D: pending.filter((view) => checkKind(view.issue) === 'D').length,
    N: pending.filter((view) => checkKind(view.issue) === 'N').length,
  }
  const fixableCount = selectableIds(views).length
  const ruleCount = new Set(pending.map((view) => view.issue.rule_code)).size
  const stats = {
    fixed: views.filter((view) => view.status === 'fixed').length,
    dismissed: views.filter((view) => view.status === 'dismissed').length,
    failed: views.filter((view) => view.status === 'unresolved').length,
  }
  const hasProvider = Boolean(session) || modelAuto
  const loaded = Boolean(audit) && issuesQuery.isSuccess

  useEffect(() => {
    if (loaded) reportOpenCounts(projectId, { blocker: severityCounts.blocker, error: severityCounts.error }, runId)
  }, [loaded, projectId, runId, severityCounts.blocker, severityCounts.error])

  const filmstrip: FilmstripSlide[] = Array.from({ length: slideCount }, (_, index) => {
    const number = index + 1
    const open = pending.filter((view) => onSlide(view, number))
    return { number, title: titleOf(number), imageUrl: slidePreviewUrl(slideAt(number), renderPreviews), openCount: open.length, worst: worstSeverity(open) }
  })

  const stageViews = filterIssueViews(views, { ...filter, status: 'all' }).filter((view) => onSlide(view, currentSlide))
  const stageJournal = journal.filter((entry) => entry.issue.slide_index === currentSlide - 1)

  const setGroupOpen = (groupKey: string, open: boolean) =>
    setGroupOverrides((current) => new Map(current).set(`${grouping}|${groupKey}`, open))

  const activate = (key: string, toggle = false) => {
    if (toggle && activeCluster?.views.some((view) => view.key === key)) {
      setActiveKey(null)
      return
    }
    const view = views.find((candidate) => candidate.key === key)
    setActiveKey(key)
    setTab('issues')
    if (!view) return
    if (!filtered.includes(view)) setFilter((current) => ({ ...current, status: 'all' }))
    const groupKey = issueGroupKey(view.issue, grouping)
    if (!isExpanded(groupKey)) setGroupOpen(groupKey, true)
    const number = slideNumber(view.issue)
    if (number && number !== currentSlide) onSlideChange(number)
  }

  const pickSlide = (number: number) => {
    const next = Math.min(Math.max(number, 1), slideCount)
    onSlideChange(next)
    setActiveKey(tab === 'issues' ? clusters.find((cluster) => onSlide(cluster.primary, next))?.key ?? null : null)
  }

  const stepIssue = (delta: 1 | -1) => {
    if (clusters.length === 0) return
    const index = clusters.findIndex((cluster) => cluster.views.some((view) => view.key === effectiveActive))
    const nextIndex = index === -1 ? (delta === 1 ? 0 : clusters.length - 1) : Math.min(Math.max(index + delta, 0), clusters.length - 1)
    const next = clusters[nextIndex]
    if (next) activate(next.key)
  }

  const onKeyDown = useEffectEvent((event: KeyboardEvent) => {
    if (shouldIgnoreShortcut(event)) return
    const actions: Record<string, () => void> = {
      j: () => stepIssue(1),
      ArrowDown: () => stepIssue(1),
      k: () => stepIssue(-1),
      ArrowUp: () => stepIssue(-1),
      ArrowRight: () => pickSlide(currentSlide + 1),
      ArrowLeft: () => pickSlide(currentSlide - 1),
      x: () => {
        const cluster = clusters.find((candidate) => candidate.views.some((view) => view.key === effectiveActive))
        if (!cluster?.primary.selectable) return
        selection.setMany(
          cluster.views.map((view) => view.issue.id),
          cluster.primary.status !== 'selected',
        )
      },
      Escape: () => setActiveKey(null),
    }
    const action = actions[event.key]
    if (!action) return
    event.preventDefault()
    action()
  })

  useEffect(() => {
    const listener = (event: KeyboardEvent) => onKeyDown(event)
    window.addEventListener('keydown', listener)
    return () => window.removeEventListener('keydown', listener)
  }, [])

  const settleReaudit = (fresh: readonly AuditIssue[], entries: readonly JournalEntry[], freshRevision: number, errorsBefore: number) => {
    setReaudit({ errorsBefore })
    setBannerOpen(true)
    const counts = countOpenBySeverity(buildIssueViews(fresh, entries))
    if (counts.blocker === 0 && freshRevision > 1) markEvidence(projectId, 'reaudited')
  }

  const runRepair = async (targets: readonly AuditIssue[]) => {
    if (targets.length === 0 || repair.isPending) return
    const errorsBefore = severityCounts.error
    try {
      const result = await repair.run(targets)
      appendJournal(variantId, result.entries, result.batch)
      selection.clear()
      setTab('journal')
      settleReaudit(result.reaudit, [...journal, ...result.entries], result.batch.revision, errorsBefore)
      const { fixed, unresolved } = result.batch
      toast.show(`Ревизия r${result.batch.revision}: исправлено ${fixed}${unresolved > 0 ? `, не удалось ${unresolved}` : ''}`)
    } catch (error) {
      toast.show(`Не удалось применить правки: ${errorText(error)}`)
    }
  }

  const runReaudit = async () => {
    setReauditPending(true)
    const errorsBefore = severityCounts.error
    try {
      const { data: fresh } = await auditQuery.refetch({ throwOnError: true })
      const freshIssues = fresh ? await queryClient.fetchQuery({ ...auditIssuesQuery(fresh.id), staleTime: 0 }) : []
      settleReaudit(freshIssues, journal, fresh?.deck_revision ?? revision, errorsBefore)
    } catch (error) {
      toast.show(`Повторный аудит не удался: ${errorText(error)}`)
    } finally {
      setReauditPending(false)
    }
  }

  const runContextual = () => {
    if (!hasProvider) return
    if (audit?.contextual_status === 'completed') {
      toast.show('Контекстные проверки для этой ревизии уже выполнены')
      return
    }
    contextual.mutate(session?.id, {
      onSuccess: (run) =>
        toast.show(run.contextual_status === 'completed' ? 'Смысловая проверка слайдов завершена' : `Контекстные проверки: ${run.contextual_status}`),
      onError: (error) => toast.show(`Не удалось выполнить смысловую проверку: ${errorText(error)}`),
    })
  }

  const onActivateCard = useStableCallback((key: string) => activate(key, true))
  const onRepairCard = useStableCallback((targets: readonly AuditIssue[]) => void runRepair(targets))
  const onDismissed = useStableCallback((dismissed: readonly AuditIssue[], reason: string) => {
    if (dismissed.length === 0) return
    appendJournal(
      variantId,
      dismissed.map((issue) => dismissalEntry(issue, reason, revision)),
    )
    const name = localizeRuleTitle(ruleMeta((dismissed[0] as AuditIssue).rule_code).name)
    toast.show(`Отклонено: ${name}${dismissed.length > 1 ? ` ×${dismissed.length}` : ''}`)
  })
  const onToggleGroup = useStableCallback((groupKey: string) => setGroupOpen(groupKey, !isExpanded(groupKey)))

  if (auditQuery.isError || issuesQuery.isError) {
    return (
      <div className={styles.center}>
        <EmptyState
          title="Не удалось загрузить аудит"
          description={errorText(auditQuery.error ?? issuesQuery.error)}
          actions={
            <Button variant="primary" onClick={() => void (audit ? issuesQuery.refetch() : auditQuery.refetch())}>
              Повторить
            </Button>
          }
        />
      </div>
    )
  }

  if (!audit || issuesQuery.isPending) return <WorkspaceSkeleton />

  return (
    <div className={styles.root}>
      <AuditSummary
        variants={orderVariants(variantsQuery.data ?? [])}
        variantId={variantId}
        onVariantChange={(id) => navigate(variantHref(id, currentSlide))}
        audit={audit}
        severityCounts={severityCounts}
        kindCounts={kindCounts}
        fixableCount={fixableCount}
        hasProvider={hasProvider}
        contextualPending={contextual.isPending}
        onRunContextual={runContextual}
        reauditPending={reauditPending}
        onReaudit={() => void runReaudit()}
        actions={actions}
      />
      {reaudit && bannerOpen && (
        <ReauditBanner
          revision={revision}
          blockers={severityCounts.blocker}
          errors={{ before: reaudit.errorsBefore, after: severityCounts.error }}
          stats={stats}
          onClose={() => setBannerOpen(false)}
        />
      )}
      <div className={styles.grid}>
        <SlideFilmstrip slides={filmstrip} current={currentSlide} pdfUrl={files.pdfUrl} onPick={pickSlide} />
        <div className={styles.stage}>
          {renderStage({
            pdfUrl: files.pdfUrl,
            pdfRevision: files.files?.pdf?.deckRevision ?? null,
            imageUrl: slidePreviewUrl(slideAt(currentSlide), renderPreviews),
            imageRevision: slideAt(currentSlide)?.revision ?? null,
            revision,
            slideNumber: currentSlide,
            slideCount,
            title: titleOf(currentSlide),
            views: stageViews,
            journal: stageJournal,
            activeKey: effectiveActive,
            linkedKeys,
            hoverKey,
            mode,
            onModeChange: setMode,
            onPick: (key) => activate(key),
            onHover: setHoverKey,
            onSlideChange: pickSlide,
          })}
        </div>
        <IssuePanel
          tab={tab}
          onTabChange={setTab}
          openCount={pending.length}
          journalCount={journal.length}
          issues={
            <>
              <p className={panelStyles.headline} aria-live="polite">
                <b>
                  {pending.length} {pluralize(pending.length, ['находка', 'находки', 'находок'])}
                </b>
                <span>
                  · {ruleCount} {pluralize(ruleCount, ['правило', 'правила', 'правил'])}
                </span>
                <span>· {fixableCount} исправимо</span>
              </p>
              <IssueFilters
                filter={filter}
                onFilterChange={setFilter}
                grouping={grouping}
                onGroupingChange={setGrouping}
                counts={{
                  all: statusScoped.length,
                  D: statusScoped.filter((view) => checkKind(view.issue) === 'D').length,
                  N: statusScoped.filter((view) => checkKind(view.issue) === 'N').length,
                }}
              />
              <IssueList
                groups={groups}
                grouping={grouping}
                expanded={expanded}
                onToggleGroup={onToggleGroup}
                selectedIds={selection.selectedIds}
                activeKey={effectiveActive}
                hoverKey={hoverKey}
                auditId={audit.id}
                repairPending={repair.isPending}
                onActivate={onActivateCard}
                onHover={setHoverKey}
                onSelect={selection.setMany}
                onRepair={onRepairCard}
                onDismissed={onDismissed}
              />
              <RepairBar
                selectedCount={selection.count}
                fixableCount={fixableCount}
                pending={repair.isPending}
                onSelectAll={selection.selectAll}
                onClear={selection.clear}
                onRepair={() => void runRepair(selectedIssues)}
                onPreview={dryRunAvailable ? () => repairPreview.mutate(selectedIssues, { onError: (error) => toast.show(errorText(error)) }) : undefined}
                previewPending={repairPreview.isPending}
                preview={repairPreview.data && repairPreview.data.key === selectionKey(selectedIssues) ? repairPreview.data : null}
                onClosePreview={repairPreview.reset}
              />
            </>
          }
          journal={<JournalList entries={journal} batches={batches} />}
          slide={renderSlidePanel?.({ slideNumber: currentSlide, slideId: slideAt(currentSlide)?.id ?? null, title: titleOf(currentSlide), revision })}
        />
      </div>
    </div>
  )
}

function WorkspaceSkeleton() {
  return (
    <div className={styles.root} aria-busy="true" aria-label="Загружаю аудит">
      <div className={styles.skeletonBar}>
        <Skeleton className={styles.skeletonBlock} />
      </div>
      <div className={styles.grid}>
        <div className={styles.skeletonStrip}>
          {Array.from({ length: 6 }, (_, index) => (
            <Skeleton key={index} className={styles.skeletonThumb} delay={index * 0.08} />
          ))}
        </div>
        <div className={styles.stage}>
          <Skeleton className={styles.skeletonSlide} />
        </div>
        <div className={styles.skeletonPanel}>
          <Skeleton className={styles.skeletonCard} />
          <Skeleton className={styles.skeletonCard} />
        </div>
      </div>
    </div>
  )
}

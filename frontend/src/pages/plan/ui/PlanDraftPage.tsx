import { useMemo, useState } from 'react'
import { Link, useParams } from 'react-router'
import { readPlanRequest, type StoredPlanRequest } from '@/features/edit-deck-plan'
import { useContentPack } from '@/entities/content-pack'
import type { GenerationCreate, PlanProposal } from '@/entities/generation'
import { DEFAULT_STRATEGY, STRATEGY_ORDER, strategyInfo, type CatalogStrategy } from '@/entities/variant'
import { routes } from '@/shared/config'
import { EmptyState, PageHeader, Segmented } from '@/shared/ui'
import { EditablePlan } from './EditablePlan'
import styles from './PlanPage.module.css'

const EMPTY_HEADINGS: ReadonlyMap<string, string> = new Map()

function catalogStrategies(strategies: readonly string[]): CatalogStrategy[] {
  const known = STRATEGY_ORDER.filter((strategy) => strategies.includes(strategy))
  return known.length > 0 ? known : [...STRATEGY_ORDER]
}

function buildBody({ request }: StoredPlanRequest): Omit<GenerationCreate, 'deck_plan' | 'deck_plan_id'> {
  const { strategies, ...body } = request
  return { ...body, variants: catalogStrategies(strategies ?? []).map((strategy) => ({ strategy })) }
}

function proposalKey(proposal: PlanProposal, index: number): string {
  return proposal.strategy ?? `plan-${index}`
}

function defaultProposal(proposals: readonly PlanProposal[]): string {
  const preferred = proposals.findIndex((proposal) => proposal.strategy === DEFAULT_STRATEGY)
  const index = preferred >= 0 ? preferred : 0
  const proposal = proposals[index]
  return proposal ? proposalKey(proposal, index) : ''
}

export function PlanDraftPage() {
  const { projectId = '' } = useParams()
  const stored = useMemo(() => readPlanRequest(projectId), [projectId])
  const proposals = useMemo(() => stored?.result.proposals ?? [], [stored])
  const [selected, setSelected] = useState(() => defaultProposal(proposals))
  const { data: contentPack } = useContentPack(stored?.request.content_pack_id)
  const headings = useMemo<ReadonlyMap<string, string>>(
    () => (contentPack ? new Map(contentPack.sections.map((section) => [section.id, section.heading])) : EMPTY_HEADINGS),
    [contentPack],
  )

  const index = proposals.findIndex((proposal, position) => proposalKey(proposal, position) === selected)
  const proposal = proposals[index] ?? proposals[0]

  if (!stored || !proposal) {
    return (
      <div className={styles.page}>
        <PageHeader eyebrow="Шаг 3 · План" title="План колоды" />
        <EmptyState
          title="Плана пока нет"
          description="Заполните бриф и нажмите «Сначала план»."
          actions={
            <Link className={styles.secondaryLink} to={routes.brief(projectId)}>
              К брифу
            </Link>
          }
        />
      </div>
    )
  }

  const options = proposals.map((item, position) => ({
    value: proposalKey(item, position),
    label: item.strategy ? strategyInfo(item.strategy).name : `План ${position + 1}`,
  }))

  return (
    <EditablePlan
      key={proposal.deckPlan.id}
      projectId={projectId}
      draftKey={`draft:${projectId}:${proposal.deckPlan.id}`}
      basePlan={proposal.deckPlan}
      headings={headings}
      eyebrow="Шаг 3 · План"
      buildLabel="Собрать 3 варианта по плану"
      buildBody={buildBody(stored)}
      notice={null}
      toolbar={
        options.length > 1 ? (
          <Segmented label="План какой стратегии править" options={options} value={proposalKey(proposal, Math.max(0, index))} onChange={setSelected} />
        ) : undefined
      }
    />
  )
}

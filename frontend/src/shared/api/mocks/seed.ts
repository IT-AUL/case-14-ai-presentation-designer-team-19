import { contentPackFixture, designDnaFixture, generationFixture, generationJobFixture, projectFixture, templateDetailFixture, variantFixtures } from './fixtures'
import { completeGeneration, fixtureFor } from './generation'
import type { VariantIdentity } from './generation'
import { createPlaceholderPptx } from './pptx'
import type { MockStore, RunRecord } from './store'
import type { ExportFormat } from './types'

async function seedTemplate(store: MockStore): Promise<void> {
  const { latest_analysis: analysis, ...asset } = templateDetailFixture
  store.templates.set(asset.id, { asset, analysis: analysis ?? null, dna: designDnaFixture })
  await store.storeBytes({
    id: asset.artifact_id,
    filename: asset.filename,
    mimeType: asset.media_type,
    bytes: createPlaceholderPptx([{ title: asset.filename, lines: ['Исходный файл шаблона не хранится в демонстрационном режиме.'] }]),
  })
}

function seedRun(store: MockStore): { run: RunRecord; identities: Map<string, VariantIdentity> } {
  const { variants, ...detail } = generationFixture
  const identities = new Map<string, VariantIdentity>()
  for (const summary of variants) {
    const fixture = variantFixtures[fixtureFor(summary.strategy)]
    const artifactIds: Partial<Record<ExportFormat, string>> = {}
    for (const artifact of fixture.export.artifacts) {
      if (artifact.format === 'pdf' || artifact.format === 'quality_passport') artifactIds[artifact.format] = artifact.artifact_id
    }
    identities.set(summary.id, {
      auditId: fixture.audit.id,
      slideIds: new Map(fixture.slides.map((slide) => [slide.id, slide.id])),
      deckArtifactId: summary.deck_artifact_id ?? undefined,
      exportId: fixture.export.id,
      artifactIds,
    })
    store.variants.set(summary.id, {
      summary: { ...summary, status: 'queued', deck_artifact_id: null, export_ids: [] },
      fixture: fixtureFor(summary.strategy),
      slides: [],
      auditId: null,
      revision: 1,
    })
  }
  const brief = detail.deck_plan?.brief ?? { purpose: 'product', audience: '', language: 'ru', target_slide_count: 12 }
  const run: RunRecord = {
    detail: { ...detail, state: 'running', finished_at: null, deck_plan: null },
    variantIds: variants.map((variant) => variant.id),
    request: {
      template_id: detail.template_id,
      content_pack_id: detail.content_pack_id,
      provider_session_id: null,
      brief,
      variants: variants.map((variant) => ({ strategy: fixtureFor(variant.strategy) })),
      config_version: null,
      seed: null,
      use_llm: false,
    },
  }
  store.runs.set(run.detail.id, run)
  store.jobs.set(generationJobFixture.id, { ...generationJobFixture, state: 'running', finished_at: null })
  return { run, identities }
}

const EXTRA_PROJECTS: readonly { name: string; template: boolean; pack: boolean }[] = [
  { name: 'Итоги года для совета директоров', template: true, pack: true },
  { name: 'Запуск продукта: дорожная карта', template: true, pack: true },
  { name: 'Питч для инвесторов', template: true, pack: false },
  { name: 'Обзор рынка доставки', template: false, pack: false },
  { name: 'Стратегия продаж 2027', template: true, pack: true },
]

function seedExtraProjects(store: MockStore, packId: string): void {
  const base = Date.now()
  EXTRA_PROJECTS.forEach((item, index) => {
    const created = new Date(base - (index + 2) * 26 * 60 * 60 * 1000).toISOString()
    store.projects.set(`seed-project-${index + 1}`, {
      ...projectFixture,
      id: `seed-project-${index + 1}`,
      name: item.name,
      status: 'draft',
      template_id: item.template ? templateDetailFixture.id : null,
      content_pack_id: item.pack ? packId : null,
      latest_run_id: null,
      latest_run_state: null,
      latest_run_stage: null,
      created_at: created,
      updated_at: created,
    })
  })
}

export async function seedStore(store: MockStore): Promise<void> {
  const pack = contentPackFixture.content_pack
  store.projects.set(projectFixture.id, { ...projectFixture, template_id: templateDetailFixture.id, content_pack_id: pack.id })
  await seedTemplate(store)
  store.contentPacks.set(pack.id, { projectId: projectFixture.id, pack })
  store.jobs.set(contentPackFixture.job.id, contentPackFixture.job)
  const { run, identities } = seedRun(store)
  await completeGeneration(store, run.detail.id, generationFixture.finished_at ?? generationFixture.created_at, identities)
  store.touchProject(projectFixture.id, { latest_run_id: run.detail.id, latest_run_state: 'completed' })
  seedExtraProjects(store, pack.id)
}

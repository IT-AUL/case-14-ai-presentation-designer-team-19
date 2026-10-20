import { http, HttpResponse } from 'msw'
import { agentChatHandlers } from './agentChat'
import type { RequestHandler } from 'msw'
import { API_BASE } from '@/shared/config'
import type { MockContext } from '../context'
import { designDnaFixture, templateDetailFixture, variantFixtures } from '../fixtures'
import { json, notFound, stateConflict, validationError } from '../http'
import { nowIso, prefixedId, rebrand } from '../ids'
import type { MockVariantChangeItem } from '../store'
import { isRecord, readJson } from '../validate'

type Object = Record<string, unknown>
type Scenario = 'idea' | 'data'
const fixtures = import.meta.glob<{ body: Object }>(['../fixtures/assistant/*.json', '!../fixtures/assistant/artifact-*.json'], { eager: true, import: 'default' })
const artifacts = import.meta.glob<{ body: { mime_type: string; base64: string } }>('../fixtures/assistant/artifact-*.json', { import: 'default' })
const fixture = (scenario: Scenario, label: string): Object => structuredClone(fixtures[`../fixtures/assistant/${scenario}-${label}.json`]?.body ?? {})
const objects = (value: unknown): Object[] => Array.isArray(value) ? value.filter(isRecord) : []
const object = (value: unknown): Object => isRecord(value) ? value : {}

interface SessionState {
  scenario: Scenario
  session: Object
  intake: Object
  questions: Object
}
interface PackState {
  projectId: string
  understanding: Object
}

export function assistantHandlers({ router, store }: MockContext): RequestHandler[] {
  const states = new Map<string, SessionState>()
  const packs = new Map<string, PackState>()
  const plans = new Map<string, Object>()
  const previewProjects = new Map<string, string>()
  const jobs = new Map<string, { scenario: Scenario; label: string; projectId: string }>()
  const priorities = new Map<string, string>()
  const analyzedBrandbooks = new Set<string>()
  function enrichAnalyzedBrandbook(summary: Object): Object {
    const palette = objects(summary.palette).map((item, idx) => {
      if (idx === 0) return { ...item, source: 'brand.pdf:page=2:vision: Основной акцент для заголовков и ключевых метрик' }
      if (idx === 1) return { ...item, source: 'brand.pdf:page=3:vision: Глубокий синий для карточек и плашек' }
      return item
    })
    const fonts = objects(summary.fonts).map((item, idx) => {
      if (idx === 0) return { ...item, source: 'brand.pdf:page=4:vision: Заголовки набираются антиквой DejaVu Serif' }
      if (idx === 1) return { ...item, source: 'brand.pdf:page=5:vision: Основной текст и подписи — Liberation Sans' }
      return item
    })
    return {
      ...summary,
      palette,
      fonts,
      analysis: { pages: ['brand.pdf:page=2', 'brand.pdf:page=3', 'brand.pdf:page=4', 'brand.pdf:page=5'], status: 'completed' },
    }
  }
  for (const scenario of ['idea', 'data'] as const) {
    const project = fixture(scenario, 'project')
    const intake = fixture(scenario, 'intake')
    states.set(String(project.id), { scenario, session: fixture(scenario, 'session-initial'), intake, questions: fixture(scenario, 'questions') })
    for (const label of ['understanding', 'understanding-edited']) {
      const understanding = fixture(scenario, label)
      packs.set(String(understanding.content_pack_id), { projectId: String(project.id), understanding })
    }
    plans.set(String(fixture(scenario, 'plan').plan_id), fixture(scenario, 'plan'))
    if (scenario === 'idea') jobs.set(String(fixture(scenario, 'autopilot').job_id), { scenario, label: 'autopilot', projectId: String(project.id) })
    else {
      jobs.set(String(fixture(scenario, 'style-start').job_id), { scenario, label: 'style', projectId: String(project.id) })
      jobs.set(String(fixture(scenario, 'generation').job_id), { scenario, label: 'generation', projectId: String(project.id) })
    }
  }
  function state(id: string): SessionState {
    const saved = states.get(id)
    if (saved) return saved
    const project = store.project(id)
    const session = fixture('idea', 'session-initial')
    session.artifacts = { template_id: project.template_id, content_pack_id: project.content_pack_id }
    const created = { scenario: 'idea' as const, session, intake: {}, questions: fixture('idea', 'questions') }
    states.set(id, created)
    return created
  }
  function mark(projectId: string, step: number, completed: number[]) {
    const current = state(projectId)
    current.session = { ...current.session, step, completed_steps: completed }
  }
  function pack(id: string): PackState {
    const current = packs.get(id)
    if (!current) throw notFound('Content pack', id)
    return current
  }
  function plan(id: string): Object {
    const current = plans.get(id)
    if (!current) throw notFound('Plan', id)
    return current
  }
  function editPack(id: string, update: (understanding: Object) => Object): Object {
    const current = pack(id)
    const packId = prefixedId('pack')
    const understanding = { ...update(structuredClone(current.understanding)), content_pack_id: packId }
    packs.set(packId, { projectId: current.projectId, understanding })
    const currentState = state(current.projectId)
    currentState.session.artifacts = { ...object(currentState.session.artifacts), content_pack_id: packId, plan_id: null, preview_id: null, generation_id: null }
    currentState.questions = fixture(currentState.scenario, 'questions')
    mark(current.projectId, 2, [1])
    store.touchProject(current.projectId, { content_pack_id: packId })
    return { content_pack_id: packId, filled_from: id, understanding }
  }
  const ALLOWED_CHANGE_ACTIONS = [
    'shorter',
    'other_layout',
    'other_chart',
    'style:faithful',
    'style:balanced',
    'style:visual',
    'replace_title',
    'replace_text',
    'larger_chart',
    'punchier',
    'add_figure',
    'to_chart',
    'to_table',
    'split_slide',
  ]
  const CHANGE_NOTES: Record<string, string> = {
    shorter: 'Сделал слайд короче.',
    other_layout: 'Поменял раскладку слайда.',
    other_chart: 'Поменял график на слайде.',
    'style:faithful': 'Пересобрал слайд в строгом стиле.',
    'style:balanced': 'Пересобрал слайд в сбалансированном стиле.',
    'style:visual': 'Пересобрал слайд в визуальном стиле.',
    punchier: 'Усилил главный вывод.',
  }
  function hasVariant(variantId: string): boolean {
    if (store.variants.has(variantId)) return true
    return (['idea', 'data'] as const).some((scenario) =>
      objects(fixture(scenario, 'variants').items).some((variant) => variant.id === variantId),
    )
  }
  function variantChangeBucket(variantId: string): MockVariantChangeItem[] {
    let bucket = store.variantChanges.get(variantId)
    if (!bucket) {
      bucket = []
      store.variantChanges.set(variantId, bucket)
    }
    return bucket
  }
  function appliedRevisionFor(variantId: string): number {
    const base = store.variants.get(variantId)?.revision ?? 1
    return variantChangeBucket(variantId)
      .filter((entry) => entry.state === 'completed')
      .reduce((max, entry) => Math.max(max, entry.target_revision), base)
  }
  function acceptedRevisionFor(variantId: string): number {
    const applied = appliedRevisionFor(variantId)
    return variantChangeBucket(variantId)
      .filter((entry) => entry.state === 'queued' || entry.state === 'running' || entry.state === 'completed')
      .reduce((max, entry) => Math.max(max, entry.target_revision), applied)
  }
  function scheduleChangeTransitions(variantId: string, item: MockVariantChangeItem): void {
    const runTimer = setTimeout(() => {
      if (item.state !== 'queued') return
      item.state = 'running'
      item.started_at = nowIso()
    }, 220)
    ;(runTimer as { unref?: () => void }).unref?.()

    const doneTimer = setTimeout(() => {
      if (item.state !== 'queued' && item.state !== 'running') return
      item.state = 'completed'
      item.completed_at = nowIso()
      const regen = fixture('data', 'regenerate')
      const note = CHANGE_NOTES[item.action] ?? String(regen.assistant_note ?? 'Сделал слайд короче.')
      item.result = {
        ...regen,
        variant_id: variantId,
        deck_revision: item.target_revision,
        slide: {
          ...object(regen.slide),
          id: item.slide_id,
          variant_id: variantId,
          revision: item.target_revision,
        },
        assistant_note: note,
      }
      const variantRecord = store.variants.get(variantId)
      if (variantRecord) {
        variantRecord.revision = item.target_revision
        if (variantRecord.auditId && store.audits.has(variantRecord.auditId)) {
          const auditRecord = store.audits.get(variantRecord.auditId)
          if (auditRecord) {
            auditRecord.run = { ...auditRecord.run, deck_revision: item.target_revision }
          }
        }
        for (const [expId, exp] of store.exports.entries()) {
          if (exp.variant_id === variantId) {
            store.exports.set(expId, { ...exp, deck_revision: item.target_revision })
          }
        }
      }
    }, 480)
    ;(doneTimer as { unref?: () => void }).unref?.()
  }
  const agentDecks = new Map<string, { summary: Object; slides: Object[] }>()
  const agentDeck = (id: string) => {
    const existing = store.variants.get(id)
    if (existing) return { summary: existing.summary as unknown as Object, slides: existing.slides as unknown as Object[] }
    const cached = agentDecks.get(id)
    if (cached) return cached
    for (const scenario of ['idea', 'data'] as const) {
      const summary = objects(fixture(scenario, 'variants').items).find((item) => item.id === id)
      if (summary) { const deck = { summary, slides: objects(fixture(scenario, 'slides').items) }; agentDecks.set(id, deck); return deck }
    }
    return null
  }
  const { route } = router
  return [
    ...agentChatHandlers({ router, store }, { deck: agentDeck, plan, task: (id) => state(id).session }),
    http.get(`*${API_BASE}/artifacts/:artifactId/download`, async ({ params }) => {
      const load = artifacts[`../fixtures/assistant/artifact-${String(params.artifactId)}.json`]
      if (!load) return
      const { body } = await load()
      const bytes = Uint8Array.from(atob(body.base64), (char) => char.charCodeAt(0))
      return new HttpResponse(bytes, { headers: { 'Content-Type': body.mime_type } })
    }),
    route('get', '/projects/:projectId/session', ({ param }) => json(state(param('projectId')).session)),
    route('post', '/projects/:projectId/intake', async ({ request, param }) => {
      const projectId = param('projectId')
      const current = state(projectId)
      const form = await request.formData()
      const files = form.getAll('files').filter((file): file is File => typeof file !== 'string')
      const idea = form.get('idea')
      if (!files.length && !(typeof idea === 'string' && idea.trim())) throw validationError('files', 'Добавьте файлы или идею')
      const isDataFileName = (name: string) =>
        /\.(csv|xlsx)$/i.test(name) || (/\.json$/i.test(name) && !/brand|бренд|tokens|logo|логотип/i.test(name))
      const scenario = files.some((file) => isDataFileName(file.name)) ? 'data' : 'idea'
      const intake = fixture(scenario, 'intake')
      intake.project_id = projectId
      intake.files = files.map((file) => {
        const supported = /\.(pptx|potx|md|txt|docx|pdf|csv|xlsx|json|png|jpe?g|webp|svg)$/i.test(file.name)
        const isImg = /\.(png|jpe?g|webp|svg)$/i.test(file.name)
        const kind = !supported
          ? 'unknown'
          : /\.(pptx|potx)$/i.test(file.name)
            ? 'template'
            : isDataFileName(file.name)
              ? 'data'
              : isImg || /brand|бренд|tokens|logo|логотип/i.test(file.name)
                ? 'brandbook'
                : 'content'
        const example = objects(intake.files).find((f) => f.kind === kind) ?? objects(fixture('data', 'brand-intake').files).find((f) => f.kind === kind) ?? {}
        return { ...example, name: file.name, kind, detected_kind: kind, accepted: supported, reason: supported ? (example.reason ?? null) : 'формат не поддерживается', size_bytes: file.size }
      })
      if (!files.some((file) => /\.(pptx|potx)$/i.test(file.name))) intake.template_id = object(current.session.artifacts).template_id ?? null
      if (intake.template_id && !store.templates.has(String(intake.template_id))) {
        const { latest_analysis: analysis, ...asset } = templateDetailFixture
        const record = { ...asset, id: String(intake.template_id), project_id: projectId }
        const seeded = analysis ? { ...analysis, id: prefixedId('ana'), template_id: record.id } : null
        const dna = seeded ? rebrand(designDnaFixture, new Map([[designDnaFixture.template_id, record.id], [designDnaFixture.analysis_id, seeded.id]])) : designDnaFixture
        store.templates.set(record.id, { asset: record, analysis: seeded, dna })
      }
      intake.brandbook_id = objects(intake.files).some((file) => file.kind === 'brandbook') ? fixture('data', 'brandbook').id : null
      const hasMaterial = (typeof idea === 'string' && Boolean(idea.trim())) || objects(intake.files).some((file) => file.kind === 'content' || file.kind === 'data')
      let packId: string | null = null
      if (hasMaterial) {
        packId = prefixedId('pack')
        const understanding = fixture(scenario, 'understanding')
        understanding.content_pack_id = packId
        const existingDatasets = objects(understanding.datasets)
        const extraDatasets: Object[] = []
        const presetDims: Record<string, [number, number]> = {
          'sales_monthly.csv': [24, 3],
          'regions_comparison.csv': [6, 3],
          'channels_share.json': [4, 2],
          'nps_with_gaps.csv': [12, 2],
          'order_values.csv': [60, 1],
          'products_reference.csv': [240, 6],
        }
        for (const file of files.filter((item) => isDataFileName(item.name))) {
          const alreadyListed = existingDatasets.some((ds) => String(ds.file ?? '').toLowerCase() === file.name.toLowerCase())
          if (alreadyListed) continue
          const rawText = typeof file.text === 'function' ? await file.text().catch(() => '') : ''
          let rows = 0
          let cols = 0
          if (/\.json$/i.test(file.name)) {
            try {
              const parsed: unknown = JSON.parse(rawText)
              if (Array.isArray(parsed)) {
                rows = parsed.length
                cols = isRecord(parsed[0]) ? Object.keys(parsed[0]).length : 1
              } else if (isRecord(parsed) && Array.isArray(parsed.data)) {
                rows = parsed.data.length
                cols = Array.isArray(parsed.columns)
                  ? parsed.columns.length
                  : Array.isArray(parsed.data[0])
                    ? parsed.data[0].length
                    : 2
              }
            } catch {
              rows = 0
            }
          } else if (/\.csv$/i.test(file.name)) {
            const lines = rawText.trim().split(/\r?\n/).filter(Boolean)
            rows = Math.max(0, lines.length - 1)
            const header = lines[0]?.replace(/"[^"]*"/g, 'col') ?? ''
            cols = header ? header.split(',').length : 0
          }
          if (rows === 0 || cols === 0) {
            const fallback = presetDims[file.name.toLowerCase()]
            if (fallback) {
              ;[rows, cols] = fallback
            }
          }
          if (rows > 0 && cols > 0) {
            extraDatasets.push({
              id: prefixedId('ds'),
              title: file.name.replace(/\.[^.]+$/, ''),
              file: file.name,
              profile_summary: `${rows} строк; ${cols} колонок`,
              task: 'comparison',
              task_ru: 'сравнение',
              recommendations: [],
              why: '',
              insights: [],
            })
          }
        }
        if (extraDatasets.length > 0) {
          understanding.datasets = [...existingDatasets, ...extraDatasets]
        }
        if (!intake.template_id) {
          intake.assistant_note = 'Материалы принял — добавьте корпоративный шаблон .pptx или .potx, чтобы я знал правила оформления.'
        }
        packs.set(packId, { projectId, understanding })
      } else intake.assistant_note = 'Принял: шаблон. Добавьте материалы или идею.'
      intake.content_pack_id = packId ?? object(current.session.artifacts).content_pack_id ?? null
      current.scenario = scenario
      current.intake = intake
      current.questions = fixture(scenario, 'questions')
      current.session = { ...fixture(scenario, 'session-initial'), artifacts: { template_id: intake.template_id, content_pack_id: intake.content_pack_id, brandbook_id: intake.brandbook_id, plan_id: null, preview_id: null, generation_id: null } }
      mark(projectId, packId ? 2 : 1, packId ? [1] : [])
      store.touchProject(projectId, { template_id: String(intake.template_id ?? ''), ...(packId ? { content_pack_id: packId } : {}) })
      return json(intake, 202)
    }),
    route('patch', '/projects/:projectId/intake/files/:name', async ({ request, param }) => {
      const current = state(param('projectId'))
      const body = await readJson(request)
      if (!['template', 'content', 'data', 'brandbook', 'unknown'].includes(String(body.kind))) throw validationError('kind', 'Неизвестный тип файла')
      const file = objects(current.intake.files).find((file) => file.name === param('name'))
      if (!file) throw notFound('Intake file', param('name'))
      const fileName = String(file.name)
      const isImg = /\.(png|jpe?g|webp|svg)$/i.test(fileName)
      const extMatch = /\.[^.]+$/.exec(fileName)
      const ext = extMatch ? extMatch[0].toLowerCase() : ''
      if (isImg && body.kind !== 'brandbook') {
        file.kind = body.kind
        file.accepted = false
        file.reason = `Формат ${ext} не подходит для роли «${String(body.kind)}»`
      } else {
        file.kind = body.kind
        file.accepted = true
        file.reason = 'тип выбран вручную'
      }
      return json(current.intake)
    }),
    route('get', '/templates/:templateId/summary', ({ param }) => json({ ...fixture('data', 'template-summary'), template_id: param('templateId') })),
    route('get', '/brandbooks/:brandbookId', ({ param }) => json({ ...fixture('data', 'brandbook'), id: param('brandbookId') })),
    route('get', '/brandbooks/:brandbookId/summary', ({ param }) => {
      const id = param('brandbookId')
      const base = { ...fixture('data', 'brand-summary'), id, priority: priorities.get(id) ?? 'brandbook' }
      return json(analyzedBrandbooks.has(id) ? enrichAnalyzedBrandbook(base) : base)
    }),
    route('patch', '/brandbooks/:brandbookId', async ({ request, param }) => {
      const id = param('brandbookId')
      const body = await readJson(request)
      if (body.analyze === true) {
        const nextPriority = body.priority === 'template' || body.priority === 'brandbook' ? body.priority : (priorities.get(id) ?? 'brandbook')
        priorities.set(id, nextPriority)
        analyzedBrandbooks.add(id)
        const base = { ...fixture('data', nextPriority === 'template' ? 'brand-priority' : 'brand-summary'), id, priority: nextPriority }
        return json(enrichAnalyzedBrandbook(base))
      }
      if (body.priority !== 'template' && body.priority !== 'brandbook') throw validationError('priority', 'Неизвестный приоритет')
      priorities.set(id, body.priority)
      const base = { ...fixture('data', body.priority === 'template' ? 'brand-priority' : 'brand-summary'), id }
      return json(analyzedBrandbooks.has(id) ? enrichAnalyzedBrandbook(base) : base)
    }),
    route('get', '/content-packs/:packId/understanding', ({ param }) => {
      const current = pack(param('packId'))
      mark(current.projectId, 3, [1, 2])
      return json(current.understanding)
    }),
    route('patch', '/content-packs/:packId/blocks', async ({ request, param }) => {
      const body = await readJson(request)
      return json(editPack(param('packId'), (understanding) => {
        let blocks = objects(understanding.blocks)
        for (const op of objects(body.ops)) {
          const index = blocks.findIndex((block) => block.id === op.block_id)
          if (index < 0) throw notFound('Block', String(op.block_id))
          if (op.op === 'remove') blocks = blocks.filter((block) => block.id !== op.block_id)
          else if (op.op === 'edit') blocks[index] = { ...blocks[index], ...Object.fromEntries(Object.entries(op).filter(([key]) => ['title', 'text', 'items'].includes(key))) }
          else if (op.op === 'move') {
            const [moved] = blocks.splice(index, 1)
            if (moved) blocks.splice(Number(op.index), 0, moved)
          } else if (op.op === 'merge') {
            const target = blocks.find((block) => block.id === op.into)
            if (!target) throw notFound('Block', String(op.into))
            target.text = [target.text, blocks[index]?.text].filter(Boolean).join('\n')
            blocks.splice(index, 1)
          } else throw validationError('op', 'Неизвестное действие')
        }
        return { ...understanding, blocks }
      }))
    }),
    route('patch', '/content-packs/:packId/insights/:insightId', async ({ request, param }) => {
      const current = pack(param('packId'))
      const body = await readJson(request)
      const insight = objects(current.understanding.datasets).flatMap((dataset) => objects(dataset.insights)).find((insight) => insight.id === param('insightId'))
      if (!insight) throw notFound('Insight', param('insightId'))
      Object.assign(insight, body, typeof body.text === 'string' ? { headline: body.text, confirmed: true } : {})
      if (body.confirmed !== undefined && insight.implication) insight.needs_confirmation = !body.confirmed
      return json(insight)
    }),
    route('post', '/content-packs/:packId/notes', async ({ request, param }) => {
      const body = await readJson(request)
      return json(editPack(param('packId'), (understanding) => ({ ...understanding, blocks: [...objects(understanding.blocks), ...Array.isArray(body.items) ? body.items.filter((item): item is string => typeof item === 'string' && Boolean(item.trim())).map((text, index) => ({ id: `b:notes:${index}`, kind: 'fact', title: 'Тезисы автора', text })) : []] })))
    }),
    route('get', '/projects/:projectId/questions', ({ param }) => json(state(param('projectId')).questions)),
    route('post', '/projects/:projectId/answers', async ({ request, param }) => {
      const current = state(param('projectId'))
      const body = await readJson(request)
      const response = fixture(current.scenario, 'answers')
      response.brief = { ...object(current.session.brief), ...object(response.brief), ...object(body.answers) }
      current.session.brief = response.brief
      current.questions.questions = []
      mark(param('projectId'), 4, [1, 2, 3])
      return json(response)
    }),
    http.post(`*${API_BASE}/projects/:projectId/plans`, async ({ request, params }) => {
      const projectId = String(params.projectId)
      const current = states.get(projectId)
      const body = object(await request.clone().json().catch(() => ({})))
      const packEntry = typeof body.content_pack_id === 'string' ? packs.get(body.content_pack_id) : undefined
      if ((!current || !current.intake.content_pack_id) && !packEntry) return undefined
      const active = current ?? state(projectId)
      const scenario =
        current?.scenario ??
        (body.content_pack_id === fixture('data', 'understanding').content_pack_id ? 'data' : 'idea')
      const result = fixture(scenario, 'plan')
      result.project_id = projectId
      result.content_pack_id = body.content_pack_id
      result.template_id = body.template_id
      const requestedSlides = Number(object(body.brief).target_slide_count)
      if (Number.isFinite(requestedSlides) && requestedSlides > 0) {
        const cf = object(result.content_fill)
        const before = object(cf.before)
        const capacity = Number(before.capacity) || requestedSlides
        if (requestedSlides <= capacity) {
          result.content_fill = {
            ...cf,
            requested_slides: requestedSlides,
            target_slides: requestedSlides,
            shortage_reason: null,
            before: { ...before, capacity, requested: requestedSlides, status: 'ok', missing_slides: 0 },
          }
          result.sufficiency = { status: 'ok', content_slides: capacity, target_slides: requestedSlides }
        }
      }
      plans.set(String(result.plan_id), result)
      active.session.artifacts = { ...object(active.session.artifacts), plan_id: result.plan_id }
      mark(projectId, 5, [1, 2, 3, 4])
      return json(result)
    }),
    route('get', '/plans/:planId', ({ param }) => json(plan(param('planId')))),
    route('get', '/projects/:projectId/plans/:planId', ({ param }) => json(plan(param('planId')))),
    ...(['patch', 'post'] as const).map((method) => route(method, `/plans/:planId/slides/:slideId${method === 'post' ? '/actions' : ''}`, async ({ request, param }) => {
      const current = plan(param('planId'))
      const body = await readJson(request)
      const deck = object(objects(current.plans)[0]?.deck_plan)
      const slides = objects(deck.slides)
      const slide = slides.find((slide) => slide.id === param('slideId'))
      if (!slide) throw notFound('Slide', param('slideId'))
      if (method === 'patch') {
        const chart = object(slide.chart)
        const allowed = [chart.type, ...objects(chart.alternatives).map((item) => item.type), 'table']
        if (!allowed.includes(body.chart_type)) throw validationError('chart_type', 'Этот тип не подходит данным')
        slide.chart = { ...chart, type: body.chart_type }
        slide.desired_visual = body.chart_type === 'table' ? 'table' : 'chart'
        slide.content_units = objects(slide.content_units).map((unit) => {
          if (unit.kind !== 'chart' && unit.kind !== 'table') return unit
          if (body.chart_type === 'table') return { ...unit, role: 'table', kind: 'table', chart_spec: null }
          return { ...unit, role: 'chart', kind: 'chart', chart_spec: { ...object(unit.chart_spec), chart_type: body.chart_type } }
        })
      } else if (body.action === 'remove') deck.slides = slides.filter((item) => item !== slide)
      else if (body.action === 'shorten') {
        slide.content_units = objects(slide.content_units).slice(0, Math.max(1, Math.ceil(objects(slide.content_units).length / 2)))
      } else if (body.action === 'to_diagram' || body.action === 'to_chart') slide.desired_visual = body.action === 'to_chart' ? 'chart' : 'diagram'
      else if (body.action === 'split') deck.slides = slides.flatMap((item) => item === slide ? [slide, { ...structuredClone(slide), id: prefixedId('slide') }] : [item])
      else if (body.action === 'merge_next') {
        const index = slides.indexOf(slide)
        const next = slides[index + 1]
        if (next) { slide.content_units = [...objects(slide.content_units), ...objects(next.content_units)]; deck.slides = slides.filter((item) => item !== next) }
      } else throw validationError('action', 'Неизвестное действие')
      objects(deck.slides).forEach((item, index) => { item.index = index })
      return json(current)
    })),
    route('post', '/plans/:planId/slides/:slideId/research', async ({ request, param }) => {
      const current = plan(param('planId'))
      const body = await readJson(request)
      if (body.focus !== undefined && !['facts', 'numbers'].includes(String(body.focus))) throw validationError('focus', 'Неизвестный фокус поиска')
      const deck = object(objects(current.plans)[0]?.deck_plan)
      const slide = objects(deck.slides).find((item) => item.id === param('slideId'))
      if (!slide) throw notFound('Slide', param('slideId'))
      const evidence = (Array.isArray(slide.evidence_ids) ? slide.evidence_ids : []).slice(0, 1)
      const added = [
        { role: 'bullet', kind: 'bullet', text: 'Найдено в источниках: рост выручки 2,1× за год', origin: 'web', evidence_ids: evidence },
        { role: 'bullet', kind: 'bullet', text: 'Доля корпоративного сегмента выросла до 41%', origin: 'web', evidence_ids: evidence },
      ]
      slide.content_units = [...objects(slide.content_units), ...added]
      return json({ plan_set: current, slide, added_units: added.length, research: { focus: body.focus ?? 'numbers', sources: objects(object(current.research).sources) } })
    }),
    route('post', '/plans/:planId/style-previews', ({ param }) => {
      const current = plan(param('planId'))
      const started = fixture('data', 'style-start')
      jobs.set(String(started.job_id), { scenario: 'data', label: 'style', projectId: String(current.project_id) })
      previewProjects.set(String(started.preview_id), String(current.project_id))
      return json(started, 202)
    }),
    route('get', '/style-previews/:previewId', ({ param }) => json({ ...fixture('data', 'style-preview'), preview_id: param('previewId') })),
    route('post', '/projects/:projectId/session/autopilot', async ({ request, param }) => {
      const current = state(param('projectId'))
      const body = await readJson(request)
      if (Number(body.from_step) < 1 || Number(body.from_step) > 7) throw validationError('from_step', 'Неизвестный шаг')
      const response = fixture('idea', 'autopilot')
      current.session.job_id = response.job_id
      mark(param('projectId'), 6, [1, 2, 3, 4, 5])
      jobs.set(String(response.job_id), { scenario: 'idea', label: 'autopilot', projectId: param('projectId') })
      return json(response, 202)
    }),
    http.get(`*${API_BASE}/jobs/:jobId/events`, ({ params, request }) => {
      const current = jobs.get(String(params.jobId))
      if (!current) return undefined
      const url = new URL(request.url)
      const after = Math.max(Number(url.searchParams.get('after') ?? 0), Number(request.headers.get('Last-Event-ID') ?? 0))
      if (!Number.isSafeInteger(after) || after < 0) return json({ error: { code: 'invalid_input', message: 'Invalid cursor' } }, 422)
      const changeSse = [
        'id: 1\nevent: job.started\ndata: {"sequence":1,"type":"job.started","stage":"slide_update","progress":0.45,"message":"Обновляю слайд"}',
        'id: 2\nevent: job.completed\ndata: {"sequence":2,"type":"job.completed","stage":"completed","progress":1,"message":"Слайд обновлён"}',
      ].join('\n\n')
      const raw = current.label === 'change' ? changeSse : String(fixture(current.scenario, `${current.label}-events`).sse ?? '')
      const frames = raw.replace(/\r\n/g, '\n').split('\n\n').filter((frame) => Number(/^id: (\d+)/m.exec(frame)?.[1] ?? 0) > after)
      const encoder = new TextEncoder()
      let index = 0
      return new HttpResponse(new ReadableStream({
        pull(controller) {
          const frame = frames[index++]
          if (frame === undefined) {
            const currentState = state(current.projectId)
            if (current.label === 'autopilot' || current.label === 'generation') {
              currentState.session = { ...fixture(current.scenario, 'session-final'), job_id: null }
            } else if (current.label === 'style') {
              currentState.session.artifacts = { ...object(currentState.session.artifacts), preview_id: fixture('data', 'style-start').preview_id }
            }
            controller.close()
          } else controller.enqueue(encoder.encode(`${frame}\n\n`))
        },
      }), { headers: { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' } })
    }),
    http.get(`*${API_BASE}/jobs/:jobId`, ({ params }) => {
      const current = jobs.get(String(params.jobId))
      if (!current) return undefined
      if (current.label === 'change') {
        return json({ id: String(params.jobId), project_id: current.projectId, kind: 'slide_change', status: 'succeeded', progress: 1, message: 'Слайд обновлён' })
      }
      return json(fixture(current.scenario, `${current.label}-job`))
    }),
    http.post(`*${API_BASE}/projects/:projectId/generations`, async ({ params }) => {
      const current = states.get(String(params.projectId))
      if (!current?.intake.content_pack_id) return
      const detail = fixture(current.scenario, 'generation-detail')
      const response = { generation_id: detail.id, job_id: detail.job_id, variant_ids: objects(detail.variants).map((variant) => variant.id) }
      jobs.set(String(detail.job_id), { scenario: current.scenario, label: current.scenario === 'idea' ? 'autopilot' : 'generation', projectId: String(params.projectId) })
      current.session.job_id = detail.job_id
      current.session.artifacts = { ...object(current.session.artifacts), generation_id: detail.id }
      mark(String(params.projectId), 6, [1, 2, 3, 4, 5])
      return json(response, 202)
    }),
    http.get(`*${API_BASE}/generations/:runId/variants`, ({ params }) => {
      const scenario = (['idea', 'data'] as const).find((scenario) => fixture(scenario, 'generation-detail').id === params.runId)
      return scenario ? json(fixture(scenario, 'variants')) : undefined
    }),
    http.get(`*${API_BASE}/variants/:variantId`, ({ params }) => {
      const variants = (['idea', 'data'] as const).flatMap((scenario) => objects(fixture(scenario, 'variants').items))
      const found = variants.find((variant) => variant.id === params.variantId)
      return found ? json(agentDeck(String(params.variantId))?.summary ?? found) : undefined
    }),
    http.post(`*${API_BASE}/variants/:variantId/audits`, ({ params }) => {
      const variantId = String(params.variantId)
      const scenario = (['idea', 'data'] as const).find((item) =>
        objects(fixture(item, 'variants').items).some((variant) => variant.id === variantId),
      )
      if (!scenario) return undefined
      const auditId = variantFixtures.balanced.audit.id
      const job = store.createJob('audit', String(fixture(scenario, 'project').id), { resultIds: { audit_id: auditId } })
      return json({ audit_id: auditId, job_id: job.id }, 202)
    }),
    http.get(`*${API_BASE}/variants/:variantId/slides`, ({ params }) => {
      const scenario = (['idea', 'data'] as const).find((scenario) => objects(fixture(scenario, 'variants').items).some((variant) => variant.id === params.variantId))
      return scenario ? json({ items: agentDeck(String(params.variantId))?.slides ?? [], next_cursor: null }) : undefined
    }),
    http.get(`*${API_BASE}/variants/:variantId/slide-drafts`, ({ params }) => {
      const variantId = String(params.variantId)
      const scenario = (['idea', 'data'] as const).find((scenario) => objects(fixture(scenario, 'variants').items).some((variant) => variant.id === variantId))
      if (!scenario) return undefined
      const slides = objects(fixture(scenario, 'slides').items).map((slide, idx) => {
        const index = typeof slide.index === 'number' ? slide.index : idx + 1
        return {
          variant_id: variantId,
          index,
          slide_id: typeof slide.slide_plan_id === 'string' ? slide.slide_plan_id : `slide-${idx}`,
          slide_key: `${variantId}:${index}`,
          revision: 1,
          preview_revision: 2,
          preview_artifact_id: typeof slide.preview_artifact_id === 'string' ? slide.preview_artifact_id : null,
          status: 'rendered',
        }
      })
      return json({ variant_id: variantId, state: 'completed', slides })
    }),
    http.get(`*${API_BASE}/generations/:runId`, ({ params }) => {
      const found = (['idea', 'data'] as const).map((scenario) => fixture(scenario, 'generation-detail')).find((item) => item.id === params.runId)
      return found ? json(agentDeck(String(params.variantId))?.summary ?? found) : undefined
    }),
    route('get', '/slides/:slideId/explain', ({ param }) => {
      const slideId = param('slideId')
      const allSlides = [
        ...(['idea', 'data'] as const).flatMap((scenario) => objects(fixture(scenario, 'slides').items)),
        ...[...store.slides.values()].map((item) => item as unknown as Object),
      ]
      const slideRecord = allSlides.find((item) => item.id === slideId)
      const planSlideId = typeof slideRecord?.slide_plan_id === 'string' ? slideRecord.slide_plan_id : null
      const dataPlanSlides = objects(object(fixture('data', 'generation-detail').deck_plan).slides)
      const planSlide = planSlideId ? dataPlanSlides.find((item) => item.id === planSlideId) : null
      const planChart = planSlide && isRecord(planSlide.chart) && typeof planSlide.chart.type === 'string' ? planSlide.chart : null
      if (slideId === fixture('data', 'chart-slide').id || planChart) {
        const base = fixture('data', 'chart-explain')
        if (!planChart) return json({ ...base, slide_id: slideId })
        const chosenType = String(planChart.type)
        const reasons = Array.isArray(planChart.reasons) ? planChart.reasons : base.chosen
        return json({
          ...base,
          slide_id: slideId,
          slide_plan_id: planSlideId ?? base.slide_plan_id,
          title: typeof planSlide?.title_intent === 'string' ? planSlide.title_intent : base.title,
          view_id: typeof planChart.view_id === 'string' ? planChart.view_id : base.view_id,
          chosen: { type: chosenType, reasons },
          recommended: { type: chosenType, reasons },
          alternatives: Array.isArray(planChart.alternatives) ? planChart.alternatives : base.alternatives,
          assistant_note: typeof planChart.why === 'string' ? planChart.why : base.assistant_note,
        })
      }
      return json({ ...fixture('data', 'slide-explain'), slide_id: slideId })
    }),
    http.get(`*${API_BASE}/slides/:slideId`, ({ params }) => {
      const found = (['idea', 'data'] as const).flatMap((scenario) => objects(fixture(scenario, 'slides').items)).find((item) => item.id === params.slideId)
      return found ? json(agentDeck(String(params.variantId))?.summary ?? found) : undefined
    }),
    route('get', '/variants/:variantId/changes', ({ param }) => {
      const variantId = param('variantId')
      if (!hasVariant(variantId)) throw notFound('Variant', variantId)
      const applied = appliedRevisionFor(variantId)
      const accepted = acceptedRevisionFor(variantId)
      const items = variantChangeBucket(variantId).map((entry) => ({ ...entry }))
      return json({
        variant_id: variantId,
        revision: applied,
        applied_revision: applied,
        accepted_revision: accepted,
        items,
      })
    }),
    route('post', '/variants/:variantId/changes', async ({ request, param }) => {
      const variantId = param('variantId')
      if (!hasVariant(variantId)) throw notFound('Variant', variantId)
      const body = await readJson(request)
      const action = String(body.action ?? 'shorter')
      if (!ALLOWED_CHANGE_ACTIONS.includes(action)) throw validationError('action', 'Неизвестное действие')
      const slideId = String(body.slide_id ?? '').trim()
      if (!slideId) throw validationError('slide_id', 'Не указан слайд')
      const accepted = acceptedRevisionFor(variantId)
      if (typeof body.expected_revision === 'number' && body.expected_revision !== accepted) {
        throw stateConflict('accepted revision changed', { actual_revision: accepted })
      }
      const targetRevision = accepted + 1
      const changeId = prefixedId('chg')
      const jobId = prefixedId('job')
      const item: MockVariantChangeItem = {
        id: changeId,
        change_id: changeId,
        job_id: jobId,
        variant_id: variantId,
        slide_id: slideId,
        action,
        chart_type: typeof body.chart_type === 'string' ? body.chart_type : null,
        text: typeof body.text === 'string' ? body.text : null,
        expected_revision: accepted,
        base_revision: accepted,
        target_revision: targetRevision,
        state: 'queued',
        created_at: nowIso(),
        started_at: null,
        completed_at: null,
        result: null,
        error: null,
        events_url: `${API_BASE}/jobs/${jobId}/events`,
      }
      variantChangeBucket(variantId).push(item)
      scheduleChangeTransitions(variantId, item)
      return json({ ...item }, 202)
    }),
    route('post', '/variants/:variantId/changes/:changeId/cancel', ({ param }) => {
      const variantId = param('variantId')
      const changeId = param('changeId')
      if (!hasVariant(variantId)) throw notFound('Variant', variantId)
      const bucket = variantChangeBucket(variantId)
      const idx = bucket.findIndex((entry) => entry.change_id === changeId || entry.id === changeId)
      const target = idx >= 0 ? bucket[idx] : undefined
      if (!target) throw notFound('Change', changeId)
      if (target.state !== 'queued') {
        throw stateConflict('only queued changes can be canceled', { state: target.state })
      }
      for (let i = idx; i < bucket.length; i += 1) {
        const candidate = bucket[i]
        if (candidate && candidate.state === 'queued') {
          candidate.state = 'canceled'
          candidate.completed_at = nowIso()
        }
      }
      return json({ ...target })
    }),
    route('post', '/variants/:variantId/slides/:slideId/regenerate', async ({ request, param }) => {
      const body = await readJson(request)
      if (!['shorter', 'other_layout', 'other_chart', 'style:faithful', 'style:balanced', 'style:visual'].includes(String(body.action))) throw validationError('action', 'Неизвестное действие')
      const result = fixture('data', 'regenerate')
      result.variant_id = param('variantId')
      result.slide = { ...object(result.slide), id: param('slideId'), variant_id: param('variantId') }
      return json(result)
    }),
    route('post', '/projects/:projectId/commands', async ({ request, param }) => {
      const projectId = param('projectId')
      const current = state(projectId)
      const body = await readJson(request)
      const rawText = typeof body.text === 'string' ? body.text.trim() : ''
      if (!rawText) throw validationError('text', 'Введите текст команды')
      const lowered = rawText.toLowerCase()
      const briefRules: { pattern: RegExp; field: string; value: unknown; label: string }[] = [
        { pattern: /руководств|совет директор|топ-менедж|инвестор/, field: 'audience', value: 'executive', label: 'аудитория: руководство' },
        { pattern: /клиент|заказчик|партнер|партнёр/, field: 'audience', value: 'clients', label: 'аудитория: клиенты' },
        { pattern: /команд|инженер|аналитик|разработчик/, field: 'audience', value: 'team', label: 'аудитория: команда' },
        { pattern: /защит|бюджет|одобрен|согласован/, field: 'goal', value: 'approval', label: 'цель: согласование решения' },
        { pattern: /отчет|отчёт|итог|результат/, field: 'goal', value: 'report', label: 'цель: отчёт по результатам' },
        { pattern: /продаж|коммерческ|питч/, field: 'goal', value: 'pitch', label: 'цель: коммерческое предложение' },
        { pattern: /строг|формальн|делов/, field: 'style', value: 'strict', label: 'тон: строгий деловой' },
        { pattern: /визуальн|выразительн/, field: 'style', value: 'visual', label: 'стиль: больше визуальных акцентов' },
        { pattern: /плотност.*высок|больше фактов|подробн/, field: 'density', value: 'high', label: 'плотность: подробнее' },
        { pattern: /плотност.*низк|сжато|тезисн|меньше текста/, field: 'density', value: 'low', label: 'плотность: короче и тезиснее' },
      ]
      const slideActions: { pattern: RegExp; intent: 'slide_change' | 'plan_action'; action: string; chart_type?: string; label: string }[] = [
        { pattern: /короче|сократ|ужми/, intent: 'slide_change', action: 'shorter', label: 'сделать слайд короче' },
        { pattern: /другой макет|смени макет|переверст/, intent: 'slide_change', action: 'other_layout', label: 'подобрать другой макет' },
        { pattern: /таблиц/, intent: 'slide_change', action: 'other_chart', chart_type: 'table', label: 'переключить на таблицу' },
        { pattern: /столбчат|bar/, intent: 'slide_change', action: 'other_chart', chart_type: 'bar', label: 'переключить на столбчатый график' },
        { pattern: /линейн|line/, intent: 'slide_change', action: 'other_chart', chart_type: 'line', label: 'переключить на линейный график' },
        { pattern: /другой график|смени график/, intent: 'slide_change', action: 'other_chart', label: 'выбрать другой график' },
        { pattern: /схем|диаграмм/, intent: 'plan_action', action: 'to_diagram', label: 'превратить в схему' },
        { pattern: /убери слайд|удали слайд/, intent: 'plan_action', action: 'remove', label: 'убрать слайд из плана' },
        { pattern: /раздели слайд|разбей слайд/, intent: 'plan_action', action: 'split', label: 'разделить слайд' },
        { pattern: /объедини|слей слайд/, intent: 'plan_action', action: 'merge_next', label: 'объединить со следующим слайдом' },
      ]
      const matchedAction = slideActions.find((rule) => rule.pattern.test(lowered))
      const seenFields = new Set<string>()
      const briefEdits: { field: string; value: unknown; label: string }[] = []
      if (!matchedAction) {
        for (const rule of briefRules) {
          if (!seenFields.has(rule.field) && rule.pattern.test(lowered)) {
            seenFields.add(rule.field)
            briefEdits.push({ field: rule.field, value: rule.value, label: rule.label })
          }
        }
        const countMatch = /(?:до\s+)?(\d{1,2})\s*слайд/.exec(lowered)
        if (countMatch && !/на\s+слайде|слайд\s+\d/.test(lowered)) {
          const count = Number(countMatch[1])
          if (count >= 3 && count <= 40) briefEdits.push({ field: 'slide_count', value: count, label: `объём: ${count} слайдов` })
        }
      }
      if (briefEdits.length > 0) {
        const patch = Object.fromEntries(briefEdits.map((item) => [item.field, item.value]))
        current.session.brief = { ...object(current.session.brief), ...patch }
        const packId = textId(object(current.session.artifacts).content_pack_id)
        if (packId && packs.has(packId)) {
          const packEntry = packs.get(packId)
          if (packEntry) packEntry.understanding.brief = { ...object(packEntry.understanding.brief), ...patch }
        }
        const note = `Принял: ${briefEdits.map((item) => item.label).join('; ')}.`
        return json({
          status: 'applied',
          parser: 'rules',
          command: { intent: 'update_brief', action: 'update_brief', text: rawText, chart_type: null, brief: briefEdits, question: null },
          brief: current.session.brief,
          context_revision: 2,
          changed_fields: briefEdits.map((item) => item.field),
          invalidated: ['plan'],
          assistant_note: note,
        })
      }
      if (matchedAction) {
        const variantId = textId(body.variant_id)
        const planId = textId(body.plan_id) ?? textId(object(current.session.artifacts).plan_id)
        const numberMatch = /слайд[аеу]?\s*№?\s*(\d{1,2})|(\d{1,2})\s*(?:-?[ймго]+\s+)?слайд/.exec(lowered)
        const slideNum = numberMatch ? Number(numberMatch[1] ?? numberMatch[2]) : null
        let slideId = textId(body.slide_id)
        if (!slideId && slideNum !== null && planId && plans.has(planId)) {
          const currentPlan = plan(planId)
          const deck = object(objects(currentPlan.plans)[0]?.deck_plan)
          const found = objects(deck.slides)[slideNum - 1]
          if (found && typeof found.id === 'string') slideId = found.id
        }
        if (!slideId) {
          const question = 'Уточните, к какому слайду применить изменение.'
          return json({
            status: 'clarification',
            parser: 'rules',
            command: { intent: 'clarification', action: matchedAction.action, text: rawText, chart_type: matchedAction.chart_type ?? null, brief: [], question },
            assistant_note: question,
            options: [
              { value: `${rawText} на слайде 1`, label: 'Слайд 1' },
              { value: `${rawText} на слайде 2`, label: 'Слайд 2' },
              { value: `${rawText} на слайде 3`, label: 'Слайд 3' },
            ],
          })
        }
        const commandPayload = { intent: matchedAction.intent, action: matchedAction.action, text: rawText, chart_type: matchedAction.chart_type ?? null, brief: [], question: null }
        if (matchedAction.intent === 'slide_change' && variantId) {
          const jobId = prefixedId('job_chg')
          jobs.set(jobId, { scenario: current.scenario, label: 'change', projectId })
          const expectedRev = typeof body.expected_revision === 'number' ? body.expected_revision : 1
          return json({
            status: 'accepted',
            parser: 'rules',
            command: commandPayload,
            change: {
              id: prefixedId('chg'),
              job_id: jobId,
              variant_id: variantId,
              slide_id: slideId,
              expected_revision: expectedRev,
              target_revision: expectedRev + 1,
              action: matchedAction.action,
              state: 'queued',
              events_url: `/api/v1/jobs/${jobId}/events`,
            },
            assistant_note: `Принял: ${matchedAction.label}.`,
          }, 202)
        }
        if (planId && plans.has(planId)) {
          const currentPlan = plan(planId)
          const deck = object(objects(currentPlan.plans)[0]?.deck_plan)
          const slides = objects(deck.slides)
          const slide = slides.find((item) => item.id === slideId) ?? slides[0]
          if (slide) {
            if (matchedAction.action === 'shorter') {
              slide.content_units = objects(slide.content_units).slice(0, Math.max(1, Math.ceil(objects(slide.content_units).length / 2)))
            } else if (matchedAction.action === 'other_chart' && matchedAction.chart_type) {
              const chart = object(slide.chart)
              slide.chart = { ...chart, type: matchedAction.chart_type }
              slide.desired_visual = matchedAction.chart_type === 'table' ? 'table' : 'chart'
            } else if (matchedAction.action === 'to_diagram') {
              slide.desired_visual = 'diagram'
            } else if (matchedAction.action === 'remove') {
              deck.slides = slides.filter((item) => item !== slide)
            } else if (matchedAction.action === 'split') {
              deck.slides = slides.flatMap((item) => item === slide ? [slide, { ...structuredClone(slide), id: prefixedId('slide') }] : [item])
            } else if (matchedAction.action === 'merge_next') {
              const idx = slides.indexOf(slide)
              const next = slides[idx + 1]
              if (next) {
                slide.content_units = [...objects(slide.content_units), ...objects(next.content_units)]
                deck.slides = slides.filter((item) => item !== next)
              }
            }
            objects(deck.slides).forEach((item, idx) => { item.index = idx })
          }
          return json({
            status: 'applied',
            parser: 'rules',
            command: commandPayload,
            plan: currentPlan,
            assistant_note: `Принял: ${matchedAction.label}.`,
          })
        }
        return json({
          status: 'applied',
          parser: 'rules',
          command: commandPayload,
          assistant_note: `Принял: ${matchedAction.label}.`,
        })
      }
      const hasVariant = Boolean(textId(body.variant_id))
      const hasPlan = Boolean(textId(body.plan_id))
      const question = 'Уточните, что изменить: аудиторию, цель, тон, объём или действие со слайдом («короче», «другой макет», «таблица»).'
      const options = hasVariant
        ? [
            { value: 'Сделай короче', label: 'Сделать короче' },
            { value: 'Другой макет', label: 'Другой макет' },
            { value: 'Переключи на таблицу', label: 'Показать таблицей' },
          ]
        : hasPlan
          ? [
              { value: 'Короче на слайде 2', label: 'Короче на слайде 2' },
              { value: 'Преврати в схему слайд 2', label: 'Схема на слайде 2' },
              { value: 'Раздели слайд 2', label: 'Разделить слайд 2' },
            ]
          : [
              { value: 'Аудитория — руководство', label: 'Для руководства' },
              { value: 'Тон строгий деловой', label: 'Строгий тон' },
              { value: 'Сжато и тезисно', label: 'Короче и тезиснее' },
            ]
      return json({
        status: 'clarification',
        parser: 'rules',
        command: { intent: 'clarification', action: null, text: rawText, chart_type: null, brief: [], question },
        assistant_note: question,
        options,
      })
    }),
  ]
}

function textId(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}


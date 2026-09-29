import type { RequestHandler } from 'msw'
import { REPAIR_HANDLERS } from '../audit'
import type { MockContext } from '../context'
import { AVAILABLE_EXPORT_FORMATS } from '../deliverables'
import { generationFixture, passportFixture, templateDetailFixture, variantFixtures } from '../fixtures'
import { json } from '../http'

const SKILL_VERSION = passportFixture.provenance.skill_version
const PIPELINE_VERSION = passportFixture.provenance.pipeline_version
const PARSER_VERSION = templateDetailFixture.latest_analysis?.parser_version ?? 'unknown'
const PLANNER_VERSION = generationFixture.deck_plan?.provenance.planner ?? 'unknown'
const RULE_VERSION = variantFixtures.balanced.issues[0]?.provenance?.rule_version ?? 'unknown'

const VERSION = {
  service: 'deckdna-api',
  api_version: 'v1',
  version: SKILL_VERSION,
  pipeline_version: PIPELINE_VERSION,
  skill_version: SKILL_VERSION,
  parser_version: PARSER_VERSION,
  planner_version: PLANNER_VERSION,
  rule_version: RULE_VERSION,
  environment: 'mock',
}

const CAPABILITIES = {
  api_version: 'v1',
  environment: 'mock',
  exports: {
    pptx: true,
    pdf: true,
    html: false,
    quality_passport: true,
    formats: AVAILABLE_EXPORT_FORMATS,
  },
  generation: {
    strategies: ['faithful', 'balanced', 'visual'],
    max_variants: 3,
    target_slide_count: { min: 3, max: 40, default: 12 },
    async_jobs: false,
    llm: false,
  },
  audit: {
    deterministic_rules: 24,
    contextual_rules: 10,
    contextual_requires_provider_session: true,
    repairable_rules: [...REPAIR_HANDLERS],
  },
  previews: { slide_png: false, montage: false, pdf: true },
  events: { sse: false, polling: true },
  uploads: {
    max_template_bytes: 200 * 1024 * 1024,
    template_extensions: ['.pptx', '.potx'],
    content_extensions: ['.md', '.txt', '.json', '.docx', '.pdf', '.xlsx'],
  },
  provider_sessions: { list: false, test: true },
}

const SKILL_MANIFEST = {
  name: 'deckdna',
  version: SKILL_VERSION,
  description: 'Compiles a PPTX template, a content pack and a brief into three audited, natively editable deck variants.',
  inputs: [
    { name: 'template', type: 'file', formats: ['pptx', 'potx'], required: true },
    { name: 'content', type: 'file', formats: ['md', 'txt', 'json', 'docx', 'pdf', 'xlsx'], required: true },
    { name: 'brief', type: 'Brief', required: true },
  ],
  outputs: [
    { name: 'variants', type: 'pptx', count: 3 },
    { name: 'audit', type: 'AuditRun' },
    { name: 'exports', type: 'file', formats: AVAILABLE_EXPORT_FORMATS },
  ],
  tools: ['template_autopsy', 'story_director', 'layout_compiler', 'deterministic_audit', 'contextual_audit', 'repair_planner', 'exporter'],
  configs: Object.keys(passportFixture.provenance.config_hashes),
  provenance: { pipeline_version: PIPELINE_VERSION, parser_version: PARSER_VERSION, planner: PLANNER_VERSION, rule_version: RULE_VERSION },
}

export function systemRoutes({ router }: MockContext): RequestHandler[] {
  const { route } = router
  return [
    route('get', '/health/live', () => json({ status: 'ok' })),
    route('get', '/health/ready', () => json({ status: 'ok', storage: 'memory', renderer: 'fixtures', environment: 'mock' })),
    route('get', '/version', () => json(VERSION)),
    route('get', '/capabilities', () => json(CAPABILITIES)),
    route('get', '/skill/manifest', () => json(SKILL_MANIFEST)),
  ]
}

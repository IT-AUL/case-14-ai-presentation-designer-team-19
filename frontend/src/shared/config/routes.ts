export const routePatterns = {
  projects: '/',
  project: '/projects/:projectId',
  start: 'start',
  understanding: 'understanding',
  questions: 'questions',
  template: 'template',
  brief: 'brief',
  planDraft: 'plan',
  style: 'style',
  run: 'runs/:runId',
  plan: 'runs/:runId/plan',
  polish: 'runs/:runId/polish/:variantId?',
  variants: 'runs/:runId/variants',
  audit: 'runs/:runId/audit/:variantId?',
  export: 'runs/:runId/export/:variantId?',
} as const

const project = (projectId: string) => `/projects/${encodeURIComponent(projectId)}`
const run = (projectId: string, runId: string) => `${project(projectId)}/runs/${encodeURIComponent(runId)}`
const withVariant = (base: string, variantId?: string) => (variantId ? `${base}/${encodeURIComponent(variantId)}` : base)

export const routes = {
  projects: () => '/',
  project,
  start: (projectId: string) => `${project(projectId)}/start`,
  understanding: (projectId: string) => `${project(projectId)}/understanding`,
  questions: (projectId: string) => `${project(projectId)}/questions`,
  style: (projectId: string) => `${project(projectId)}/style`,
  template: (projectId: string) => `${project(projectId)}/template`,
  brief: (projectId: string) => `${project(projectId)}/brief`,
  planDraft: (projectId: string) => `${project(projectId)}/plan`,
  run,
  plan: (projectId: string, runId: string) => `${run(projectId, runId)}/plan`,
  polish: (projectId: string, runId: string, variantId?: string) => withVariant(`${run(projectId, runId)}/polish`, variantId),
  variants: (projectId: string, runId: string) => `${run(projectId, runId)}/variants`,
  audit: (projectId: string, runId: string, variantId?: string) => withVariant(`${run(projectId, runId)}/audit`, variantId),
  export: (projectId: string, runId: string, variantId?: string) => withVariant(`${run(projectId, runId)}/export`, variantId),
}

export type ProjectStep = 'template' | 'brief' | 'plan' | 'run' | 'variants' | 'audit' | 'export'

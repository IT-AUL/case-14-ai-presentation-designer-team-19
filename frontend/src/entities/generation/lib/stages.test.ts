import job from '@/shared/api/mocks/fixtures/job-generation.json'
import type { Job } from '../model/types'
import { GENERATION_PIPELINE, jobStatusLabel, PIPELINE_STAGE_LABEL, pipelineStage, stageLabel } from './stages'

describe('generation stage labels', () => {
  it('describes the recorded job, which reports no stage', () => {
    const recorded = job as Job
    expect(recorded.stage).toBeNull()
    expect(stageLabel(recorded.stage)).toBeNull()
    expect(jobStatusLabel(recorded)).toBe('Готово')
  })

  it('maps backend stage names onto pipeline stages', () => {
    expect(pipelineStage('content_ingestion')).toBe('content')
    expect(pipelineStage('deck_plan')).toBe('plan')
    expect(pipelineStage('compose_layout')).toBe('layout')
    expect(pipelineStage('audit.deterministic')).toBe('audit')
    expect(pipelineStage('render_pdf')).toBe('render')
    expect(pipelineStage('quality_passport')).toBe('passport')
    expect(pipelineStage('template_analysis')).toBe('template')
  })

  it('keeps unknown stages readable instead of guessing', () => {
    expect(pipelineStage('warmup')).toBeNull()
    expect(stageLabel('model_warmup')).toBe('Model warmup')
    expect(stageLabel('   ')).toBeNull()
  })

  it('builds status lines for running and failed jobs', () => {
    expect(jobStatusLabel({ state: 'running', stage: 'deck_plan', error: null })).toBe('Идёт: план структуры')
    expect(jobStatusLabel({ state: 'running', stage: null, error: null })).toBe('Идёт')
    expect(jobStatusLabel({ state: 'failed', stage: null, error: { code: 'parse_error', message: 'x', stage: 'content_ingestion' } })).toBe(
      'Ошибка на этапе «разбор контента»',
    )
    expect(jobStatusLabel({ state: 'queued', stage: null, error: null })).toBe('В очереди')
  })

  it('labels every stage of the generation pipeline', () => {
    for (const stage of GENERATION_PIPELINE) expect(PIPELINE_STAGE_LABEL[stage]).toBeTruthy()
  })
})

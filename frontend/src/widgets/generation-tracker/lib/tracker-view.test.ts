import { ApiError } from '@/shared/api'
import { generationFixture } from '@/shared/api/mocks'
import { describeFailure, failureMeta, isTrackingLost } from './failure'
import { autoSelectedKey, resultSummary, sceneProgress } from './scene'
import { variantCards } from './variants'

describe('stage scene', () => {
  const [faithful, balanced, visual] = generationFixture.variants
  if (!faithful || !balanced || !visual) throw new Error('fixture must contain three variants')

  it('explains the current stage in plain words and marks earlier stages done', () => {
    const [card] = variantCards('running', [{ ...balanced, status: 'running', stage: 'compose' }])
    if (!card) throw new Error('card is missing')
    const view = sceneProgress(card)
    expect(view.phrase).toBe('Раскладываем слайды по макетам шаблона')
    expect(view.steps.map((step) => step.state)).toEqual(['done', 'done', 'active', 'pending', 'pending', 'pending'])
    expect(view.steps.map((step) => step.label)).toEqual(['контент', 'план', 'вёрстка', 'аудит', 'рендер', 'паспорт'])
  })

  it('stays calm while the service has not named a stage yet', () => {
    const [card] = variantCards('running', [{ ...balanced, status: 'running', stage: null }])
    if (!card) throw new Error('card is missing')
    expect(sceneProgress(card).phrase).toBe('Собираем колоду')
  })

  it('follows the variant being built, then settles on the recommended one', () => {
    const building = variantCards('running', [
      { ...faithful, status: 'completed' },
      { ...balanced, status: 'running', stage: 'audit' },
      { ...visual, status: 'queued' },
    ])
    expect(autoSelectedKey(building)).toBe(balanced.id)
    const done = variantCards('completed', [faithful, balanced, visual])
    expect(autoSelectedKey(done)).toBe(balanced.id)
  })

  it('summarises a finished variant from its real metrics', () => {
    expect(resultSummary({ metrics: { validity: 1, editability_pei: 3, issues_total: 37, style_fidelity: 0.93, auto_fixed: 29 } })).toEqual({
      styleFidelity: 0.93,
      autoFixed: 29,
      openIssues: 37,
      opensCleanly: true,
    })
    expect(resultSummary({ metrics: null })).toEqual({ styleFidelity: null, autoFixed: null, openIssues: null, opensCleanly: null })
  })
})

describe('variant cards', () => {
  it('shows the three strategies as in progress before the service answers', () => {
    const cards = variantCards('submitting', undefined)
    expect(cards.map((card) => [card.strategy.id, card.state])).toEqual([
      ['faithful', 'pending'],
      ['balanced', 'pending'],
      ['visual', 'pending'],
    ])
  })

  it('follows real variant statuses in strategy order', () => {
    const [faithful, balanced, visual] = generationFixture.variants
    if (!faithful || !balanced || !visual) throw new Error('fixture must contain three variants')
    const cards = variantCards('running', [
      { ...visual, status: 'queued' },
      { ...faithful, status: 'completed' },
      { ...balanced, status: 'running' },
    ])
    expect(cards.map((card) => [card.strategy.id, card.state])).toEqual([
      ['faithful', 'done'],
      ['balanced', 'running'],
      ['visual', 'queued'],
    ])
  })

  it('waits for the in-pipeline audit before calling a variant done', () => {
    const [faithful] = generationFixture.variants
    if (!faithful) throw new Error('fixture must contain a variant')
    expect(variantCards('running', [{ ...faithful, audit_status: 'running' }])[0]?.state).toBe('running')
    expect(variantCards('failed', [{ ...faithful, status: 'failed' }])[0]?.state).toBe('error')
  })

  it('shows no placeholders once a run without variants has stopped', () => {
    expect(variantCards('failed', undefined)).toEqual([])
    expect(variantCards('failed', [])).toEqual([])
  })
})

describe('failure description', () => {
  it('prefers the job error reported by the service', () => {
    const failure = describeFailure({
      jobError: { code: 'llm_timeout', message: 'Модель не вернула план в срок', stage: 'deck_plan' },
      error: null,
      job: undefined,
    })
    expect(failure).toEqual({ code: 'llm_timeout', message: 'Модель не вернула план в срок', stage: 'План структуры', retryable: null })
    expect(failureMeta(failure)).toBe('LLM_TIMEOUT · этап: план структуры')
  })

  it('reads the error envelope of a rejected request', () => {
    const error = new ApiError({ code: 'provider_unavailable', message: 'Провайдер недоступен', status: 503, stage: 'render_pdf', retryable: true })
    const failure = describeFailure({ jobError: null, error, job: undefined })
    expect(failureMeta(failure)).toBe('PROVIDER_UNAVAILABLE · этап: рендер · можно повторить')
  })

  it('falls back to a neutral message', () => {
    expect(describeFailure({ jobError: null, error: null, job: undefined }).message).toBe('Сервис сообщил об ошибке без подробностей.')
    expect(failureMeta({ code: null, message: '', stage: null, retryable: null })).toBeNull()
  })

  it('recognises a lost tracking id', () => {
    expect(isTrackingLost(new ApiError({ code: 'tracking_lost', message: 'x', status: 0 }))).toBe(true)
    expect(isTrackingLost(new Error('tracking_lost'))).toBe(false)
    expect(isTrackingLost(null)).toBe(false)
  })
})

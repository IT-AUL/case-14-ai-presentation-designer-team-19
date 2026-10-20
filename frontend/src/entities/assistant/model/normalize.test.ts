import session from '@/shared/api/mocks/fixtures/assistant/data-session-understood.json'
import intake from '@/shared/api/mocks/fixtures/assistant/data-intake.json'
import understanding from '@/shared/api/mocks/fixtures/assistant/data-understanding.json'
import style from '@/shared/api/mocks/fixtures/assistant/data-style-preview.json'
import chart from '@/shared/api/mocks/fixtures/assistant/data-chart-explain.json'
import brand from '@/shared/api/mocks/fixtures/assistant/data-brand-summary.json'
import regeneration from '@/shared/api/mocks/fixtures/assistant/data-regenerate.json'
import { normalizeAnswers, normalizeBrandbookSummary, normalizeExplain, normalizeInsight, normalizeIntake, normalizeJobEvent, normalizePackEdit, normalizeQuestions, normalizeRegenerate, normalizeSession, normalizeSlide, normalizeStyleAccepted, normalizeStylePreview, normalizeTemplateSummary, normalizeUnderstanding } from './normalize'

it('normalizes session steps without inventing completed steps', () => {
  expect(normalizeSession(session.body)).toMatchObject(session.body)
  expect(normalizeSession({ step: 9, completed_steps: [1, 1, 8, '2'], skipped_steps: [3, -1] })).toMatchObject({ step: 1, completed_steps: [1], skipped_steps: [3] })
})
it('reads recorded intake and preserves rejected and unknown files', () => {
  expect(normalizeIntake(intake.body).files).toEqual(intake.body.files)
  expect(normalizeIntake({ files: [{ name: 'photo.png', kind: 'unexpected', accepted: false, reason: 'Не прочитал' }, {}] }).files).toEqual([{ name: 'photo.png', kind: 'unknown', detected_kind: 'unknown', accepted: false, reason: 'Не прочитал', size_bytes: null }])
})
it('preserves data previews, provenance and context from understanding', () => {
  const normalized = normalizeUnderstanding(understanding.body)
  expect(normalized.datasets[0]?.insights[0]?.fact).toBe(understanding.body.datasets[0]?.insights[0]?.fact)
  expect(normalized.blocks.find((b) => b.kind === 'data')?.preview).toBeDefined()
  expect(normalized.context).toEqual(understanding.body.context)
  expect(normalized.summary.assistant_note).toEqual(understanding.body.summary.assistant_note)
})
it('keeps human insight confirmation and missing interpretation separate', () => {
  expect(normalizeInsight({ id: 'i', confirmed: true, implication: null })).toMatchObject({ confirmed: true, implication: null, meaning: null })
})
it('reads recorded styles, brand conflicts, chart explanation and regeneration', () => {
  expect(normalizeStylePreview(style.body)).toMatchObject({ preview_id: style.body.preview_id, styles: style.body.styles.map((s) => ({ strategy: s.strategy, previews: s.previews })) })
  expect(normalizeBrandbookSummary(brand.body)).toMatchObject(brand.body)
  expect(normalizeExplain(chart.body)).toMatchObject({ dataset: chart.body.dataset, chosen: chart.body.chosen })
  expect(normalizeRegenerate(regeneration.body)).toMatchObject(regeneration.body)
})
it('does not invent a dataset for a slide without a chart', () => {
  expect(normalizeExplain({ dataset: null })).toMatchObject({ dataset: null, chosen: null, alternatives: [], rejected: [] })
})
it('normalizes flattened and nested event payloads and validates sequence', () => {
  expect(normalizeJobEvent({ sequence: 2, event: 'slide.ready', index: 1 })).toMatchObject({ sequence: 2, type: 'slide.ready', data: { index: 1 } })
  expect(normalizeJobEvent({ data: { state: 'completed' } }, 'job.completed', '3')).toMatchObject({ sequence: 3, data: { state: 'completed' } })
  expect(normalizeJobEvent({ sequence: -1, event: 'job.completed' })).toBeNull()
  expect(normalizeJobEvent({ sequence: 1.5, event: 'job.completed' })).toBeNull()
  expect(normalizeJobEvent({ sequence: 1 })).toBeNull()
})
it.each([normalizeIntake, normalizeTemplateSummary, normalizeBrandbookSummary, normalizeUnderstanding, normalizePackEdit, normalizeQuestions, normalizeAnswers, normalizeStyleAccepted, normalizeStylePreview, normalizeRegenerate, normalizeExplain])('handles absent or malformed optional JSON in %s', (normalize) => {
  expect(() => normalize(null)).not.toThrow()
  expect(() => normalize({ blocks: 'invalid', datasets: [null], summary: [], styles: [{}] })).not.toThrow()
})
it('keeps absent slide and edited understanding absent', () => {
  expect(normalizeSlide({})).toBeNull()
  expect(normalizePackEdit({ content_pack_id: 'new', filled_from: 'old' })).toEqual({ content_pack_id: 'new', filled_from: 'old', understanding: null })
})

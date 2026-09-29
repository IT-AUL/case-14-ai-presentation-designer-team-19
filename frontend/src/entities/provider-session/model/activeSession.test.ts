import type { ProviderSession } from './types'
import { clearActiveProviderSession, getActiveProviderSession, recordProviderSessionTest, setActiveProviderSession } from './activeSession'

const session: ProviderSession = {
  id: 'ps_1',
  label: 'VK Inference',
  base_url: 'https://inference.example/v1',
  models: { text: 'Qwen/Qwen3-32B', vision: 'Qwen/Qwen2.5-VL-32B-Instruct' },
  capabilities: { structured_output: true, tool_calls: false, image_input: true, embeddings: false },
  created_at: '2026-09-25T10:00:00Z',
  expires_at: '2999-01-01T00:00:00Z',
}

describe('active provider session', () => {
  afterEach(() => clearActiveProviderSession())

  it('stores the session without any secret', () => {
    setActiveProviderSession({ ...session, api_token: 'secret-token' } as ProviderSession)
    const raw = window.sessionStorage.getItem('deckdna.provider-session.v1') ?? ''
    expect(raw).not.toContain('secret-token')
    expect(getActiveProviderSession()?.id).toBe('ps_1')
  })

  it('records test results only for the active session', () => {
    setActiveProviderSession(session)
    recordProviderSessionTest({ session_id: 'ps_other', results: [], tested_at: '2026-09-25T10:01:00Z' })
    expect(getActiveProviderSession()?.lastTest).toBeNull()
    recordProviderSessionTest({ session_id: 'ps_1', results: [{ capability: 'structured_output', status: 'ok' }], tested_at: '2026-09-25T10:01:00Z' })
    expect(getActiveProviderSession()?.lastTest?.results).toHaveLength(1)
  })

  it('hides expired sessions', () => {
    setActiveProviderSession({ ...session, expires_at: '2000-01-01T00:00:00Z' })
    expect(getActiveProviderSession()).toBeNull()
  })

  it('keeps a different session when clearing by id', () => {
    setActiveProviderSession(session)
    clearActiveProviderSession('ps_other')
    expect(getActiveProviderSession()?.id).toBe('ps_1')
  })
})

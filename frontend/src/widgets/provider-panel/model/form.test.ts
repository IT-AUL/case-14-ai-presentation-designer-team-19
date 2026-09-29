import { EMPTY_PROVIDER_FORM, providerFormFromSession, toProviderSessionCreate, validateProviderForm } from './form'
import type { ProviderForm } from './form'

const valid: ProviderForm = {
  ...EMPTY_PROVIDER_FORM,
  label: '',
  baseUrl: ' https://inference.example/v1/ ',
  apiToken: ' token ',
  textModel: 'Qwen/Qwen3-32B',
  visionModel: 'Qwen/Qwen2.5-VL-32B-Instruct',
}

describe('provider form validation', () => {
  it('requires the address, token and both models', () => {
    expect(validateProviderForm(EMPTY_PROVIDER_FORM)).toEqual({
      baseUrl: 'Укажите адрес OpenAI-совместимого API',
      apiToken: 'Нужен токен доступа',
      textModel: 'Укажите модель для текста',
      visionModel: 'Укажите модель для картинок слайдов',
    })
    expect(validateProviderForm(valid)).toEqual({})
  })

  it('accepts only absolute http(s) addresses', () => {
    expect(validateProviderForm({ ...valid, baseUrl: 'inference.example/v1' }).baseUrl).toBe('Нужен полный адрес http(s)://…')
    expect(validateProviderForm({ ...valid, baseUrl: 'ftp://inference.example' }).baseUrl).toBe('Нужен полный адрес http(s)://…')
  })

  it('rejects models above the case limits', () => {
    const errors = validateProviderForm({
      ...valid,
      textModel: 'openai/gpt-oss-120b',
      visionModel: 'Qwen/Qwen2.5-VL-72B-Instruct',
      imageModel: 'some-t2i-24B',
      embeddingModel: 'BAAI/bge-m3',
    })
    expect(errors.textModel).toMatch(/35B/)
    expect(errors.visionModel).toMatch(/35B/)
    expect(errors.imageModel).toMatch(/20B/)
    expect(errors.embeddingModel).toBeUndefined()
  })
})

describe('provider session body', () => {
  it('maps the form to the create request', () => {
    expect(toProviderSessionCreate(valid)).toEqual({
      label: 'inference.example',
      base_url: 'https://inference.example/v1',
      api_token: 'token',
      models: { text: 'Qwen/Qwen3-32B', vision: 'Qwen/Qwen2.5-VL-32B-Instruct', embedding: null, image: null },
      capabilities: { structured_output: true, tool_calls: false, image_input: true, embeddings: false },
      timeout_seconds: 90,
      max_concurrency: 4,
      ttl_seconds: 14_400,
    })
  })

  it('declares embeddings only with an embedding model', () => {
    const body = toProviderSessionCreate({ ...valid, label: 'VK Inference', embeddingModel: 'BAAI/bge-m3' })
    expect(body.label).toBe('VK Inference')
    expect(body.models.embedding).toBe('BAAI/bge-m3')
    expect(body.capabilities.embeddings).toBe(true)
  })

  it('prefills an edit form without the token', () => {
    const form = providerFormFromSession({
      id: 'ps_1',
      label: 'VK Inference',
      baseUrl: 'https://inference.example/v1',
      models: { text: 'Qwen/Qwen3-32B', vision: 'Qwen/Qwen2.5-VL-32B-Instruct', embedding: null },
      capabilities: { structured_output: true, tool_calls: false, image_input: true, embeddings: false },
      createdAt: '2026-09-25T10:00:00Z',
      expiresAt: '2999-01-01T00:00:00Z',
      lastTest: null,
    })
    expect(form).toMatchObject({ label: 'VK Inference', apiToken: '', embeddingModel: '', imageModel: '' })
  })
})

import { DEFAULT_PROVIDER_CAPABILITIES, DEFAULT_SESSION_LIMITS, exceedsParameterLimit, MODEL_PARAMETER_LIMIT_B } from '@/entities/provider-session'
import type { ActiveProviderSession, ModelRole, ProviderSessionCreate } from '@/entities/provider-session'

export interface ProviderForm {
  label: string
  baseUrl: string
  apiToken: string
  textModel: string
  visionModel: string
  embeddingModel: string
  imageModel: string
}

export type ProviderFormField = keyof ProviderForm
export type ProviderFormErrors = Partial<Record<ProviderFormField, string>>

export const MODEL_FIELDS: readonly { field: ProviderFormField; role: ModelRole }[] = [
  { field: 'textModel', role: 'text' },
  { field: 'visionModel', role: 'vision' },
  { field: 'embeddingModel', role: 'embedding' },
  { field: 'imageModel', role: 'image' },
]

export const EMPTY_PROVIDER_FORM: ProviderForm = {
  label: '',
  baseUrl: '',
  apiToken: '',
  textModel: '',
  visionModel: '',
  embeddingModel: '',
  imageModel: '',
}

export function providerFormFromSession(session: ActiveProviderSession): ProviderForm {
  return {
    label: session.label,
    baseUrl: session.baseUrl,
    apiToken: '',
    textModel: session.models.text,
    visionModel: session.models.vision,
    embeddingModel: session.models.embedding ?? '',
    imageModel: session.models.image ?? '',
  }
}

export function parameterLimitMessage(role: ModelRole): string {
  return `Больше ${MODEL_PARAMETER_LIMIT_B[role]}B параметров — такая модель нарушает условия кейса`
}

function parseUrl(value: string): URL | null {
  try {
    return new URL(value)
  } catch {
    return null
  }
}

function normalizeBaseUrl(value: string): string | null {
  const trimmed = value.trim()
  const url = parseUrl(trimmed)
  if (!url) return null
  if (url.protocol !== 'http:' && url.protocol !== 'https:') return null
  return trimmed.replace(/\/+$/, '')
}

export function validateProviderForm(form: ProviderForm): ProviderFormErrors {
  const errors: ProviderFormErrors = {}
  if (!form.baseUrl.trim()) errors.baseUrl = 'Укажите адрес OpenAI-совместимого API'
  else if (!normalizeBaseUrl(form.baseUrl)) errors.baseUrl = 'Нужен полный адрес http(s)://…'
  if (!form.apiToken.trim()) errors.apiToken = 'Нужен токен доступа'
  if (!form.textModel.trim()) errors.textModel = 'Укажите модель для текста'
  if (!form.visionModel.trim()) errors.visionModel = 'Укажите модель для картинок слайдов'
  for (const { field, role } of MODEL_FIELDS) {
    const model = form[field].trim()
    if (model && exceedsParameterLimit(model, role)) errors[field] = parameterLimitMessage(role)
  }
  return errors
}

function sessionLabel(form: ProviderForm, baseUrl: string): string {
  const label = form.label.trim()
  if (label) return label
  return parseUrl(baseUrl)?.host ?? baseUrl
}

export function toProviderSessionCreate(form: ProviderForm): ProviderSessionCreate {
  const baseUrl = normalizeBaseUrl(form.baseUrl) ?? form.baseUrl.trim()
  const embedding = form.embeddingModel.trim() || null
  const image = form.imageModel.trim() || null
  return {
    label: sessionLabel(form, baseUrl),
    base_url: baseUrl,
    api_token: form.apiToken.trim(),
    models: { text: form.textModel.trim(), vision: form.visionModel.trim(), embedding, image },
    capabilities: { ...DEFAULT_PROVIDER_CAPABILITIES, embeddings: Boolean(embedding) },
    timeout_seconds: DEFAULT_SESSION_LIMITS.timeout_seconds,
    max_concurrency: DEFAULT_SESSION_LIMITS.max_concurrency,
    ttl_seconds: DEFAULT_SESSION_LIMITS.ttl_seconds,
  }
}

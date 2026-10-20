import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { QueryClient, QueryKey } from '@tanstack/react-query'
import { api, isUnsupportedEndpoint, requestOptionalJson, unwrap, uploadWithProgress } from '@/shared/api'
import { API_BASE } from '@/shared/config'
import { normalizeAnswers, normalizeBrandbookSummary, normalizeCommandResult, normalizeExplain, normalizeIntake, normalizePackEdit, normalizeQuestions, normalizeRegenerate, normalizeSession, normalizeSlide, normalizeStyleAccepted, normalizeStylePreview, normalizeTemplateSummary, normalizeUnderstanding, record } from '../model/normalize'
import type { AnswersBody, AssistantCommandRequest, AutopilotRequest, BlockOps, BrandPriority, InsightPatch, IntakeKind, NotesBody, PackEditResult, PlanSet, PlanSlideAction, PlanSlidePatch, RegenerateRequest, StylePreviewRequest } from '../model/types'
import { abortable } from '../lib/abortable'
import { assistantKeys } from './keys'

type Id = string | null | undefined

async function optional<T>(request: Promise<T>): Promise<T | null> {
  try { return await request } catch (error) {
    if (isUnsupportedEndpoint(error)) return null
    throw error
  }
}
async function normalized<T>(request: Promise<unknown>, normalize: (value: unknown) => T): Promise<T | null> {
  const result = await optional(request)
  return result === null ? null : normalize(result)
}
async function invalidate(client: QueryClient, keys: readonly QueryKey[]) {
  await Promise.all(keys.map((queryKey) => client.invalidateQueries({ queryKey })))
}
const contentKeys = (packId: string): QueryKey[] => [assistantKeys.sessions, ['assistant', 'questions'], assistantKeys.understanding(packId), ['content-pack', packId], ['assistant', 'plan'], ['plan']]
const planKeys = (planId: string): QueryKey[] => [assistantKeys.sessions, assistantKeys.plan(planId), ['plan'], ['assistant', 'style-preview']]

export function useAssistantSession(projectId: Id) {
  return useQuery({ queryKey: assistantKeys.session(projectId ?? ''), enabled: Boolean(projectId), queryFn: ({ signal }) => normalized(unwrap(api.GET('/api/v1/projects/{project_id}/session', { params: { path: { project_id: projectId as string } }, signal })), normalizeSession), staleTime: 0, refetchInterval: (q) => q.state.data?.job_id ? 2000 : false })
}
export function useStartAutopilot(projectId: string) {
  const client = useQueryClient()
  return useMutation({ mutationFn: (body: AutopilotRequest) => optional(unwrap(api.POST('/api/v1/projects/{project_id}/session/autopilot', { params: { path: { project_id: projectId } }, body }))), onSuccess: (data) => data ? invalidate(client, [assistantKeys.session(projectId), ['job', data.job_id]]) : undefined })
}
export interface IntakeInput {
  files: File[]
  idea?: string
  onProgress?: (fraction: number) => void
  signal?: AbortSignal
}
export function useIntake(projectId: string) {
  const client = useQueryClient()
  const [progress, setProgress] = useState(0)
  const uploadId = useRef(0)
  const mutation = useMutation({
    mutationFn: async ({ files, idea, onProgress, signal }: IntakeInput) => {
      signal?.throwIfAborted()
      const id = ++uploadId.current
      setProgress(0)
      const form = new FormData()
      files.forEach((file) => form.append('files', file, file.name))
      if (idea !== undefined) form.append('idea', idea)
      const response = await normalized(abortable(uploadWithProgress<unknown>({ url: `${API_BASE}/projects/${encodeURIComponent(projectId)}/intake`, form, signal, onProgress: (fraction) => { if (uploadId.current === id) setProgress(fraction); onProgress?.(fraction) } }), signal), normalizeIntake)
      if (response && uploadId.current === id) setProgress(1)
      return response
    },
    onSuccess: (data) => data ? invalidate(client, [assistantKeys.session(projectId), assistantKeys.questions(projectId), ['project', projectId], ...contentKeys(data.content_pack_id ?? ''), ['assistant', 'template'], ['assistant', 'brandbook']]) : undefined,
  })
  return { ...mutation, progress, reset: () => { uploadId.current++; setProgress(0); mutation.reset() } }
}
export function useSetIntakeKind(projectId: string) {
  const client = useQueryClient()
  return useMutation({ mutationFn: ({ name, kind }: { name: string; kind: IntakeKind }) => normalized(unwrap(api.PATCH('/api/v1/projects/{project_id}/intake/files/{name}', { params: { path: { project_id: projectId, name } }, body: { kind } })), normalizeIntake), onSuccess: (data) => data ? invalidate(client, [assistantKeys.session(projectId), ['project', projectId], ...contentKeys(data.content_pack_id ?? ''), ['assistant', 'template'], ['assistant', 'brandbook']]) : undefined })
}
export function useTemplateSummary(templateId: Id) {
  return useQuery({ queryKey: assistantKeys.templateSummary(templateId ?? ''), enabled: Boolean(templateId), queryFn: ({ signal }) => normalized(unwrap(api.GET('/api/v1/templates/{template_id}/summary', { params: { path: { template_id: templateId as string } }, signal })), normalizeTemplateSummary) })
}
export function useBrandbook(brandbookId: Id) {
  return useQuery({ queryKey: assistantKeys.brandbook(brandbookId ?? ''), enabled: Boolean(brandbookId), queryFn: async ({ signal }) => normalized(unwrap(api.GET('/api/v1/brandbooks/{brandbook_id}', { params: { path: { brandbook_id: brandbookId as string } }, signal })), record) })
}
export function useBrandbookSummary(brandbookId: Id) {
  return useQuery({ queryKey: assistantKeys.brandbookSummary(brandbookId ?? ''), enabled: Boolean(brandbookId), queryFn: ({ signal }) => normalized(unwrap(api.GET('/api/v1/brandbooks/{brandbook_id}/summary', { params: { path: { brandbook_id: brandbookId as string } }, signal })), normalizeBrandbookSummary) })
}
export function useSetBrandPriority(brandbookId: string) {
  const client = useQueryClient()
  return useMutation({ mutationFn: (body: { priority: BrandPriority }) => normalized(unwrap(api.PATCH('/api/v1/brandbooks/{brandbook_id}', { params: { path: { brandbook_id: brandbookId } }, body })), normalizeBrandbookSummary), onSuccess: async (data) => {
    if (!data) return
    await invalidate(client, [assistantKeys.sessions, assistantKeys.brandbook(brandbookId), ['assistant', 'style-preview']])
    client.setQueryData(assistantKeys.brandbookSummary(brandbookId), data)
  } })
}
export function useUnderstanding(packId: Id, { useLlm = false }: { useLlm?: boolean } = {}) {
  const client = useQueryClient()
  return useQuery({
    queryKey: [...assistantKeys.understanding(packId ?? ''), { useLlm }], enabled: Boolean(packId), staleTime: 60_000, retry: false, refetchOnWindowFocus: false,
    queryFn: async ({ signal }) => {
      const data = await normalized(unwrap(api.GET('/api/v1/content-packs/{pack_id}/understanding', { params: { path: { pack_id: packId as string }, query: { use_llm: useLlm } }, signal })), normalizeUnderstanding)
      if (data) await invalidate(client, [assistantKeys.sessions])
      return data
    },
    refetchInterval: (q) => q.state.data?.context?.status === 'pending' || q.state.data?.interpretation?.status === 'pending' ? 2000 : false,
  })
}
function usePackEditSuccess(packId: string) {
  const client = useQueryClient()
  return async (data: PackEditResult | null) => {
    if (!data) return
    await invalidate(client, [...contentKeys(packId), ...(data.content_pack_id ? contentKeys(data.content_pack_id) : [])])
  }
}
export function useEditBlocks(packId: string) {
  const onSuccess = usePackEditSuccess(packId)
  return useMutation({ mutationFn: (body: BlockOps) => normalized(unwrap(api.PATCH('/api/v1/content-packs/{pack_id}/blocks', { params: { path: { pack_id: packId } }, body })), normalizePackEdit), onSuccess })
}
export function useEditInsight(packId: string) {
  const client = useQueryClient()
  return useMutation({ mutationFn: ({ insightId, ...body }: InsightPatch & { insightId: string }) => optional(unwrap(api.PATCH('/api/v1/content-packs/{pack_id}/insights/{insight_id}', { params: { path: { pack_id: packId, insight_id: insightId } }, body }))), onSuccess: (data) => data ? invalidate(client, contentKeys(packId)) : undefined })
}
export function useAddNotes(packId: string) {
  const onSuccess = usePackEditSuccess(packId)
  return useMutation({ mutationFn: (body: NotesBody) => normalized(unwrap(api.POST('/api/v1/content-packs/{pack_id}/notes', { params: { path: { pack_id: packId } }, body })), normalizePackEdit), onSuccess })
}
export function useQuestions(projectId: Id) {
  return useQuery({ queryKey: assistantKeys.questions(projectId ?? ''), enabled: Boolean(projectId), queryFn: ({ signal }) => normalized(unwrap(api.GET('/api/v1/projects/{project_id}/questions', { params: { path: { project_id: projectId as string } }, signal })), normalizeQuestions) })
}
export function useSubmitAnswers(projectId: string) {
  const client = useQueryClient()
  return useMutation({ mutationFn: (body: AnswersBody) => normalized(unwrap(api.POST('/api/v1/projects/{project_id}/answers', { params: { path: { project_id: projectId } }, body })), normalizeAnswers), onSuccess: (data) => data ? invalidate(client, [assistantKeys.session(projectId), assistantKeys.questions(projectId), ['assistant', 'understanding'], ['assistant', 'plan'], ['plan']]) : undefined })
}
function usePlanSuccess(planId: string) {
  const client = useQueryClient()
  return async (data: PlanSet | null) => {
    if (!data) return
    await invalidate(client, [...planKeys(planId), ...contentKeys(data.content_pack_id)])
    client.setQueryData(assistantKeys.plan(planId), data)
  }
}
export function usePatchPlanSlide(planId: string) {
  const onSuccess = usePlanSuccess(planId)
  return useMutation({ mutationFn: ({ slideId, ...body }: PlanSlidePatch & { slideId: string }) => optional(unwrap(api.PATCH('/api/v1/plans/{plan_id}/slides/{slide_id}', { params: { path: { plan_id: planId, slide_id: slideId } }, body }))), onSuccess })
}
export function usePlanSlideAction(planId: string) {
  const onSuccess = usePlanSuccess(planId)
  return useMutation({ mutationFn: ({ slideId, ...body }: PlanSlideAction & { slideId: string }) => optional(unwrap(api.POST('/api/v1/plans/{plan_id}/slides/{slide_id}/actions', { params: { path: { plan_id: planId, slide_id: slideId } }, body }))), onSuccess })
}
export function useStartStylePreviews(planId: string) {
  const client = useQueryClient()
  return useMutation({ mutationFn: (body: Partial<StylePreviewRequest> = {}) => normalized(unwrap(api.POST('/api/v1/plans/{plan_id}/style-previews', { params: { path: { plan_id: planId } }, body: { ...body, respond_async: true } })), normalizeStyleAccepted), onSuccess: (data) => data ? invalidate(client, [assistantKeys.sessions, assistantKeys.stylePreview(data.preview_id ?? '')]) : undefined })
}
export function useStylePreview(previewId: Id) {
  return useQuery({ queryKey: assistantKeys.stylePreview(previewId ?? ''), enabled: Boolean(previewId), queryFn: ({ signal }) => normalized(unwrap(api.GET('/api/v1/style-previews/{preview_id}', { params: { path: { preview_id: previewId as string } }, signal })), normalizeStylePreview) })
}
export function useRegenerateSlide(variantId: string) {
  const client = useQueryClient()
  return useMutation({ mutationFn: ({ slideId, ...body }: RegenerateRequest & { slideId: string }) => normalized(unwrap(api.POST('/api/v1/variants/{variant_id}/slides/{slide_id}/regenerate', { params: { path: { variant_id: variantId, slide_id: slideId } }, body })), normalizeRegenerate), onSuccess: async (data) => {
    if (!data) return
    await invalidate(client, [assistantKeys.sessions, ['variant'], ['generation'], ['audit'], ['export'], ['passport'], ['pdf'], ['assistant', 'slide']])
    if (data.slide) client.setQueryData(assistantKeys.slide(data.slide.id), data.slide)
  } })
}
export function useSlideExplain(slideId: Id) {
  return useQuery({ queryKey: assistantKeys.slideExplain(slideId ?? ''), enabled: Boolean(slideId), queryFn: ({ signal }) => normalized(unwrap(api.GET('/api/v1/slides/{slide_id}/explain', { params: { path: { slide_id: slideId as string } }, signal })), normalizeExplain) })
}
export function useSlide(slideId: Id) {
  return useQuery({ queryKey: assistantKeys.slide(slideId ?? ''), enabled: Boolean(slideId), queryFn: ({ signal }) => normalized(unwrap(api.GET('/api/v1/slides/{slide_id}', { params: { path: { slide_id: slideId as string } }, signal })), normalizeSlide) })
}
export function useAssistantCommand(projectId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: AssistantCommandRequest) => normalized(requestOptionalJson<unknown>(`/projects/${encodeURIComponent(projectId)}/commands`, { method: 'POST', body }), normalizeCommandResult),
    onSuccess: async (data) => {
      if (!data || data.status === 'clarification') return
      if (data.plan) client.setQueryData(assistantKeys.plan(data.plan.plan_id), data.plan)
      const keys: QueryKey[] = [assistantKeys.session(projectId), assistantKeys.questions(projectId), ['assistant', 'understanding'], ['content-pack'], ['assistant', 'plan'], ['plan'], ['assistant', 'style-preview'], ['variant'], ['generation'], ['audit'], ['export'], ['passport'], ['pdf'], ['assistant', 'slide']]
      if (data.change?.job_id) keys.push(['job', data.change.job_id])
      await invalidate(client, keys)
    },
  })
}


import { useMutation } from '@tanstack/react-query'
import { uploadContentPack } from '@/entities/content-pack'
import { startGeneration, whenGenerationAccepted } from '@/entities/generation'
import { requestAndStorePlan } from '@/features/edit-deck-plan'
import { rememberRun, useInvalidateProject } from '@/entities/project'
import { resolveContent, toBrief, toGenerationBody } from '../lib/brief'
import type { BriefForm, ParsedContent } from './form'

export interface ContentUploadRequest {
  file: File
  key: string
  label: string
  brief: Record<string, unknown>
}

async function uploadContent(projectId: string, { file, key, label, brief }: ContentUploadRequest): Promise<ParsedContent> {
  const accepted = await uploadContentPack(projectId, [file], brief)
  if (accepted.job.state === 'failed') throw new Error(accepted.job.error?.message ?? 'Не удалось разобрать контент')
  return { packId: accepted.content_pack.id, sourceKey: key, label, sizeBytes: file.size }
}

export function useContentUpload(projectId: string) {
  const invalidateProject = useInvalidateProject()
  return useMutation({
    mutationFn: (request: ContentUploadRequest) => uploadContent(projectId, request),
    onSuccess: () => invalidateProject(projectId),
  })
}

export interface SubmitBriefRequest {
  form: BriefForm
  files: readonly File[]
  templateId: string
  providerSessionId: string | null
  onParsed?: (parsed: ParsedContent) => void
}

async function prepareContent(projectId: string, { form, files, onParsed }: Pick<SubmitBriefRequest, 'form' | 'files' | 'onParsed'>): Promise<string> {
  const brief = toBrief(form)
  const plan = resolveContent({ mode: form.contentMode, files, text: form.text, parsed: form.parsed })
  if (plan.kind === 'missing') throw new Error('Добавьте контент: файл или текст')
  if (plan.kind !== 'upload') return plan.packId
  const parsed = await uploadContent(projectId, { file: plan.file, key: plan.key, label: plan.label, brief })
  onParsed?.(parsed)
  return parsed.packId
}

export function usePlanFirst(projectId: string) {
  const invalidateProject = useInvalidateProject()
  return useMutation({
    mutationFn: async (request: SubmitBriefRequest): Promise<void> => {
      const { form, templateId, providerSessionId } = request
      const contentPackId = await prepareContent(projectId, request)
      const { variants, ...body } = toGenerationBody({ brief: toBrief(form), templateId, contentPackId, useLlm: form.useLlm, providerSessionId })
      await requestAndStorePlan(projectId, { ...body, strategies: variants.map((variant) => variant.strategy) })
      void invalidateProject(projectId)
    },
  })
}

export function useSubmitBrief(projectId: string) {
  const invalidateProject = useInvalidateProject()
  return useMutation({
    mutationFn: async (request: SubmitBriefRequest): Promise<string> => {
      const { form, templateId, providerSessionId } = request
      const brief = toBrief(form)
      const contentPackId = await prepareContent(projectId, request)
      const trackingId = startGeneration(projectId, toGenerationBody({ brief, templateId, contentPackId, useLlm: form.useLlm, providerSessionId }))
      whenGenerationAccepted(trackingId)?.then(
        (accepted) => rememberRun(projectId, accepted.generation_id),
        () => undefined,
      )
      void invalidateProject(projectId)
      return trackingId
    },
  })
}

import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useRef, useState } from 'react'
import { useInvalidateProject } from '@/entities/project'
import { analyzeTemplate, templateKeys, uploadTemplate } from '@/entities/template'
import { isApiError } from '@/shared/api'
import { validateTemplateFile } from '../lib/validateTemplateFile'

export interface UploadedFileMeta {
  name: string
  size: number
}

export type TemplateUploadState =
  | { phase: 'idle' }
  | { phase: 'uploading'; file: UploadedFileMeta; progress: number }
  | { phase: 'analyzing'; file: UploadedFileMeta; templateId: string }
  | { phase: 'error'; message: string; file?: UploadedFileMeta; templateId?: string }

const ERROR_TEXT: Record<string, string> = {
  payload_too_large: 'Файл больше, чем принимает сервер: предел — 200 МБ.',
  unsupported_media_type: 'Сервер принимает только .pptx и .potx.',
  invalid_template: 'Файл не открылся как пакет PowerPoint: возможно, он повреждён или это не .pptx.',
  network_error: 'Сервис недоступен. Попробуйте ещё раз.',
}

function describeError(error: unknown, fallback: string): string {
  if (!isApiError(error)) return fallback
  if (error.status === 413) return ERROR_TEXT.payload_too_large as string
  return ERROR_TEXT[error.code] ?? (error.message ? `${fallback} ${error.message}` : fallback)
}

export function useTemplateUpload(projectId: string) {
  const queryClient = useQueryClient()
  const invalidateProject = useInvalidateProject()
  const [state, setState] = useState<TemplateUploadState>({ phase: 'idle' })
  const runRef = useRef(0)

  const runAnalysis = useCallback(
    async (templateId: string, file: UploadedFileMeta, run: number): Promise<boolean> => {
      setState({ phase: 'analyzing', file, templateId })
      try {
        await analyzeTemplate(templateId)
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: templateKeys.detail(templateId) }),
          queryClient.invalidateQueries({ queryKey: templateKeys.dna(templateId) }),
          invalidateProject(projectId),
        ])
        if (runRef.current !== run) return false
        setState({ phase: 'idle' })
        return true
      } catch (error) {
        if (runRef.current === run) {
          setState({ phase: 'error', message: describeError(error, 'Не удалось разобрать шаблон.'), file, templateId })
        }
        return false
      }
    },
    [invalidateProject, projectId, queryClient],
  )

  const upload = useCallback(
    async (file: File): Promise<boolean> => {
      const run = ++runRef.current
      const meta = { name: file.name, size: file.size }
      const invalid = validateTemplateFile(file)
      if (invalid) {
        setState({ phase: 'error', message: invalid })
        return false
      }
      setState({ phase: 'uploading', file: meta, progress: 0 })
      try {
        const asset = await uploadTemplate(projectId, file, (progress) => {
          if (runRef.current === run) setState({ phase: 'uploading', file: meta, progress })
        })
        if (runRef.current !== run) return false
        if (asset.validation_status === 'invalid') {
          setState({ phase: 'error', message: `Файл «${file.name}» не прошёл проверку: это не корректный пакет PPTX/POTX.`, file: meta })
          return false
        }
        return await runAnalysis(asset.id, meta, run)
      } catch (error) {
        if (runRef.current === run) setState({ phase: 'error', message: describeError(error, 'Не удалось загрузить файл.'), file: meta })
        return false
      }
    },
    [projectId, runAnalysis],
  )

  const analyze = useCallback((templateId: string, file: UploadedFileMeta) => runAnalysis(templateId, file, ++runRef.current), [runAnalysis])

  const reset = useCallback(() => {
    runRef.current += 1
    setState({ phase: 'idle' })
  }, [])

  return { state, upload, analyze, reset }
}

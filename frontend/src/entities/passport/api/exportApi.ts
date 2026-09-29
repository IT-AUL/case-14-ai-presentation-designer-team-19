import { useMutation, useQueries, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query'
import { api, isApiError, unwrap, type ApiError } from '@/shared/api'
import { parsePassport } from '../lib/passport'
import type { CreateExportResult, ExportFormat, ExportRecord, QualityPassport } from '../model/types'

export const exportKeys = {
  all: ['export'] as const,
  detail: (exportId: string) => ['export', exportId] as const,
  passport: (artifactId: string) => ['passport', artifactId] as const,
}

export function isNotImplemented(error: unknown): error is ApiError {
  return isApiError(error) && (error.status === 501 || error.code === 'not_implemented')
}

function fetchExport(exportId: string): Promise<ExportRecord> {
  return unwrap(api.GET('/api/v1/exports/{export_id}', { params: { path: { export_id: exportId } } }))
}

export async function fetchPassport(artifactId: string): Promise<QualityPassport> {
  const raw: unknown = await unwrap(api.GET('/api/v1/artifacts/{artifact_id}/download', { params: { path: { artifact_id: artifactId } } }))
  return parsePassport(raw)
}

export function useExport(exportId: string | null | undefined) {
  return useQuery({
    queryKey: exportKeys.detail(exportId ?? ''),
    queryFn: () => fetchExport(exportId as string),
    enabled: Boolean(exportId),
  })
}

export interface ExportsState {
  data: ExportRecord[]
  isPending: boolean
  error: Error | null
}

function combineExports(results: readonly UseQueryResult<ExportRecord>[]): ExportsState {
  return {
    data: results.flatMap((result) => (result.data ? [result.data] : [])),
    isPending: results.some((result) => result.isPending),
    error: results.find((result) => result.error)?.error ?? null,
  }
}

export function useExports(exportIds: readonly string[]): ExportsState {
  return useQueries({
    queries: exportIds.map((exportId) => ({
      queryKey: exportKeys.detail(exportId),
      queryFn: () => fetchExport(exportId),
    })),
    combine: combineExports,
  })
}

export async function createExport(variantId: string, formats: ExportFormat[]): Promise<CreateExportResult> {
  try {
    const accepted = await unwrap(api.POST('/api/v1/variants/{variant_id}/exports', { params: { path: { variant_id: variantId } }, body: { formats } }))
    const record = await fetchExport(accepted.export_id).catch(() => null)
    return { status: 'created', formats, exportId: accepted.export_id, jobId: accepted.job_id, record }
  } catch (error) {
    if (isNotImplemented(error)) return { status: 'unavailable', formats, message: error.message, error }
    throw error
  }
}

export function useCreateExport(variantId: string) {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (formats: ExportFormat[]) => createExport(variantId, formats),
    onSuccess: async (result) => {
      if (result.status !== 'created') return
      if (result.record) queryClient.setQueryData(exportKeys.detail(result.exportId), result.record)
      await queryClient.invalidateQueries({ queryKey: ['variant', variantId] })
    },
  })
}

export function usePassport(artifactId: string | null | undefined) {
  return useQuery({
    queryKey: exportKeys.passport(artifactId ?? ''),
    queryFn: () => fetchPassport(artifactId as string),
    enabled: Boolean(artifactId),
    staleTime: Infinity,
  })
}

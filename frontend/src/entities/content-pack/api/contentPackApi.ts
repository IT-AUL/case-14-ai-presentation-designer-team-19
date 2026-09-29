import { useQuery } from '@tanstack/react-query'
import { api, unwrap, uploadWithProgress } from '@/shared/api'
import { API_BASE } from '@/shared/config'
import type { ContentPackAccepted } from '../model/types'

export const contentPackKeys = {
  detail: (packId: string) => ['content-pack', packId] as const,
}

export function useContentPack(packId: string | null | undefined) {
  return useQuery({
    queryKey: contentPackKeys.detail(packId ?? ''),
    queryFn: () => unwrap(api.GET('/api/v1/content-packs/{pack_id}', { params: { path: { pack_id: packId as string } } })),
    enabled: Boolean(packId),
  })
}

export function uploadContentPack(projectId: string, files: File[], brief: Record<string, unknown>): Promise<ContentPackAccepted> {
  const form = new FormData()
  for (const file of files) form.append('files', file, file.name)
  form.append('brief', JSON.stringify(brief))
  return uploadWithProgress<ContentPackAccepted>({ url: `${API_BASE}/projects/${encodeURIComponent(projectId)}/content-packs`, form })
}

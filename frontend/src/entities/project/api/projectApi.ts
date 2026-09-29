import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/shared/api'
import type { Project } from '../model/types'

export const projectKeys = {
  all: ['project'] as const,
  list: () => ['project', 'list'] as const,
  detail: (projectId: string) => ['project', projectId] as const,
}

export function useProjects() {
  return useQuery({
    queryKey: projectKeys.list(),
    queryFn: async () => (await unwrap(api.GET('/api/v1/projects'))).items,
  })
}

export function useProject(projectId: string | undefined) {
  return useQuery({
    queryKey: projectKeys.detail(projectId ?? ''),
    queryFn: () => unwrap(api.GET('/api/v1/projects/{project_id}', { params: { path: { project_id: projectId as string } } })),
    enabled: Boolean(projectId),
  })
}

export function useCreateProject() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: (name: string) => unwrap(api.POST('/api/v1/projects', { body: { name, default_language: 'ru', target_slide_count: 12 } })),
    onSuccess: (project: Project) => {
      queryClient.setQueryData(projectKeys.detail(project.id), project)
      return queryClient.invalidateQueries({ queryKey: projectKeys.list() })
    },
  })
}

export function useInvalidateProject() {
  const queryClient = useQueryClient()
  return (projectId: string) =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: projectKeys.detail(projectId) }),
      queryClient.invalidateQueries({ queryKey: projectKeys.list() }),
    ])
}

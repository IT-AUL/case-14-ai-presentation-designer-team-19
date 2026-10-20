import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, unwrap } from '@/shared/api'
import type { Project } from '../model/types'

export const projectKeys = {
  all: ['project'] as const,
  list: () => ['project', 'list'] as const,
  detail: (projectId: string) => ['project', projectId] as const,
}

const MAX_PROJECT_PAGES = 40

export async function fetchAllProjects(): Promise<Project[]> {
  const projects: Project[] = []
  let cursor: string | null = null
  for (let page = 0; page < MAX_PROJECT_PAGES; page += 1) {
    const init = cursor ? ({ params: { query: { cursor } } } as never) : undefined
    const result: { items: Project[]; next_cursor?: string | null } = await unwrap(api.GET('/api/v1/projects', init))
    projects.push(...result.items)
    cursor = result.next_cursor ?? null
    if (!cursor) break
  }
  return projects
}

export function useProjects() {
  return useQuery({
    queryKey: projectKeys.list(),
    queryFn: fetchAllProjects,
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

export function useDeleteProject() {
  const queryClient = useQueryClient()
  return useMutation({
    mutationFn: async (projectId: string) => {
      await unwrap(api.DELETE('/api/v1/projects/{project_id}', { params: { path: { project_id: projectId } } }))
      return projectId
    },
    onSuccess: (projectId) => {
      queryClient.setQueryData<Project[]>(projectKeys.list(), (projects) => projects?.filter((project) => project.id !== projectId))
      queryClient.removeQueries({ queryKey: projectKeys.detail(projectId) })
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

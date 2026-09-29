import { api, unwrap } from '@/shared/api'
import type { GenerationAccepted, GenerationCreate, GenerationDetail, Job } from '../model/types'

export function submitGeneration(projectId: string, body: GenerationCreate): Promise<GenerationAccepted> {
  return unwrap(api.POST('/api/v1/projects/{project_id}/generations', { params: { path: { project_id: projectId } }, body }))
}

export function submitRetry(generationId: string): Promise<GenerationAccepted> {
  return unwrap(api.POST('/api/v1/generations/{run_id}/retry', { params: { path: { run_id: generationId } } }))
}

export function fetchGeneration(generationId: string): Promise<GenerationDetail> {
  return unwrap(api.GET('/api/v1/generations/{run_id}', { params: { path: { run_id: generationId } } }))
}

export function fetchJob(jobId: string): Promise<Job> {
  return unwrap(api.GET('/api/v1/jobs/{job_id}', { params: { path: { job_id: jobId } } }))
}

export function requestCancel(generationId: string): Promise<Job> {
  return unwrap(api.POST('/api/v1/generations/{run_id}/cancel', { params: { path: { run_id: generationId } } }))
}

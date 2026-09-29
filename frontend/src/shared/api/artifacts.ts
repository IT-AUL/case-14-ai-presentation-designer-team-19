import { API_BASE } from '@/shared/config'

export function artifactUrl(artifactId: string): string {
  return `${API_BASE}/artifacts/${encodeURIComponent(artifactId)}/download`
}

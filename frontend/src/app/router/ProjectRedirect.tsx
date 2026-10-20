import { Navigate, useParams } from 'react-router'
import { routes } from '@/shared/config'

type Target = 'start' | 'planDraft' | 'polish'

export function ProjectRedirect({ to }: { to: Target }) {
  const { projectId = '', runId = '', variantId } = useParams()
  const target = to === 'polish' ? routes.polish(projectId, runId, variantId) : routes[to](projectId)
  return <Navigate to={target} replace />
}

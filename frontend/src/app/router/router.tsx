import { createBrowserRouter, Navigate } from 'react-router'
import { routePatterns } from '@/shared/config'
import { ProjectLayout } from '../layouts/ProjectLayout'
import { RootLayout } from '../layouts/RootLayout'
import { RouteError } from './RouteError'

export const router = createBrowserRouter([
  {
    errorElement: <RouteError />,
    children: [
      {
        element: <RootLayout />,
        children: [
          { path: routePatterns.projects, lazy: async () => ({ Component: (await import('@/pages/projects')).ProjectsPage }) },
          { path: '*', lazy: async () => ({ Component: (await import('@/pages/not-found')).NotFoundPage }) },
        ],
      },
      {
        path: routePatterns.project,
        element: <ProjectLayout />,
        children: [
          { index: true, element: <Navigate to={routePatterns.template} replace /> },
          { path: routePatterns.template, lazy: async () => ({ Component: (await import('@/pages/template')).TemplatePage }) },
          { path: routePatterns.brief, lazy: async () => ({ Component: (await import('@/pages/brief')).BriefPage }) },
          { path: routePatterns.planDraft, lazy: async () => ({ Component: (await import('@/pages/plan')).PlanDraftPage }) },
          { path: routePatterns.run, lazy: async () => ({ Component: (await import('@/pages/generation')).GenerationPage }) },
          { path: routePatterns.plan, lazy: async () => ({ Component: (await import('@/pages/plan')).PlanPage }) },
          { path: routePatterns.variants, lazy: async () => ({ Component: (await import('@/pages/variants')).VariantsPage }) },
          { path: routePatterns.audit, lazy: async () => ({ Component: (await import('@/pages/audit')).AuditPage }) },
          { path: routePatterns.export, lazy: async () => ({ Component: (await import('@/pages/export')).ExportPage }) },
        ],
      },
    ],
  },
])

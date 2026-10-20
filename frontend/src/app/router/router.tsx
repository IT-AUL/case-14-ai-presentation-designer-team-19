import { createBrowserRouter, Navigate } from 'react-router'
import { routePatterns } from '@/shared/config'
import { ProjectLayout } from '../layouts/ProjectLayout'
import { RootLayout } from '../layouts/RootLayout'
import { ProjectRedirect } from './ProjectRedirect'
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
          { index: true, element: <Navigate to={routePatterns.start} replace /> },
          { path: routePatterns.start, lazy: async () => ({ Component: (await import('@/pages/intake')).IntakePage }) },
          { path: routePatterns.understanding, lazy: async () => ({ Component: (await import('@/pages/understanding')).UnderstandingPage }) },
          { path: routePatterns.questions, lazy: async () => ({ Component: (await import('@/pages/questions')).QuestionsPage }) },
          { path: routePatterns.style, lazy: async () => ({ Component: (await import('@/pages/style')).StylePage }) },
          { path: routePatterns.template, element: <ProjectRedirect to="start" /> },
          { path: routePatterns.brief, element: <ProjectRedirect to="start" /> },
          { path: routePatterns.planDraft, lazy: async () => ({ Component: (await import('@/pages/plan')).PlanDraftPage }) },
          { path: routePatterns.run, lazy: async () => ({ Component: (await import('@/pages/generation')).GenerationPage }) },
          { path: routePatterns.plan, element: <ProjectRedirect to="planDraft" /> },
          { path: routePatterns.polish, lazy: async () => ({ Component: (await import('@/pages/audit')).AuditPage }) },
          { path: routePatterns.variants, element: <ProjectRedirect to="polish" /> },
          { path: routePatterns.audit, element: <ProjectRedirect to="polish" /> },
          { path: routePatterns.export, element: <ProjectRedirect to="polish" /> },
        ],
      },
    ],
  },
])

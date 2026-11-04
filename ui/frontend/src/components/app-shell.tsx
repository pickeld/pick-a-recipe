import { Suspense, useCallback } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'

import { SidebarInset, SidebarProvider, SidebarTrigger } from '@/components/ui/sidebar'
import { AppSidebar } from '@/components/app-sidebar'
import { RecipePreviewHost } from '@/components/recipe-preview-host'
import { TimerTray } from '@/components/kitchen/timers'
import { useKitchenLive } from '@/components/kitchen/use-kitchen-live'
import { Separator } from '@/components/ui/separator'
import { useSocketEvent } from '@/lib/socket'
import type { JobFailedPayload } from '@/types'

const TITLES: Record<string, string> = {
  '/': 'nav.extract',
  '/tasks': 'nav.tasks',
  '/settings': 'nav.settings',
  '/library': 'nav.library',
  '/planner': 'nav.planner',
  '/shopping': 'nav.shopping',
  '/souschef': 'nav.souschef',
  '/import': 'nav.import',
  '/cook-log': 'nav.cookLog',
}

function JobFailureNotice() {
  useSocketEvent(
    'job_failed',
    useCallback((payload: JobFailedPayload) => {
      const message = payload.error?.trim() || 'The job failed.'
      // Stay up until dismissed. A short toast was gone before it could be read.
      toast.error(message, { duration: Infinity, id: `job-failed-${payload.job_id}` })
    }, []),
  )
  return null
}

function KitchenLive() {
  useKitchenLive()
  return null
}

function titleKey(path: string): string {
  if (TITLES[path]) return TITLES[path]
  if (path.startsWith('/jobs/')) return 'nav.jobProgress'
  if (path.startsWith('/library/')) return 'nav.library'
  return 'nav.app'
}

export function AppShell() {
  const location = useLocation()
  const { t } = useTranslation()
  const title = t(titleKey(location.pathname))

  return (
    <SidebarProvider>
      <JobFailureNotice />
      <KitchenLive />
      <RecipePreviewHost />
      <TimerTray />
      <AppSidebar />
      <SidebarInset>
        <header className="no-print flex h-14 shrink-0 items-center gap-2 border-b px-4">
          <SidebarTrigger className="-ms-1 rtl:-scale-x-100" />
          <Separator orientation="vertical" className="me-2 !h-4" />
          <h1 className="text-sm font-semibold">{title}</h1>
        </header>
        <div className="min-w-0 flex-1 overflow-auto p-4 md:p-6">
          <Suspense fallback={null}>
            <Outlet />
          </Suspense>
        </div>
      </SidebarInset>
    </SidebarProvider>
  )
}

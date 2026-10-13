import { useCallback } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { toast } from 'sonner'

import { SidebarInset, SidebarProvider, SidebarTrigger } from '@/components/ui/sidebar'
import { AppSidebar } from '@/components/app-sidebar'
import { RecipePreviewHost } from '@/components/recipe-preview-host'
import { Separator } from '@/components/ui/separator'
import { useSocketEvent } from '@/lib/socket'
import type { JobFailedPayload } from '@/types'

const TITLES: Record<string, string> = {
  '/': 'Extract a Recipe',
  '/tasks': 'Tasks',
  '/settings': 'Settings',
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

export function AppShell() {
  const location = useLocation()
  const title =
    TITLES[location.pathname] ??
    (location.pathname.startsWith('/jobs/') ? 'Job Progress' : 'Pick a Recipe')

  return (
    <SidebarProvider>
      <JobFailureNotice />
      <RecipePreviewHost />
      <AppSidebar />
      <SidebarInset>
        <header className="flex h-14 shrink-0 items-center gap-2 border-b px-4">
          <SidebarTrigger className="-ml-1" />
          <Separator orientation="vertical" className="mr-2 !h-4" />
          <h1 className="text-sm font-semibold">{title}</h1>
        </header>
        <div className="min-w-0 flex-1 overflow-auto p-4 md:p-6">
          <Outlet />
        </div>
      </SidebarInset>
    </SidebarProvider>
  )
}
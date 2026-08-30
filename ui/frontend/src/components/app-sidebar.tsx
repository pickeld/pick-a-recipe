import { useQuery } from '@tanstack/react-query'
import { NavLink } from 'react-router-dom'
import { ChefHat, Home, ListChecks, LogOut, Moon, Settings, Sun } from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { useSession } from '@/hooks/use-session'
import { useSocketEvent } from '@/lib/socket'
import { useTheme } from '@/components/theme-provider'
import { Button } from '@/components/ui/button'
import { Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent, SidebarGroupLabel, SidebarHeader, SidebarMenu, SidebarMenuBadge, SidebarMenuButton, SidebarMenuItem, SidebarRail } from '@/components/ui/sidebar'
import { Separator } from '@/components/ui/separator'
import { TooltipProvider } from '@/components/ui/tooltip'

const NAV_ITEMS = [
  { title: 'Home', to: '/', icon: Home },
  { title: 'Tasks', to: '/tasks', icon: ListChecks },
  { title: 'Settings', to: '/settings', icon: Settings },
] as const

export function AppSidebar() {
  const queryClient = useQueryClient()
  const { data: session } = useSession()
  const { theme, toggleTheme } = useTheme()

  // Approvals badge count, refreshed on socket pings + every 30s
  const { data: pending } = useQuery({
    queryKey: ['pending-uploads'],
    queryFn: () => api.getPendingUploads(),
    refetchInterval: 30_000,
  })
  useSocketEvent('approvals_updated', () => {
    queryClient.invalidateQueries({ queryKey: ['pending-uploads'] })
  })
  const approvalsCount = pending?.pending_uploads.length ?? 0

  return (
    <TooltipProvider>
      <Sidebar collapsible="offcanvas">
        <SidebarHeader>
          <div className="flex items-center gap-2 px-2 py-1.5">
            <div className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
              <ChefHat className="size-4" />
            </div>
            <span className="text-sm font-semibold tracking-tight">
              Pick a Recipe
            </span>
          </div>
        </SidebarHeader>

        <SidebarContent>
          <SidebarGroup>
            <SidebarGroupLabel>Navigation</SidebarGroupLabel>
            <SidebarGroupContent>
              <SidebarMenu>
                {NAV_ITEMS.map((item) => (
                  <SidebarMenuItem key={item.to}>
                    <NavLink to={item.to}>
                      {({ isActive }) => (
                        <SidebarMenuButton
                          isActive={isActive}
                          tooltip={item.title}
                        >
                          <item.icon />
                          <span>{item.title}</span>
                        </SidebarMenuButton>
                      )}
                    </NavLink>
                    {item.title === 'Tasks' && approvalsCount > 0 && (
                      <SidebarMenuBadge className="bg-destructive text-white rounded-full px-1.5">
                        {approvalsCount}
                      </SidebarMenuBadge>
                    )}
                  </SidebarMenuItem>
                ))}
              </SidebarMenu>
            </SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>

        <SidebarFooter>
          <div className="flex items-center justify-between gap-2 px-2 py-1">
            <Button
              variant="ghost"
              size="icon"
              onClick={toggleTheme}
              aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
            >
              {theme === 'dark' ? (
                <Sun className="size-4" />
              ) : (
                <Moon className="size-4" />
              )}
            </Button>
            {!session?.auth_disabled && (
              <>
                <Separator orientation="vertical" className="h-5" />
                <a href="/logout" aria-label="Log out">
                  <Button variant="ghost" size="icon" title="Logout">
                    <LogOut className="size-4" />
                  </Button>
                </a>
              </>
            )}
          </div>
          {session && (
            <div className="flex items-center gap-1.5 px-3 pb-2 text-xs text-muted-foreground">
              <span className="truncate">{session.user}</span>
              {session.is_admin && (
                <span className="shrink-0 rounded-sm bg-primary/10 px-1 py-0.5 font-medium text-primary">
                  admin
                </span>
              )}
            </div>
          )}
        </SidebarFooter>
        <SidebarRail />
      </Sidebar>
    </TooltipProvider>
  )
}

import { useQuery } from '@tanstack/react-query'
import { NavLink } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  BookOpen, CalendarDays, ChefHat, Download, History, Home, Languages, ListChecks, LogOut, Moon, Settings,
  ShoppingCart, Sparkles, Sun,
} from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { kitchen } from '@/lib/kitchen'
import { cn } from '@/lib/utils'
import { useSession } from '@/hooks/use-session'
import { useSocketEvent } from '@/lib/socket'
import { LANGUAGES, useDirection } from '@/i18n'
import { useTheme } from '@/components/theme-provider'
import { Button } from '@/components/ui/button'
import { Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupContent, SidebarGroupLabel, SidebarHeader, SidebarMenu, SidebarMenuBadge, SidebarMenuButton, SidebarMenuItem, SidebarRail } from '@/components/ui/sidebar'
import { Separator } from '@/components/ui/separator'
import { TooltipProvider } from '@/components/ui/tooltip'

const KITCHEN_ITEMS = [
  { key: 'library', to: '/library', icon: BookOpen },
  { key: 'souschef', to: '/souschef', icon: Sparkles },
  { key: 'planner', to: '/planner', icon: CalendarDays },
  { key: 'shopping', to: '/shopping', icon: ShoppingCart },
  { key: 'import', to: '/import', icon: Download },
  { key: 'cookLog', to: '/cook-log', icon: History },
] as const

const NAV_ITEMS = [
  { key: 'extract', to: '/', icon: Home },
  { key: 'tasks', to: '/tasks', icon: ListChecks },
  { key: 'settings', to: '/settings', icon: Settings },
] as const

export function AppSidebar() {
  const queryClient = useQueryClient()
  const { t, i18n } = useTranslation()
  const dir = useDirection()
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

  const { data: lists } = useQuery({ queryKey: ['kitchen', 'shopping', 'lists'], queryFn: kitchen.shoppingLists })
  const shoppingCount = lists?.lists.reduce((n, l) => n + (l.open_items ?? 0), 0) ?? 0

  const { data: library } = useQuery({ queryKey: ['kitchen', 'recipes', 'count'], queryFn: () => kitchen.listRecipes({ limit: 1 }) })
  const libraryCount = library?.total ?? 0

  const badges: Record<string, number> = { tasks: approvalsCount, shopping: shoppingCount, library: libraryCount }
  const nextLanguage = LANGUAGES.find((l) => l.code !== i18n.language) ?? LANGUAGES[0]

  const renderItems = (items: readonly { key: string; to: string; icon: typeof Home }[]) => (
    <SidebarMenu>
      {items.map((item) => (
        <SidebarMenuItem key={item.to}>
          <NavLink to={item.to} end={item.to === '/'}>
            {({ isActive }) => (
              <SidebarMenuButton isActive={isActive} tooltip={t(`nav.${item.key}`)}>
                <item.icon />
                <span>{t(`nav.${item.key}`)}</span>
              </SidebarMenuButton>
            )}
          </NavLink>
          {badges[item.key] > 0 && (
            <SidebarMenuBadge className={cn('top-1.5 rounded-full px-1.5', item.key === 'tasks' ? 'bg-destructive text-white' : 'bg-foreground/10')}>
              {badges[item.key]}
            </SidebarMenuBadge>
          )}
        </SidebarMenuItem>
      ))}
    </SidebarMenu>
  )

  return (
    <TooltipProvider>
      <Sidebar collapsible="offcanvas" side={dir === 'rtl' ? 'right' : 'left'} className="no-print">
        <SidebarHeader>
          <div className="flex items-center gap-2 px-2 py-1.5">
            <div className="flex size-8 items-center justify-center rounded-lg bg-primary text-primary-foreground">
              <ChefHat className="size-4" />
            </div>
            <span className="text-sm font-semibold tracking-tight">
              {t('nav.app')}
            </span>
          </div>
        </SidebarHeader>

        <SidebarContent>
          <SidebarGroup>
            <SidebarGroupLabel>{t('nav.kitchen')}</SidebarGroupLabel>
            <SidebarGroupContent>{renderItems(KITCHEN_ITEMS)}</SidebarGroupContent>
          </SidebarGroup>
          <SidebarGroup>
            <SidebarGroupLabel>{t('nav.extraction')}</SidebarGroupLabel>
            <SidebarGroupContent>{renderItems(NAV_ITEMS)}</SidebarGroupContent>
          </SidebarGroup>
        </SidebarContent>

        <SidebarFooter>
          <div className="flex items-center justify-between gap-2 px-2 py-1">
            <Button
              variant="ghost"
              size="icon"
              onClick={toggleTheme}
              aria-label={theme === 'dark' ? t('nav.lightMode') : t('nav.darkMode')}
            >
              {theme === 'dark' ? (
                <Sun className="size-4" />
              ) : (
                <Moon className="size-4" />
              )}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void i18n.changeLanguage(nextLanguage.code)}
              aria-label={t('nav.language')}
              title={nextLanguage.label}
            >
              <Languages className="size-4" />
              {t(`languages.${nextLanguage.code}`)}
            </Button>
            <Separator orientation="vertical" className="h-5" />
            <a href="/logout" aria-label={t('nav.logout')}>
              <Button variant="ghost" size="icon" title={t('nav.logout')}>
                <LogOut className="size-4 rtl:-scale-x-100" />
              </Button>
            </a>
          </div>
          {session && (
            <div className="flex items-center gap-1.5 px-3 pb-2 text-xs text-muted-foreground">
              <span className="truncate">{session.user}</span>
              {session.is_admin && (
                <span className="shrink-0 rounded-sm bg-primary/10 px-1 py-0.5 font-medium text-primary">
                  {t('nav.admin')}
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

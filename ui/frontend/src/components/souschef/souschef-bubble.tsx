import { lazy, Suspense, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { ChefHat, Loader2, X } from 'lucide-react'

import { cn } from '@/lib/utils'

const SousChefChat = lazy(() => import('@/components/souschef/souschef-chat').then((m) => ({ default: m.SousChefChat })))

const isPhone = () => window.matchMedia('(max-width: 639px)').matches

export function SousChefBubble() {
  const { t } = useTranslation()
  const location = useLocation()
  const [open, setOpen] = useState(false)
  // Stays mounted once opened, so closing the window keeps the conversation.
  const [loaded, setLoaded] = useState(false)
  const [path, setPath] = useState(location.pathname)
  const panelRef = useRef<HTMLDivElement>(null)

  // Full screen on a phone, so following a recipe link has to get out of the way.
  if (path !== location.pathname) {
    setPath(location.pathname)
    if (open && isPhone()) setOpen(false)
  }

  const toggle = () => {
    setLoaded(true)
    setOpen((v) => !v)
  }

  return (
    <div className="no-print">
      {loaded && (
        <div
          ref={panelRef}
          role="dialog"
          aria-label={t('nav.souschef')}
          onKeyDown={(e) => {
            // Select menus and the draft editor portal out of the panel and handle their own Escape.
            if (e.key === 'Escape' && panelRef.current?.contains(e.target as Node)) setOpen(false)
          }}
          className={cn(
            'fixed inset-0 z-50 flex flex-col overflow-hidden bg-background',
            'sm:inset-auto sm:right-6 sm:bottom-24 sm:h-[min(40rem,calc(100dvh-8rem))] sm:w-[26rem] sm:rounded-2xl sm:border sm:shadow-2xl',
            open ? 'animate-in fade-in-0 slide-in-from-bottom-4' : 'hidden',
          )}
        >
          <Suspense fallback={<div className="flex flex-1 items-center justify-center"><Loader2 className="size-5 animate-spin text-muted-foreground" /></div>}>
            <SousChefChat open={open} onClose={() => setOpen(false)} />
          </Suspense>
        </div>
      )}
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        aria-label={open ? t('souschef.close') : t('souschef.open')}
        title={open ? t('souschef.close') : t('souschef.open')}
        className={cn(
          'fixed right-6 bottom-6 z-50 flex size-14 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-lg transition-transform hover:scale-105 focus-visible:ring-4 focus-visible:ring-ring/50 focus-visible:outline-none',
          open && 'max-sm:hidden',
        )}
      >
        {open ? <X className="size-6" /> : <ChefHat className="size-6" />}
      </button>
    </div>
  )
}

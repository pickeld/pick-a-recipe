import { useCallback, useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { CheckCircle2, ChevronLeft, ChevronRight, List, Sun, SunDim, X } from 'lucide-react'
import { toast } from 'sonner'

import { useDirection } from '@/i18n'
import { kitchen } from '@/lib/kitchen'
import { recipeDirection } from '@/lib/kitchen-format'
import { Button } from '@/components/ui/button'
import { Progress } from '@/components/ui/progress'
import { Sheet, SheetContent, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { errorText } from '@/components/kitchen/bits'
import { IngredientList } from '@/components/kitchen/recipe-parts'
import { TimerChip, TimerTray } from '@/components/kitchen/timers'
import { useUnitPreference } from '@/pages/recipe-page'

type WakeLockSentinelLike = { release: () => Promise<void>; released?: boolean }

/** Keep the screen on while cooking. Re-acquired when the tab comes back. */
function useWakeLock(): { supported: boolean; active: boolean; toggle: () => void } {
  const supported = typeof navigator !== 'undefined' && 'wakeLock' in navigator
  const [wanted, setWanted] = useState(supported)
  const [active, setActive] = useState(false)
  const sentinel = useRef<WakeLockSentinelLike | null>(null)

  useEffect(() => {
    if (!supported || !wanted) return
    let cancelled = false
    const acquire = async () => {
      try {
        const wl = (navigator as Navigator & { wakeLock: { request: (t: 'screen') => Promise<WakeLockSentinelLike & EventTarget> } }).wakeLock
        const s = await wl.request('screen')
        if (cancelled) { void s.release(); return }
        sentinel.current = s
        setActive(true)
        s.addEventListener('release', () => setActive(false))
      } catch {
        setActive(false)
      }
    }
    const onVisible = () => { if (document.visibilityState === 'visible') void acquire() }
    void acquire()
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      cancelled = true
      document.removeEventListener('visibilitychange', onVisible)
      void sentinel.current?.release().catch(() => undefined)
      sentinel.current = null
      setActive(false)
    }
  }, [supported, wanted])

  return { supported, active, toggle: () => setWanted((w) => !w) }
}

export function CookModePage() {
  const { recipeId = '' } = useParams()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const { t } = useTranslation()
  const dir = useDirection()
  const [units] = useUnitPreference()
  const servings = Number(params.get('servings')) || null
  const [index, setIndex] = useState(0)
  const [showAll, setShowAll] = useState(false)
  const [checked, setChecked] = useState<Set<number>>(new Set())
  const wake = useWakeLock()
  const touch = useRef<number | null>(null)

  const { data: recipe, error } = useQuery({
    queryKey: ['kitchen', 'recipe', recipeId, servings, units],
    queryFn: () => kitchen.getRecipe(recipeId, { servings, units }),
  })
  const total = recipe?.steps.length ?? 0
  const finished = index >= total

  const go = useCallback((delta: number) => setIndex((i) => Math.max(0, Math.min(total, i + delta))), [total])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const forward = dir === 'rtl' ? 'ArrowLeft' : 'ArrowRight'
      const back = dir === 'rtl' ? 'ArrowRight' : 'ArrowLeft'
      if (e.key === forward || e.key === ' ' || e.key === 'PageDown') { e.preventDefault(); go(1) }
      else if (e.key === back || e.key === 'PageUp') { e.preventDefault(); go(-1) }
      else if (e.key === 'Escape') navigate(`/library/${recipeId}`)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [dir, go, navigate, recipeId])

  if (error) return <p className="p-6 text-destructive">{errorText(error)}</p>
  if (!recipe) return <div className="flex h-svh items-center justify-center text-muted-foreground">{t('common.loading')}</div>

  const contentDir = recipeDirection(recipe)
  const step = recipe.steps[index]
  const used = step ? recipe.ingredients.filter((i) => step.ingredient_refs.includes(i.position)) : []
  const Prev = dir === 'rtl' ? ChevronRight : ChevronLeft
  const Next = dir === 'rtl' ? ChevronLeft : ChevronRight

  const markCooked = async () => {
    try {
      await kitchen.markCooked(recipe.id, { servings })
      toast.success(t('cooked.done'))
      navigate(`/library/${recipe.id}`)
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  return (
    <div
      className="flex h-svh flex-col bg-background"
      onTouchStart={(e) => { touch.current = e.touches[0].clientX }}
      onTouchEnd={(e) => {
        if (touch.current === null) return
        const dx = e.changedTouches[0].clientX - touch.current
        touch.current = null
        if (Math.abs(dx) < 60) return
        const forward = dir === 'rtl' ? dx > 0 : dx < 0
        go(forward ? 1 : -1)
      }}
    >
      <header className="flex items-center gap-2 border-b px-4 py-2">
        <Button variant="ghost" size="icon" asChild aria-label={t('cook.exit')}>
          <Link to={`/library/${recipe.id}`}><X /></Link>
        </Button>
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium"><bdi>{recipe.name}</bdi></p>
          <p className="text-xs text-muted-foreground">
            {finished ? t('cook.finished') : t('cook.stepOf', { n: index + 1, total })} · {t('recipe.servingsLabel', { count: recipe.servings ?? 0 })}
          </p>
        </div>
        {wake.supported && (
          <Button variant="ghost" size="icon" onClick={wake.toggle} aria-pressed={wake.active} title={wake.active ? t('cook.awakeOn') : t('cook.awakeOff')}>
            {wake.active ? <Sun /> : <SunDim className="opacity-50" />}
          </Button>
        )}
        <Button variant="outline" size="sm" onClick={() => setShowAll(true)}><List className="size-4" />{t('recipe.ingredients')}</Button>
      </header>
      <Progress value={total ? (Math.min(index, total) / total) * 100 : 0} className="h-1 rounded-none" />

      <main className="flex flex-1 flex-col items-center justify-center overflow-y-auto px-6 py-8">
        {finished ? (
          <div className="flex flex-col items-center gap-4 text-center">
            <CheckCircle2 className="size-14 text-primary" />
            <p className="text-2xl font-semibold">{t('cook.enjoy')}</p>
            <div className="flex gap-2">
              <Button onClick={() => void markCooked()}>{t('recipe.cooked')}</Button>
              <Button variant="outline" asChild><Link to={`/library/${recipe.id}`}>{t('cook.back')}</Link></Button>
            </div>
          </div>
        ) : step ? (
          <div className="grid w-full max-w-3xl gap-6">
            {step.section && <p className="text-sm font-semibold tracking-wide text-muted-foreground uppercase" dir={contentDir ?? 'auto'}>{step.section}</p>}
            <p className="text-2xl leading-relaxed md:text-3xl" dir={contentDir ?? 'auto'}>{step.text}</p>
            {step.timers.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {step.timers.map((timer, i) => <TimerChip key={i} timer={timer} recipe={recipe.name} className="px-3 py-1.5 text-base" />)}
              </div>
            )}
            {used.length > 0 && (
              <div className="rounded-xl border bg-muted/30 p-4">
                <p className="mb-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase">{t('cook.forThisStep')}</p>
                <IngredientList ingredients={used} dir={contentDir} />
              </div>
            )}
          </div>
        ) : (
          <p className="text-muted-foreground">{t('recipe.noSteps')}</p>
        )}
      </main>

      <footer className="flex items-center justify-between gap-3 border-t px-4 py-3">
        <Button variant="outline" size="lg" onClick={() => go(-1)} disabled={index === 0}>
          <Prev className="size-5" />{t('cook.previous')}
        </Button>
        <div className="hidden gap-1 sm:flex">
          {recipe.steps.map((s, i) => (
            <button key={s.position} type="button" aria-label={t('cook.stepOf', { n: i + 1, total })} onClick={() => setIndex(i)}
              className={`size-2 rounded-full ${i === index ? 'bg-primary' : i < index ? 'bg-primary/40' : 'bg-muted-foreground/30'}`} />
          ))}
        </div>
        <Button size="lg" onClick={() => go(1)} disabled={finished}>
          {index === total - 1 ? t('cook.finish') : t('cook.next')}<Next className="size-5" />
        </Button>
      </footer>

      <Sheet open={showAll} onOpenChange={setShowAll}>
        <SheetContent side={dir === 'rtl' ? 'left' : 'right'} className="overflow-y-auto">
          <SheetHeader><SheetTitle>{t('recipe.ingredients')}</SheetTitle></SheetHeader>
          <div className="px-4 pb-6">
            <IngredientList
              ingredients={recipe.ingredients}
              dir={contentDir}
              checked={checked}
              highlight={step?.ingredient_refs}
              onToggle={(p) => setChecked((s) => { const n = new Set(s); if (n.has(p)) n.delete(p); else n.add(p); return n })}
            />
          </div>
        </SheetContent>
      </Sheet>
      <TimerTray />
    </div>
  )
}

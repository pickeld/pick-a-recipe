import { useMemo, useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'
import {
  DndContext, DragOverlay, KeyboardSensor, PointerSensor, TouchSensor, useDraggable, useDroppable, useSensor, useSensors,
  type DragEndEvent, type DragStartEvent,
} from '@dnd-kit/core'
import {
  CalendarDays, CalendarRange, ChevronLeft, ChevronRight, Copy, Eraser, Link2, Loader2, MoreHorizontal, Plus, Repeat,
  ShoppingCart, Sparkles, Trash2,
} from 'lucide-react'
import { toast } from 'sonner'

import { useDirection } from '@/i18n'
import { isUnavailable, kitchen, SLOTS, type PlanEntry, type PlanResponse, type Slot, type WeekPlanResult } from '@/lib/kitchen'
import { addDays, monthGrid, parseISODate, sameMonth, toISODate, weekDays, weekStart } from '@/lib/kitchen-format'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Checkbox, PageHeader, RecipeImage, errorText } from '@/components/kitchen/bits'
import { RecipePicker } from '@/components/kitchen/recipe-picker'

type View = 'week' | 'month'

function useLocaleDate() {
  const { i18n } = useTranslation()
  const locale = i18n.language === 'he' ? 'he-IL' : 'en-GB'
  return (iso: string, opts: Intl.DateTimeFormatOptions) => parseISODate(iso).toLocaleDateString(locale, opts)
}

function EntryCard({ entry, compact, onRemove, onLeftovers, onServings, overlay }: {
  entry: PlanEntry
  compact?: boolean
  overlay?: boolean
  onRemove?: () => void
  onLeftovers?: () => void
  onServings?: () => void
}) {
  const { t } = useTranslation()
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id: `entry-${entry.id}`, data: { entry }, disabled: overlay })
  return (
    <div
      ref={overlay ? undefined : setNodeRef}
      {...(overlay ? {} : attributes)}
      {...(overlay ? {} : listeners)}
      className={cn(
        'group flex touch-none items-center gap-2 rounded-lg border bg-card p-1.5 text-sm shadow-xs',
        isDragging && 'opacity-30',
        overlay && 'rotate-2 shadow-lg',
        entry.leftover_of && 'border-dashed',
      )}
    >
      {!compact && entry.recipe_id && <RecipeImage src={entry.image_url} alt="" className="size-9 shrink-0 rounded-md" />}
      <div className="min-w-0 flex-1">
        {entry.recipe_id ? (
          <Link to={`/library/${entry.recipe_id}`} className="line-clamp-2 leading-tight hover:underline" dir="auto" onPointerDown={(e) => e.stopPropagation()}>
            {entry.title}
          </Link>
        ) : (
          <span className="line-clamp-2 leading-tight" dir="auto">{entry.title}</span>
        )}
        {!compact && (
          <div className="mt-0.5 flex flex-wrap items-center gap-1 text-[11px] text-muted-foreground">
            {entry.servings ? <span>{t('recipe.servingsLabel', { count: entry.servings })}</span> : null}
            {entry.leftover_of && <Badge variant="outline" className="h-4 px-1 text-[10px]">{t('plan.leftovers')}</Badge>}
          </div>
        )}
      </div>
      {!overlay && (onRemove || onLeftovers) && (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button size="icon-xs" variant="ghost" className="shrink-0 opacity-60 group-hover:opacity-100" onPointerDown={(e) => e.stopPropagation()} aria-label={t('common.more')}>
              <MoreHorizontal />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {onServings && <DropdownMenuItem onSelect={onServings}>{t('plan.changeServings')}</DropdownMenuItem>}
            {onLeftovers && entry.recipe_id && !entry.leftover_of && (
              <DropdownMenuItem onSelect={onLeftovers}><Repeat />{t('plan.makeLeftovers')}</DropdownMenuItem>
            )}
            <DropdownMenuSeparator />
            {onRemove && <DropdownMenuItem variant="destructive" onSelect={onRemove}><Trash2 />{t('plan.remove')}</DropdownMenuItem>}
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  )
}

function DropCell({ id, children, className, onAdd }: { id: string; children: ReactNode; className?: string; onAdd?: () => void }) {
  const { isOver, setNodeRef } = useDroppable({ id })
  const { t } = useTranslation()
  return (
    <div ref={setNodeRef} className={cn('group/cell relative flex min-h-16 flex-col gap-1.5 rounded-lg p-1 transition-colors', isOver && 'bg-primary/10 ring-2 ring-primary/40', className)}>
      {children}
      {onAdd && (
        <button
          type="button"
          onClick={onAdd}
          className="flex h-7 items-center justify-center rounded-md border border-dashed text-muted-foreground opacity-40 transition-opacity group-hover/cell:opacity-100 hover:bg-muted focus:opacity-100"
          aria-label={t('plan.add')}
        >
          <Plus className="size-3.5" />
        </button>
      )}
    </div>
  )
}

function IcsDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [url, setUrl] = useState('')
  const { data } = useQuery({ queryKey: ['kitchen', 'ics'], queryFn: kitchen.icsStatus, enabled: open })
  const rotate = useMutation({
    mutationFn: kitchen.icsRotate,
    onSuccess: (r) => { setUrl(r.url); void qc.invalidateQueries({ queryKey: ['kitchen', 'ics'] }) },
  })
  const revoke = useMutation({
    mutationFn: kitchen.icsRevoke,
    onSuccess: () => { setUrl(''); void qc.invalidateQueries({ queryKey: ['kitchen', 'ics'] }) },
  })
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('ics.title')}</DialogTitle>
          <DialogDescription>{t('ics.description')}</DialogDescription>
        </DialogHeader>
        {url ? (
          <div className="grid gap-2">
            <Input readOnly value={url} dir="ltr" onFocus={(e) => e.target.select()} />
            <p className="text-xs text-muted-foreground">{t('ics.copyOnce')}</p>
            <Button variant="outline" onClick={() => void navigator.clipboard?.writeText(url).then(() => toast.success(t('share.copiedShort')))}>
              <Copy className="size-4" />{t('common.copy')}
            </Button>
          </div>
        ) : data?.enabled ? (
          <p className="text-sm">{t('ics.enabled')}</p>
        ) : (
          <p className="text-sm text-muted-foreground">{t('ics.disabled')}</p>
        )}
        <DialogFooter>
          {data?.enabled && <Button variant="destructive" onClick={() => revoke.mutate()}>{t('ics.revoke')}</Button>}
          <Button onClick={() => rotate.mutate()} disabled={rotate.isPending}>{data?.enabled ? t('ics.rotate') : t('ics.enable')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function PlanWeekDialog({ open, onOpenChange, start, end }: { open: boolean; onOpenChange: (v: boolean) => void; start: string; end: string }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const fmt = useLocaleDate()
  const [slots, setSlots] = useState<Slot[]>(['dinner'])
  const [message, setMessage] = useState('')
  const [result, setResult] = useState<WeekPlanResult | null>(null)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const run = useMutation({
    mutationFn: () => kitchen.planWeek({ start, end, slots, message }),
    onSuccess: (r) => { setResult(r); setSelected(new Set(r.assignments.map((_, i) => i))) },
    onError: (e) => toast.error(isUnavailable(e) ? t('souschef.unavailable') : errorText(e)),
  })
  const apply = useMutation({
    mutationFn: () => kitchen.applyPlan(result!.assignments.filter((_, i) => selected.has(i))),
    onSuccess: (r) => {
      toast.success(t('plan.applied', { count: r.added }))
      void qc.invalidateQueries({ queryKey: ['kitchen', 'plan'] })
      onOpenChange(false)
      setResult(null)
    },
  })
  return (
    <Dialog open={open} onOpenChange={(v) => { onOpenChange(v); if (!v) setResult(null) }}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2"><Sparkles className="size-4 text-primary" />{t('souschef.planWeek')}</DialogTitle>
          <DialogDescription>{t('souschef.planWeekHint')}</DialogDescription>
        </DialogHeader>
        {!result ? (
          <div className="grid gap-3">
            <div className="flex flex-wrap gap-3">
              {SLOTS.map((s) => (
                <label key={s} className="flex items-center gap-1.5 text-sm">
                  <Checkbox checked={slots.includes(s)} onChange={(v) => setSlots((cur) => (v ? [...cur, s] : cur.filter((x) => x !== s)))} />
                  {t(`plan.slots.${s}`)}
                </label>
              ))}
            </div>
            <Textarea value={message} onChange={(e) => setMessage(e.target.value)} rows={2} placeholder={t('souschef.planWeekPlaceholder')} />
          </div>
        ) : (
          <div className="grid gap-2">
            {result.summary && <p className="text-sm" dir="auto">{result.summary}</p>}
            {!result.assignments.length && <p className="text-sm text-muted-foreground">{t('souschef.noPlan')}</p>}
            <ul className="grid max-h-80 gap-1.5 overflow-y-auto">
              {result.assignments.map((a, i) => (
                <li key={i} className="flex items-center gap-2 rounded-lg border p-2 text-sm">
                  <Checkbox checked={selected.has(i)} onChange={(v) => setSelected((s) => { const n = new Set(s); if (v) n.add(i); else n.delete(i); return n })} />
                  <span className="w-24 shrink-0 text-xs text-muted-foreground">{fmt(a.date, { weekday: 'short', day: 'numeric' })} · {t(`plan.slots.${a.slot}`)}</span>
                  <span className="min-w-0 flex-1" dir="auto"><span className="font-medium">{a.name}</span>{a.why && <span className="block text-xs text-muted-foreground">{a.why}</span>}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        <DialogFooter>
          {!result ? (
            <Button onClick={() => run.mutate()} disabled={run.isPending || !slots.length}>
              {run.isPending && <Loader2 className="size-4 animate-spin" />}{t('souschef.suggestPlan')}
            </Button>
          ) : (
            <>
              <Button variant="outline" onClick={() => setResult(null)}>{t('common.back')}</Button>
              <Button onClick={() => apply.mutate()} disabled={!selected.size || apply.isPending}>{t('plan.applySelected', { count: selected.size })}</Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function PlannerPage() {
  const { t } = useTranslation()
  const dir = useDirection()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const fmt = useLocaleDate()
  const today = toISODate(new Date())
  const [view, setView] = useState<View>('week')
  const [anchor, setAnchor] = useState(today)
  const [picker, setPicker] = useState<{ date: string; slot: Slot } | null>(null)
  const [servingsFor, setServingsFor] = useState<PlanEntry | null>(null)
  const [servingsDraft, setServingsDraft] = useState('')
  const [dragging, setDragging] = useState<PlanEntry | null>(null)
  const [icsOpen, setIcsOpen] = useState(false)
  const [planOpen, setPlanOpen] = useState(false)

  const days = useMemo(() => (view === 'week' ? weekDays(weekStart(anchor)) : monthGrid(anchor)), [view, anchor])
  const start = days[0]
  const end = days[days.length - 1]
  const weekStartIso = weekStart(anchor)
  const weekEndIso = addDays(weekStartIso, 6)
  const key = ['kitchen', 'plan', start, end]
  const { data } = useQuery({ queryKey: key, queryFn: () => kitchen.plan(start, end) })

  const byCell = useMemo(() => {
    const m = new Map<string, PlanEntry[]>()
    for (const e of data?.entries ?? []) {
      const k = view === 'week' ? `${e.date}|${e.slot}` : e.date
      m.set(k, [...(m.get(k) ?? []), e])
    }
    return m
  }, [data, view])

  const invalidate = () => qc.invalidateQueries({ queryKey: ['kitchen', 'plan'] })
  const move = useMutation({
    mutationFn: ({ id, date, slot }: { id: number; date: string; slot?: Slot }) =>
      kitchen.updatePlanEntry(id, slot ? { date, slot } : { date }),
    onMutate: async ({ id, date, slot }) => {
      await qc.cancelQueries({ queryKey: key })
      const prev = qc.getQueryData<PlanResponse>(key)
      if (prev) {
        qc.setQueryData<PlanResponse>(key, {
          ...prev,
          entries: prev.entries.map((e) => (e.id === id ? { ...e, date, slot: slot ?? e.slot } : e)),
        })
      }
      return { prev }
    },
    onError: (err, _v, ctx) => {
      if (ctx?.prev) qc.setQueryData(key, ctx.prev)
      toast.error(errorText(err))
    },
    onSettled: () => void invalidate(),
  })
  const remove = useMutation({ mutationFn: kitchen.deletePlanEntry, onSuccess: () => void invalidate() })

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 220, tolerance: 6 } }),
    useSensor(KeyboardSensor),
  )

  const onDragStart = (e: DragStartEvent) => setDragging((e.active.data.current as { entry: PlanEntry }).entry)
  const onDragEnd = (e: DragEndEvent) => {
    setDragging(null)
    const entry = (e.active.data.current as { entry: PlanEntry } | undefined)?.entry
    if (!entry || !e.over) return
    const [date, slot] = String(e.over.id).split('|') as [string, Slot | undefined]
    if (date === entry.date && (!slot || slot === entry.slot)) return
    move.mutate({ id: entry.id, date, slot })
  }

  const shift = (dirSign: 1 | -1) => {
    if (view === 'week') setAnchor(addDays(anchor, 7 * dirSign))
    else {
      const d = parseISODate(anchor)
      setAnchor(toISODate(new Date(d.getFullYear(), d.getMonth() + dirSign, 1)))
    }
  }

  const leftovers = async (entry: PlanEntry) => {
    await kitchen.addPlanEntry({ date: addDays(entry.date, 1), slot: 'lunch', leftover_of: entry.id, servings: entry.servings ? Math.max(1, Math.round(entry.servings / 2)) : null })
    void invalidate()
    toast.success(t('plan.leftoversAdded'))
  }

  const toShopping = async () => {
    try {
      const res = await kitchen.generateShopping({ start: weekStartIso, end: weekEndIso })
      void qc.invalidateQueries({ queryKey: ['kitchen', 'shopping'] })
      toast.success(t('shopping.addedFromPlan', { count: res.added, recipes: res.recipes ?? 0 }), {
        action: { label: t('nav.shopping'), onClick: () => navigate('/shopping') },
      })
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  const copyLastWeek = async () => {
    const res = await kitchen.copyPlan(addDays(weekStartIso, -7), addDays(weekStartIso, -1), weekStartIso)
    void invalidate()
    toast.success(t('plan.copied', { count: res.copied }))
  }

  const Prev = dir === 'rtl' ? ChevronRight : ChevronLeft
  const Next = dir === 'rtl' ? ChevronLeft : ChevronRight
  const title = view === 'week'
    ? `${fmt(start, { day: 'numeric', month: 'short' })} – ${fmt(end, { day: 'numeric', month: 'short', year: 'numeric' })}`
    : fmt(anchor, { month: 'long', year: 'numeric' })

  const nutrition = (date: string) => {
    const n = data?.nutrition[date]
    if (!n || !n.meals) return null
    return (
      <p className="text-[11px] text-muted-foreground tabular-nums" title={t('plan.nutritionHint', { covered: n.covered, meals: n.meals })}>
        {n.totals.calories ? t('library.kcal', { value: Math.round(n.totals.calories) }) : '–'}
        {n.totals.proteinContent ? ` · ${t('plan.protein', { value: Math.round(n.totals.proteinContent) })}` : ''}
        {n.covered < n.meals && <span className="text-amber-600"> *</span>}
      </p>
    )
  }

  return (
    <div className="mx-auto max-w-[1400px]">
      <PageHeader
        title={t('plan.title')}
        actions={
          <>
            <Button variant="outline" size="sm" onClick={() => setPlanOpen(true)}><Sparkles className="size-4" />{t('souschef.planWeek')}</Button>
            <Button variant="outline" size="sm" onClick={() => void toShopping()}><ShoppingCart className="size-4" />{t('plan.toShopping')}</Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild><Button variant="outline" size="icon-sm" aria-label={t('common.more')}><MoreHorizontal /></Button></DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onSelect={() => void copyLastWeek()}><Copy />{t('plan.copyLastWeek')}</DropdownMenuItem>
                <DropdownMenuItem onSelect={async () => { const r = await kitchen.clearPlan(weekStartIso, weekEndIso); void invalidate(); toast.success(t('plan.cleared', { count: r.removed })) }}>
                  <Eraser />{t('plan.clearWeek')}
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onSelect={() => setIcsOpen(true)}><Link2 />{t('ics.title')}</DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </>
        }
      />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Button variant="outline" size="icon-sm" onClick={() => shift(-1)} aria-label={t('plan.previous')}><Prev /></Button>
        <Button variant="outline" size="sm" onClick={() => setAnchor(today)}>{t('plan.today')}</Button>
        <Button variant="outline" size="icon-sm" onClick={() => shift(1)} aria-label={t('plan.next')}><Next /></Button>
        <span className="ms-1 font-medium">{title}</span>
        <div className="ms-auto inline-flex rounded-lg border p-0.5">
          {(['week', 'month'] as const).map((v) => (
            <Button key={v} size="xs" variant={view === v ? 'secondary' : 'ghost'} onClick={() => setView(v)}>
              {v === 'week' ? <CalendarRange className="size-3.5" /> : <CalendarDays className="size-3.5" />}{t(`plan.views.${v}`)}
            </Button>
          ))}
        </div>
      </div>

      <DndContext sensors={sensors} onDragStart={onDragStart} onDragEnd={onDragEnd} onDragCancel={() => setDragging(null)}>
        {view === 'week' ? (
          <div className="overflow-x-auto pb-2">
            <div className="grid min-w-[980px] grid-cols-[5.5rem_repeat(7,minmax(0,1fr))] gap-1">
              <div />
              {days.map((d) => (
                <div key={d} className={cn('rounded-lg px-2 py-1.5', d === today && 'bg-primary/10')}>
                  <p className={cn('text-sm font-medium', d === today && 'text-primary')}>{fmt(d, { weekday: 'short', day: 'numeric' })}</p>
                  {nutrition(d)}
                </div>
              ))}
              {SLOTS.map((slot) => (
                <div key={slot} className="contents">
                  <div className="pt-2 text-xs font-medium text-muted-foreground">{t(`plan.slots.${slot}`)}</div>
                  {days.map((d) => (
                    <DropCell key={`${d}|${slot}`} id={`${d}|${slot}`} className="border bg-muted/20" onAdd={() => setPicker({ date: d, slot })}>
                      {(byCell.get(`${d}|${slot}`) ?? []).map((e) => (
                        <EntryCard
                          key={e.id}
                          entry={e}
                          onRemove={() => remove.mutate(e.id)}
                          onLeftovers={() => void leftovers(e)}
                          onServings={() => { setServingsFor(e); setServingsDraft(e.servings ? String(e.servings) : '') }}
                        />
                      ))}
                    </DropCell>
                  ))}
                </div>
              ))}
            </div>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <div className="grid min-w-[700px] grid-cols-7 gap-1">
              {days.slice(0, 7).map((d) => (
                <div key={d} className="px-2 text-xs font-medium text-muted-foreground">{fmt(d, { weekday: 'short' })}</div>
              ))}
              {days.map((d) => (
                <DropCell key={d} id={d} className={cn('min-h-28 border', !sameMonth(d, anchor) && 'opacity-50', d === today && 'border-primary')} onAdd={() => setPicker({ date: d, slot: 'dinner' })}>
                  <div className="flex items-baseline justify-between px-1">
                    <span className={cn('text-xs font-medium', d === today && 'text-primary')}>{parseISODate(d).getDate()}</span>
                    {nutrition(d)}
                  </div>
                  {(byCell.get(d) ?? []).map((e) => <EntryCard key={e.id} entry={e} compact onRemove={() => remove.mutate(e.id)} />)}
                </DropCell>
              ))}
            </div>
          </div>
        )}
        <DragOverlay>{dragging ? <EntryCard entry={dragging} overlay /> : null}</DragOverlay>
      </DndContext>
      <p className="mt-2 text-xs text-muted-foreground">{t('plan.dragHint')}</p>

      <RecipePicker
        open={!!picker}
        onOpenChange={(v) => !v && setPicker(null)}
        title={picker ? t('plan.addFor', { day: fmt(picker.date, { weekday: 'long', day: 'numeric', month: 'short' }), slot: t(`plan.slots.${picker.slot}`) }) : ''}
        onPick={async (choice, servings) => {
          if (!picker) return
          try {
            await kitchen.addPlanEntry({
              date: picker.date, slot: picker.slot, servings,
              ...('recipe' in choice ? { recipe_id: choice.recipe.id } : { title: choice.title }),
            })
            void invalidate()
            setPicker(null)
          } catch (err) {
            toast.error(errorText(err))
          }
        }}
      />
      <Dialog open={!!servingsFor} onOpenChange={(v) => !v && setServingsFor(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>{t('plan.changeServings')}</DialogTitle></DialogHeader>
          <div className="grid gap-1.5">
            <Label htmlFor="srv">{t('plan.servings')}</Label>
            <Input id="srv" inputMode="decimal" value={servingsDraft} onChange={(e) => setServingsDraft(e.target.value)} />
          </div>
          <DialogFooter>
            <Button onClick={async () => {
              if (!servingsFor) return
              await kitchen.updatePlanEntry(servingsFor.id, { servings: Number(servingsDraft) || null })
              setServingsFor(null)
              void invalidate()
            }}>{t('common.save')}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <IcsDialog open={icsOpen} onOpenChange={setIcsOpen} />
      <PlanWeekDialog open={planOpen} onOpenChange={setPlanOpen} start={view === 'week' ? start : weekStartIso} end={view === 'week' ? end : weekEndIso} />
    </div>
  )
}

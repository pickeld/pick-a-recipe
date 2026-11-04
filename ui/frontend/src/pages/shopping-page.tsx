import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import {
  Archive, ChevronDown, CloudOff, MoreHorizontal, PackageCheck, Pencil, Plus, ShoppingBasket, Trash2,
} from 'lucide-react'
import { toast } from 'sonner'

import { ApiError } from '@/lib/api'
import { idbGet, idbSet } from '@/lib/idb'
import { kitchen, type ShoppingItem, type ShoppingPayload } from '@/lib/kitchen'
import { formatAmount } from '@/lib/kitchen-format'
import { applyQueue, enqueue, nowIso, remapTempId, type QueuedOp } from '@/lib/shopping-queue'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuRadioGroup, DropdownMenuRadioItem,
  DropdownMenuSeparator, DropdownMenuSub, DropdownMenuSubContent, DropdownMenuSubTrigger, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Checkbox, EmptyState, PageHeader, errorText } from '@/components/kitchen/bits'

const QUEUE_KEY = 'shopping:queue'
const LIST_KEY = 'shopping:list'

function isNetworkError(err: unknown) {
  return !(err instanceof ApiError)
}

/** Edits go through a persistent queue so the list keeps working offline. */
function useShoppingQueue(onSynced: () => void) {
  const { t } = useTranslation()
  const [queue, setQueue] = useState<QueuedOp[]>([])
  const [loaded, setLoaded] = useState(false)
  const queueRef = useRef<QueuedOp[]>([])
  const flushing = useRef(false)
  const tempIds = useRef(-1)

  useEffect(() => {
    void idbGet<QueuedOp[]>(QUEUE_KEY).then((q) => {
      const initial = q ?? []
      queueRef.current = initial
      setQueue(initial)
      tempIds.current = Math.min(-1, ...initial.filter((o) => o.op === 'add').map((o) => (o as { temp_id: number }).temp_id - 1))
      setLoaded(true)
    })
  }, [])

  const save = (next: QueuedOp[]) => {
    queueRef.current = next
    setQueue(next)
    void idbSet(QUEUE_KEY, next)
  }

  const flush = useCallback(async () => {
    if (flushing.current || !queueRef.current.length) return
    flushing.current = true
    const sending = queueRef.current
    try {
      const { results } = await kitchen.syncShopping(sending)
      let rest = queueRef.current.filter((op) => !sending.includes(op))
      let stale = 0
      sending.forEach((op, i) => {
        const r = results[i]
        if (op.op === 'add' && r?.ok && r.id) rest = remapTempId(rest, op.temp_id, r.id)
        if (r?.stale) stale += 1
      })
      save(rest)
      if (stale) toast.info(t('shopping.staleEdits', { count: stale }))
      onSynced()
    } catch (err) {
      if (!isNetworkError(err)) {
        // The server refused the batch outright; dropping it beats retrying forever.
        save(queueRef.current.filter((op) => !sending.includes(op)))
        toast.error(errorText(err))
        onSynced()
      }
    } finally {
      flushing.current = false
    }
  }, [onSynced, t])

  useEffect(() => {
    if (!loaded) return
    void flush()
    const onOnline = () => void flush()
    window.addEventListener('online', onOnline)
    const id = setInterval(() => { if (queueRef.current.length && navigator.onLine) void flush() }, 15000)
    return () => {
      window.removeEventListener('online', onOnline)
      clearInterval(id)
    }
  }, [loaded, flush])

  const push = (op: QueuedOp) => {
    save(enqueue(queueRef.current, op))
    void flush()
  }

  return {
    queue,
    update: (id: number, patch: Record<string, unknown>) => push({ op: 'update', id, patch, client_ts: nowIso() }),
    remove: (id: number) => push({ op: 'delete', id, client_ts: nowIso() }),
    add: (listId: number, food: string) => push({ op: 'add', temp_id: tempIds.current--, list_id: listId, food, client_ts: nowIso() }),
  }
}

function ItemRow({ item, aisles, onUpdate, onRemove }: {
  item: ShoppingItem
  aisles: string[]
  onUpdate: (patch: Record<string, unknown>) => void
  onRemove: () => void
}) {
  const { t } = useTranslation()
  const amount = formatAmount(item.quantity, item.unit)
  return (
    <li className="group flex items-center gap-3 px-3 py-2">
      <Checkbox checked={item.checked} onChange={(v) => onUpdate({ checked: v })} label={item.food} className="size-5" />
      <div className={cn('min-w-0 flex-1', item.checked && 'text-muted-foreground line-through')}>
        <p className="leading-tight" dir="auto">
          {amount && <span className="font-semibold tabular-nums">{amount}{item.unit ? ` ${item.unit}` : ''} </span>}
          {item.food}
        </p>
        {(item.note || item.sources.length > 0) && (
          <p className="truncate text-xs text-muted-foreground" dir="auto">
            {[item.note, item.sources.map((s) => s.name).join(', ')].filter(Boolean).join(' · ')}
          </p>
        )}
      </div>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button size="icon-xs" variant="ghost" className="opacity-50 group-hover:opacity-100" aria-label={t('common.more')}><MoreHorizontal /></Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuSub>
            <DropdownMenuSubTrigger>{t('shopping.moveAisle')}</DropdownMenuSubTrigger>
            <DropdownMenuSubContent className="max-h-80 overflow-y-auto">
              <DropdownMenuRadioGroup value={item.aisle} onValueChange={(aisle) => onUpdate({ aisle })}>
                {aisles.map((a) => <DropdownMenuRadioItem key={a} value={a}>{t(`aisles.${a}`)}</DropdownMenuRadioItem>)}
              </DropdownMenuRadioGroup>
            </DropdownMenuSubContent>
          </DropdownMenuSub>
          <DropdownMenuItem onSelect={() => onUpdate({ excluded: !item.excluded })}>
            {item.excluded ? t('shopping.needAnyway') : t('shopping.haveIt')}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem variant="destructive" onSelect={onRemove}><Trash2 />{t('common.delete')}</DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </li>
  )
}

export function ShoppingPage() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [listId, setListId] = useState<number | null>(() => Number(localStorage.getItem(LIST_KEY)) || null)
  const [offline, setOffline] = useState(!navigator.onLine)
  const [quick, setQuick] = useState('')
  const [showExcluded, setShowExcluded] = useState(false)
  const [renaming, setRenaming] = useState<{ id: number | null; name: string } | null>(null)

  useEffect(() => {
    const on = () => setOffline(false)
    const off = () => setOffline(true)
    window.addEventListener('online', on)
    window.addEventListener('offline', off)
    return () => { window.removeEventListener('online', on); window.removeEventListener('offline', off) }
  }, [])

  const { data: lists } = useQuery({ queryKey: ['kitchen', 'shopping', 'lists'], queryFn: kitchen.shoppingLists })
  const activeId = listId && lists?.lists.some((l) => l.id === listId) ? listId : lists?.lists[0]?.id ?? listId ?? undefined
  const cacheKey = `shopping:payload:${activeId ?? 'default'}`

  const { data: payload, isLoading } = useQuery({
    queryKey: ['kitchen', 'shopping', 'items', activeId ?? 'default'],
    queryFn: async (): Promise<ShoppingPayload> => {
      try {
        const fresh = await kitchen.shopping(activeId)
        void idbSet(cacheKey, fresh)
        setOffline(false)
        return fresh
      } catch (err) {
        const cached = await idbGet<ShoppingPayload>(cacheKey)
        if (cached && isNetworkError(err)) {
          setOffline(true)
          return cached
        }
        throw err
      }
    },
  })

  const refresh = useCallback(() => {
    void qc.invalidateQueries({ queryKey: ['kitchen', 'shopping'] })
  }, [qc])
  const q = useShoppingQueue(refresh)
  const view = useMemo(() => (payload ? applyQueue(payload, q.queue) : null), [payload, q.queue])

  const selectList = (id: number) => {
    localStorage.setItem(LIST_KEY, String(id))
    setListId(id)
  }

  const quickAdd = () => {
    const text = quick.trim()
    if (!text || !view) return
    q.add(view.list_id, text)
    setQuick('')
  }

  const checkedIds = view?.checked.map((i) => i.id).filter((id) => id > 0) ?? []
  const openCount = view?.groups.reduce((n, g) => n + g.items.length, 0) ?? 0

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader
        title={t('shopping.title')}
        description={view ? t('shopping.remaining', { count: openCount }) : undefined}
        actions={
          <>
            {lists && (
              <Select value={activeId ? String(activeId) : undefined} onValueChange={(v) => selectList(Number(v))}>
                <SelectTrigger className="w-44" aria-label={t('shopping.list')}><SelectValue /></SelectTrigger>
                <SelectContent>
                  {lists.lists.map((l) => <SelectItem key={l.id} value={String(l.id)}>{l.name}{l.open_items ? ` (${l.open_items})` : ''}</SelectItem>)}
                </SelectContent>
              </Select>
            )}
            <DropdownMenu>
              <DropdownMenuTrigger asChild><Button variant="outline" size="icon-sm" aria-label={t('common.more')}><MoreHorizontal /></Button></DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuLabel>{t('shopping.lists')}</DropdownMenuLabel>
                <DropdownMenuItem onSelect={() => setRenaming({ id: null, name: '' })}><Plus />{t('shopping.newList')}</DropdownMenuItem>
                {activeId && <DropdownMenuItem onSelect={() => setRenaming({ id: activeId, name: lists?.lists.find((l) => l.id === activeId)?.name ?? '' })}><Pencil />{t('shopping.renameList')}</DropdownMenuItem>}
                {activeId && (lists?.lists.length ?? 0) > 1 && (
                  <DropdownMenuItem variant="destructive" onSelect={async () => { await kitchen.deleteShoppingList(activeId); setListId(null); refresh() }}>
                    <Trash2 />{t('shopping.deleteList')}
                  </DropdownMenuItem>
                )}
                <DropdownMenuSeparator />
                {activeId && (
                  <DropdownMenuItem onSelect={async () => { await kitchen.clearShopping(activeId, true); refresh() }}>
                    <Trash2 />{t('shopping.clearAll')}
                  </DropdownMenuItem>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          </>
        }
      />

      {(offline || q.queue.length > 0) && (
        <div className="mb-3 flex items-center gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm">
          <CloudOff className="size-4" />
          {offline ? t('shopping.offline') : t('shopping.syncing')}
          {q.queue.length > 0 && <Badge variant="outline" className="ms-auto">{t('shopping.pending', { count: q.queue.length })}</Badge>}
        </div>
      )}

      <form className="mb-4 flex gap-2" onSubmit={(e) => { e.preventDefault(); quickAdd() }}>
        <Input value={quick} onChange={(e) => setQuick(e.target.value)} placeholder={t('shopping.quickAdd')} enterKeyHint="done" />
        <Button type="submit" disabled={!quick.trim()}><Plus className="size-4" />{t('common.add')}</Button>
      </form>

      {isLoading ? (
        <p className="text-muted-foreground">{t('common.loading')}</p>
      ) : !view ? null : openCount === 0 && !view.checked.length ? (
        <EmptyState icon={<ShoppingBasket />} title={t('shopping.empty')}>
          <span>{t('shopping.emptyHint')} </span>
          <Link to="/planner" className="text-primary hover:underline">{t('nav.planner')}</Link>
        </EmptyState>
      ) : (
        <div className="grid gap-4">
          {view.groups.map((g) => (
            <Card key={g.aisle} className="gap-0 py-0">
              <p className="border-b px-3 py-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase">{t(`aisles.${g.aisle}`)} · {g.items.length}</p>
              <ul className="divide-y">
                {g.items.map((item) => (
                  <ItemRow key={item.id} item={item} aisles={view.aisles} onUpdate={(p) => q.update(item.id, p)} onRemove={() => q.remove(item.id)} />
                ))}
              </ul>
            </Card>
          ))}

          {view.checked.length > 0 && (
            <Card className="gap-0 py-0">
              <div className="flex flex-wrap items-center gap-2 border-b px-3 py-2">
                <p className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">{t('shopping.inCart', { count: view.checked.length })}</p>
                <div className="ms-auto flex gap-1.5">
                  <Button size="xs" variant="outline" disabled={!checkedIds.length} onClick={async () => {
                    const r = await kitchen.toPantry(checkedIds)
                    toast.success(t('shopping.movedToPantry', { count: r.moved }))
                    refresh()
                    void qc.invalidateQueries({ queryKey: ['kitchen', 'pantry'] })
                  }}>
                    <PackageCheck className="size-3.5" />{t('shopping.toPantry')}
                  </Button>
                  <Button size="xs" variant="outline" onClick={async () => { if (activeId) { await kitchen.clearShopping(activeId); refresh() } }}>
                    <Archive className="size-3.5" />{t('shopping.clearChecked')}
                  </Button>
                </div>
              </div>
              <ul className="divide-y">
                {view.checked.map((item) => (
                  <ItemRow key={item.id} item={item} aisles={view.aisles} onUpdate={(p) => q.update(item.id, p)} onRemove={() => q.remove(item.id)} />
                ))}
              </ul>
            </Card>
          )}
        </div>
      )}

      {view && view.excluded.length > 0 && (
        <div className="mt-4">
          <Button variant="ghost" size="sm" onClick={() => setShowExcluded((s) => !s)} aria-expanded={showExcluded}>
            <ChevronDown className={cn('size-4 transition-transform', showExcluded && 'rotate-180')} />
            {t('shopping.inPantry', { count: view.excluded.length })}
          </Button>
          {showExcluded && (
            <ul className="mt-1 divide-y rounded-lg border opacity-80">
              {view.excluded.map((item) => (
                <ItemRow key={item.id} item={item} aisles={view.aisles} onUpdate={(p) => q.update(item.id, p)} onRemove={() => q.remove(item.id)} />
              ))}
            </ul>
          )}
        </div>
      )}

      <Dialog open={!!renaming} onOpenChange={(v) => !v && setRenaming(null)}>
        <DialogContent>
          <DialogHeader><DialogTitle>{renaming?.id ? t('shopping.renameList') : t('shopping.newList')}</DialogTitle></DialogHeader>
          <Input autoFocus value={renaming?.name ?? ''} onChange={(e) => setRenaming((r) => (r ? { ...r, name: e.target.value } : r))} />
          <DialogFooter>
            <Button
              disabled={!renaming?.name.trim()}
              onClick={async () => {
                if (!renaming) return
                try {
                  if (renaming.id) await kitchen.renameShoppingList(renaming.id, renaming.name)
                  else selectList((await kitchen.createShoppingList(renaming.name)).id)
                  setRenaming(null)
                  refresh()
                } catch (err) {
                  toast.error(errorText(err))
                }
              }}
            >
              {t('common.save')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

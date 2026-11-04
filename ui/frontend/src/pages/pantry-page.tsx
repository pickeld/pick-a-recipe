import { useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { AlarmClock, Package, Pin, Plus, Sparkles, Trash2 } from 'lucide-react'
import { toast } from 'sonner'

import { kitchen, type PantryItem } from '@/lib/kitchen'
import { formatAmount } from '@/lib/kitchen-format'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { EmptyState, PageHeader, errorText } from '@/components/kitchen/bits'

function ExpiryBadge({ item }: { item: PantryItem }) {
  const { t } = useTranslation()
  if (item.days_left === null) return null
  const d = item.days_left
  return (
    <Badge variant={d < 0 ? 'destructive' : d <= 2 ? 'default' : 'outline'} className={cn(d >= 0 && d <= 2 && 'bg-amber-500 text-white')}>
      {d < 0 ? t('pantry.expired', { count: -d }) : d === 0 ? t('pantry.today') : t('pantry.daysLeft', { count: d })}
    </Badge>
  )
}

function PantryRow({ item }: { item: PantryItem }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [qty, setQty] = useState(item.quantity !== null ? String(item.quantity) : '')
  const [unit, setUnit] = useState(item.unit)
  const [expires, setExpires] = useState(item.expires_on ?? '')
  const invalidate = () => qc.invalidateQueries({ queryKey: ['kitchen', 'pantry'] })
  const update = useMutation({
    mutationFn: (patch: Parameters<typeof kitchen.updatePantry>[1]) => kitchen.updatePantry(item.id, patch),
    onSuccess: () => void invalidate(),
    onError: (e) => toast.error(errorText(e)),
  })
  const remove = useMutation({ mutationFn: () => kitchen.deletePantry(item.id), onSuccess: () => void invalidate() })

  return (
    <li className="flex flex-wrap items-center gap-2 px-3 py-2">
      <button
        type="button"
        onClick={() => update.mutate({ staple: !item.staple })}
        title={item.staple ? t('pantry.unstaple') : t('pantry.staple')}
        className={cn('rounded p-1', item.staple ? 'text-primary' : 'text-muted-foreground/40 hover:text-muted-foreground')}
      >
        <Pin className={cn('size-4', item.staple && 'fill-current')} />
      </button>
      <span className="min-w-28 flex-1 font-medium" dir="auto">{item.food}</span>
      {editing ? (
        <form
          className="flex flex-wrap items-center gap-1.5"
          onSubmit={(e) => {
            e.preventDefault()
            update.mutate({ quantity: qty || null, unit, expires_on: expires || null })
            setEditing(false)
          }}
        >
          <Input className="h-7 w-20" inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} placeholder={t('pantry.qty')} />
          <Input className="h-7 w-20" value={unit} onChange={(e) => setUnit(e.target.value)} placeholder={t('pantry.unit')} />
          <Input className="h-7 w-36" type="date" value={expires} onChange={(e) => setExpires(e.target.value)} />
          <Button size="xs" type="submit">{t('common.save')}</Button>
        </form>
      ) : (
        <button type="button" onClick={() => setEditing(true)} className="flex items-center gap-2 rounded px-1 text-sm hover:bg-muted">
          <span className="tabular-nums text-muted-foreground">
            {item.quantity !== null ? `${formatAmount(item.quantity, item.unit)} ${item.unit}` : t('pantry.someAmount')}
          </span>
          <ExpiryBadge item={item} />
        </button>
      )}
      <Button size="icon-xs" variant="ghost" onClick={() => remove.mutate()} aria-label={t('common.delete')}><Trash2 /></Button>
    </li>
  )
}

export function PantryPage() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['kitchen', 'pantry'], queryFn: () => kitchen.pantry(3) })
  const [food, setFood] = useState('')
  const [qty, setQty] = useState('')
  const [unit, setUnit] = useState('')
  const [expires, setExpires] = useState('')
  const [filter, setFilter] = useState('')

  const add = useMutation({
    mutationFn: () => kitchen.addPantry({ food, quantity: qty || null, unit, expires_on: expires || null }),
    onSuccess: () => {
      setFood(''); setQty(''); setUnit(''); setExpires('')
      void qc.invalidateQueries({ queryKey: ['kitchen', 'pantry'] })
    },
    onError: (e) => toast.error(errorText(e)),
  })

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (food.trim()) add.mutate()
  }

  const items = (data?.items ?? []).filter((i) => !filter || i.food.toLowerCase().includes(filter.toLowerCase()))
  const staples = items.filter((i) => i.staple)
  const rest = items.filter((i) => !i.staple)

  return (
    <div className="mx-auto max-w-3xl">
      <PageHeader title={t('pantry.title')} description={t('pantry.description')} />

      {data && data.expiring.length > 0 && (
        <Card className="mb-4 flex-row items-center gap-3 border-amber-500/40 bg-amber-500/10 p-3">
          <AlarmClock className="size-5 shrink-0 text-amber-600" />
          <p className="flex-1 text-sm" dir="auto">
            {t('pantry.expiringSoon', { count: data.expiring.length })}: {data.expiring.map((i) => i.food).join(', ')}
          </p>
          <Button size="sm" asChild><Link to="/souschef?mode=use-it-up"><Sparkles className="size-4" />{t('souschef.useItUp')}</Link></Button>
        </Card>
      )}

      <form onSubmit={submit} className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-[1fr_5rem_5rem_9rem_auto]">
        <Input className="col-span-2 sm:col-span-1" value={food} onChange={(e) => setFood(e.target.value)} placeholder={t('pantry.food')} />
        <Input inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} placeholder={t('pantry.qty')} />
        <Input value={unit} onChange={(e) => setUnit(e.target.value)} placeholder={t('pantry.unit')} />
        <Input type="date" value={expires} onChange={(e) => setExpires(e.target.value)} aria-label={t('pantry.expires')} />
        <Button type="submit" disabled={!food.trim() || add.isPending}><Plus className="size-4" />{t('common.add')}</Button>
      </form>

      {(data?.items.length ?? 0) > 8 && (
        <Input className="mb-3" type="search" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder={t('pantry.filter')} />
      )}

      {isLoading ? (
        <p className="text-muted-foreground">{t('common.loading')}</p>
      ) : !data?.items.length ? (
        <EmptyState icon={<Package />} title={t('pantry.empty')}>{t('pantry.emptyHint')}</EmptyState>
      ) : (
        <div className="grid gap-4">
          {staples.length > 0 && (
            <Card className="gap-0 py-0">
              <p className="border-b px-3 py-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase">{t('pantry.staples')}</p>
              <ul className="divide-y">{staples.map((i) => <PantryRow key={i.id} item={i} />)}</ul>
            </Card>
          )}
          {rest.length > 0 && (
            <Card className="gap-0 py-0">
              <p className="border-b px-3 py-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase">{t('pantry.items')}</p>
              <ul className="divide-y">{rest.map((i) => <PantryRow key={`${i.id}-${i.updated_at}`} item={i} />)}</ul>
            </Card>
          )}
        </div>
      )}
    </div>
  )
}

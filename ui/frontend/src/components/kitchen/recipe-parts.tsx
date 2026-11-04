import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Minus, Pencil, Plus, RotateCcw } from 'lucide-react'

import { NUTRIENT_KEYS, type Ingredient, type Nutrients, type Nutrition, type Step, type UnitSystem } from '@/lib/kitchen'
import { stepServings } from '@/lib/kitchen-format'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Checkbox } from '@/components/kitchen/bits'
import { TimerChip } from '@/components/kitchen/timers'

export function ServingsStepper({ value, onChange }: { value: number; onChange: (v: number) => void }) {
  const { t } = useTranslation()
  const [draft, setDraft] = useState<string | null>(null)
  return (
    <div className="inline-flex items-center rounded-lg border">
      <Button variant="ghost" size="icon-sm" onClick={() => onChange(stepServings(value, -1))} aria-label={t('recipe.fewer')}>
        <Minus />
      </Button>
      <Input
        aria-label={t('recipe.servings')}
        className="h-7 w-14 border-0 text-center shadow-none focus-visible:ring-0 dark:bg-transparent"
        inputMode="decimal"
        value={draft ?? String(value)}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => {
          const n = Number((draft ?? '').replace(',', '.'))
          if (draft !== null && Number.isFinite(n) && n > 0 && n <= 500) onChange(n)
          setDraft(null)
        }}
        onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur() }}
      />
      <Button variant="ghost" size="icon-sm" onClick={() => onChange(stepServings(value, 1))} aria-label={t('recipe.more')}>
        <Plus />
      </Button>
    </div>
  )
}

export function UnitToggle({ value, onChange }: { value: UnitSystem; onChange: (v: UnitSystem) => void }) {
  const { t } = useTranslation()
  return (
    <div className="inline-flex rounded-lg border p-0.5" role="radiogroup" aria-label={t('recipe.units')}>
      {(['original', 'metric', 'us'] as const).map((u) => (
        <button
          key={u}
          type="button"
          role="radio"
          aria-checked={value === u}
          onClick={() => onChange(u)}
          className={cn('rounded-md px-2 py-0.5 text-xs', value === u ? 'bg-primary text-primary-foreground' : 'text-muted-foreground hover:bg-muted')}
        >
          {t(`recipe.unitSystems.${u}`)}
        </button>
      ))}
    </div>
  )
}

export function ingredientText(i: Ingredient): { amount: string; rest: string } {
  const amount = [i.display_quantity, i.display_unit].filter(Boolean).join(' ')
  const rest = [i.food, i.notes ? `(${i.notes})` : ''].filter(Boolean).join(' ')
  if (!i.food) return { amount: '', rest: i.raw }
  return { amount, rest }
}

export function IngredientList({
  ingredients, checked, onToggle, highlight, onSubstitute,
}: {
  ingredients: Ingredient[]
  checked?: Set<number>
  onToggle?: (position: number) => void
  highlight?: number[]
  onSubstitute?: (i: Ingredient) => void
}) {
  const { t } = useTranslation()
  let section = ''
  return (
    <ul className="grid gap-1">
      {ingredients.map((i) => {
        const header = i.section && i.section !== section ? i.section : null
        section = i.section
        const { amount, rest } = ingredientText(i)
        const done = checked?.has(i.position)
        return (
          <li key={i.position}>
            {header && <p className="mt-3 mb-1 text-xs font-semibold tracking-wide text-muted-foreground uppercase">{header}</p>}
            <div className={cn('group flex items-start gap-2 rounded-md px-1 py-1', highlight?.includes(i.position) && 'bg-primary/10')}>
              {onToggle && <Checkbox className="mt-0.5" checked={!!done} onChange={() => onToggle(i.position)} label={i.raw} />}
              <span className={cn('flex-1 leading-snug', done && 'text-muted-foreground line-through')} dir="auto">
                {amount && <span className="font-semibold tabular-nums">{amount} </span>}
                {rest}
                {i.optional && <span className="text-xs text-muted-foreground"> · {t('recipe.optional')}</span>}
              </span>
              {onSubstitute && (
                <button
                  type="button"
                  onClick={() => onSubstitute(i)}
                  className="no-print shrink-0 text-xs text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 focus:opacity-100 hover:text-primary"
                >
                  {t('recipe.substitute')}
                </button>
              )}
            </div>
          </li>
        )
      })}
    </ul>
  )
}

export function StepList({ steps, recipeName }: { steps: Step[]; recipeName: string }) {
  let section = ''
  return (
    <ol className="grid gap-4">
      {steps.map((s, idx) => {
        const header = s.section && s.section !== section ? s.section : null
        section = s.section
        return (
          <li key={s.position}>
            {header && <p className="mb-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase">{header}</p>}
            <div className="flex gap-3">
              <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">{idx + 1}</span>
              <div className="flex-1">
                <p className="leading-relaxed" dir="auto">{s.text}</p>
                {s.timers.length > 0 && (
                  <div className="no-print mt-1.5 flex flex-wrap gap-1.5">
                    {s.timers.map((timer, i) => <TimerChip key={i} timer={timer} recipe={recipeName} />)}
                  </div>
                )}
              </div>
            </div>
          </li>
        )
      })}
    </ol>
  )
}

const UNITS: Record<string, string> = {
  calories: 'kcal', proteinContent: 'g', fatContent: 'g', carbohydrateContent: 'g',
  fiberContent: 'g', sugarContent: 'g', sodiumContent: 'mg', cholesterolContent: 'mg',
}

function round(v: number | undefined) {
  if (v === undefined || v === null) return '–'
  return v >= 10 ? String(Math.round(v)) : String(Math.round(v * 10) / 10)
}

export function NutritionPanel({
  nutrition, servings, onSave, onReset,
}: {
  nutrition: Nutrition | null
  servings: number | null
  onSave?: (values: Nutrients) => Promise<unknown>
  onReset?: () => Promise<unknown>
}) {
  const { t } = useTranslation()
  const [mode, setMode] = useState<'serving' | 'total'>('serving')
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<Record<string, string>>({})
  const values = nutrition ? (mode === 'serving' ? nutrition.per_serving : nutrition.total) : {}
  const coverage = nutrition && nutrition.usable ? nutrition.matched / nutrition.usable : null

  const openEdit = () => {
    setDraft(Object.fromEntries(NUTRIENT_KEYS.map((k) => [k, nutrition?.per_serving[k] !== undefined ? String(nutrition.per_serving[k]) : ''])))
    setEditing(true)
  }

  return (
    <div className="grid gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="inline-flex rounded-lg border p-0.5 text-xs">
          {(['serving', 'total'] as const).map((m) => (
            <button key={m} type="button" onClick={() => setMode(m)} className={cn('rounded-md px-2 py-0.5', mode === m ? 'bg-muted font-medium' : 'text-muted-foreground')}>
              {m === 'serving' ? t('nutrition.perServing') : t('nutrition.total', { count: servings ?? 0 })}
            </button>
          ))}
        </div>
        <div className="no-print flex items-center gap-1">
          {onSave && <Button size="icon-xs" variant="ghost" onClick={openEdit} aria-label={t('nutrition.edit')}><Pencil /></Button>}
          {onReset && nutrition?.source === 'manual' && (
            <Button size="icon-xs" variant="ghost" onClick={() => void onReset()} aria-label={t('nutrition.recalculate')}><RotateCcw /></Button>
          )}
        </div>
      </div>
      {nutrition ? (
        <>
          <dl className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-sm sm:grid-cols-4">
            {NUTRIENT_KEYS.map((k) => (
              <div key={k} className={cn('flex flex-col', k === 'calories' && 'col-span-2 sm:col-span-1')}>
                <dt className="text-xs text-muted-foreground">{t(`nutrition.keys.${k}`)}</dt>
                <dd className={cn('tabular-nums', k === 'calories' && 'text-lg font-semibold')}>
                  {round(values[k])} <span className="text-xs text-muted-foreground">{UNITS[k]}</span>
                </dd>
              </div>
            ))}
          </dl>
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            {nutrition.source === 'auto' && coverage !== null && (
              <Badge variant={coverage >= 0.8 ? 'secondary' : 'outline'} className={cn(coverage < 0.5 && 'border-amber-500 text-amber-600')}>
                {t('nutrition.coverage', { matched: nutrition.matched, usable: nutrition.usable })}
              </Badge>
            )}
            <span>{t(`nutrition.sources.${nutrition.source}`)}</span>
          </div>
          {nutrition.source === 'auto' && nutrition.breakdown.some((b) => !b.matched) && (
            <p className="text-xs text-muted-foreground">
              {t('nutrition.unmatched')}: <span dir="auto">{nutrition.breakdown.filter((b) => !b.matched).map((b) => b.food).join(', ')}</span>
            </p>
          )}
        </>
      ) : (
        <p className="text-sm text-muted-foreground">{t('nutrition.none')}</p>
      )}
      <Dialog open={editing} onOpenChange={setEditing}>
        <DialogContent>
          <DialogHeader><DialogTitle>{t('nutrition.editTitle')}</DialogTitle></DialogHeader>
          <div className="grid grid-cols-2 gap-3">
            {NUTRIENT_KEYS.map((k) => (
              <div key={k} className="grid gap-1">
                <Label htmlFor={`n-${k}`}>{t(`nutrition.keys.${k}`)} ({UNITS[k]})</Label>
                <Input id={`n-${k}`} inputMode="decimal" value={draft[k] ?? ''} onChange={(e) => setDraft((d) => ({ ...d, [k]: e.target.value }))} />
              </div>
            ))}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEditing(false)}>{t('common.cancel')}</Button>
            <Button
              onClick={async () => {
                const out: Nutrients = {}
                for (const k of NUTRIENT_KEYS) {
                  const n = Number((draft[k] ?? '').replace(',', '.'))
                  if (draft[k] && Number.isFinite(n) && n >= 0) out[k] = n
                }
                await onSave?.(out)
                setEditing(false)
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

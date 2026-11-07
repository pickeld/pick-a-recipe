import { useEffect, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2 } from 'lucide-react'

import type { Recipe, RecipePatch, SchemaRecipe } from '@/lib/kitchen'
import { isoToMinutes, minutesToIso } from '@/lib/kitchen-format'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { ChipsInput, errorText } from '@/components/kitchen/bits'

export interface EditorValues {
  name: string
  description: string
  servings: string
  prep: string
  cook: string
  total: string
  source_url: string
  tags: string[]
  ingredients: string
  steps: string
}

export const EMPTY_EDITOR: EditorValues = {
  name: '', description: '', servings: '4', prep: '', cook: '', total: '', source_url: '', tags: [], ingredients: '', steps: '',
}

const minutes = (iso: string | undefined) => {
  const m = isoToMinutes(iso)
  return m ? String(m) : ''
}

export function valuesFromRecipe(r: Recipe): EditorValues {
  return {
    name: r.name,
    description: r.description,
    servings: r.base_servings ? String(r.base_servings) : '',
    prep: minutes(r.prep_time),
    cook: minutes(r.cook_time),
    total: minutes(r.total_time),
    source_url: r.source_url,
    tags: r.tags,
    ingredients: r.ingredients.map((i) => i.raw).join('\n'),
    steps: r.steps.map((s) => s.text).join('\n'),
  }
}

export function valuesFromSchema(s: SchemaRecipe): EditorValues {
  const lines = s.recipeIngredients?.length
    ? s.recipeIngredients.map((i) => i.raw || [i.quantity, i.unit, i.food, i.notes].filter(Boolean).join(' '))
    : s.recipeIngredient ?? []
  const servings = /\d+(?:[.,]\d+)?/.exec(String(s.recipeYield ?? ''))?.[0] ?? ''
  const keywords = s.keywords
  const tags = Array.isArray(keywords) ? keywords.map(String) : typeof keywords === 'string' ? keywords.split(',').map((k) => k.trim()).filter(Boolean) : []
  return {
    name: s.name ?? '',
    description: s.description ?? '',
    servings,
    prep: minutes(s.prepTime as string | undefined),
    cook: minutes(s.cookTime as string | undefined),
    total: minutes(s.totalTime as string | undefined),
    source_url: typeof s.url === 'string' ? s.url : '',
    tags,
    ingredients: lines.join('\n'),
    steps: (s.recipeInstructions ?? []).map((x) => (typeof x === 'string' ? x : x.text)).join('\n'),
  }
}

const lines = (text: string) => text.split('\n').map((l) => l.trim()).filter(Boolean)

export function valuesToPatch(v: EditorValues): RecipePatch & { servings: number | null } {
  const servings = Number(v.servings.replace(',', '.'))
  return {
    name: v.name.trim(),
    description: v.description.trim(),
    servings: Number.isFinite(servings) && servings > 0 ? servings : null,
    prep_time: minutesToIso(Number(v.prep) || 0),
    cook_time: minutesToIso(Number(v.cook) || 0),
    total_time: minutesToIso(Number(v.total) || (Number(v.prep) || 0) + (Number(v.cook) || 0)),
    source_url: v.source_url.trim(),
    tags: v.tags,
    ingredients: lines(v.ingredients),
    steps: lines(v.steps),
  }
}

export function valuesToSchema(v: EditorValues): SchemaRecipe {
  const p = valuesToPatch(v)
  return {
    name: p.name ?? '',
    description: p.description,
    recipeYield: p.servings ? String(p.servings) : '',
    prepTime: p.prep_time,
    cookTime: p.cook_time,
    totalTime: p.total_time,
    url: p.source_url,
    keywords: p.tags,
    recipeIngredient: p.ingredients,
    recipeInstructions: (p.steps ?? []).map((text) => ({ text })),
  }
}

export function RecipeEditor({
  open, onOpenChange, title, description, initial, submitLabel, onSubmit,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  description?: string
  initial: EditorValues
  submitLabel?: string
  onSubmit: (values: EditorValues) => Promise<unknown>
}) {
  const { t } = useTranslation()
  const [values, setValues] = useState(initial)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (open) {
      setValues(initial)
      setError('')
    }
  }, [open, initial])

  const set = <K extends keyof EditorValues>(key: K, value: EditorValues[K]) => setValues((v) => ({ ...v, [key]: value }))

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!values.name.trim()) {
      setError(t('editor.nameRequired'))
      return
    }
    if (!lines(values.ingredients).length && !lines(values.steps).length) {
      setError(t('editor.contentRequired'))
      return
    }
    setBusy(true)
    setError('')
    try {
      await onSubmit(values)
      onOpenChange(false)
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-2xl">
        <form onSubmit={submit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            {description && <DialogDescription>{description}</DialogDescription>}
          </DialogHeader>
          <div className="grid gap-1.5">
            <Label htmlFor="ed-name">{t('editor.name')}</Label>
            <Input id="ed-name" dir="auto" value={values.name} onChange={(e) => set('name', e.target.value)} autoFocus />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="ed-desc">{t('editor.description')}</Label>
            <Textarea id="ed-desc" dir="auto" rows={2} value={values.description} onChange={(e) => set('description', e.target.value)} />
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div className="grid gap-1.5">
              <Label htmlFor="ed-serv">{t('editor.servings')}</Label>
              <Input id="ed-serv" inputMode="decimal" value={values.servings} onChange={(e) => set('servings', e.target.value)} />
            </div>
            {(['prep', 'cook', 'total'] as const).map((k) => (
              <div key={k} className="grid gap-1.5">
                <Label htmlFor={`ed-${k}`}>{t(`editor.${k}`)}</Label>
                <Input id={`ed-${k}`} inputMode="numeric" placeholder={t('editor.minutes')} value={values[k]} onChange={(e) => set(k, e.target.value.replace(/\D/g, ''))} />
              </div>
            ))}
          </div>
          <div className="grid gap-1.5">
            <Label>{t('editor.tags')}</Label>
            <ChipsInput value={values.tags} onChange={(v) => set('tags', v)} placeholder={t('editor.tagsHint')} />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="ed-ing">{t('editor.ingredients')}</Label>
            <Textarea id="ed-ing" dir="auto" rows={8} value={values.ingredients} onChange={(e) => set('ingredients', e.target.value)} placeholder={t('editor.ingredientsHint')} />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="ed-steps">{t('editor.steps')}</Label>
            <Textarea id="ed-steps" dir="auto" rows={8} value={values.steps} onChange={(e) => set('steps', e.target.value)} placeholder={t('editor.stepsHint')} />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="ed-url">{t('editor.source')}</Label>
            <Input id="ed-url" dir="ltr" type="url" value={values.source_url} onChange={(e) => set('source_url', e.target.value)} placeholder="https://" />
          </div>
          {error && <p className="text-sm text-destructive">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>{t('common.cancel')}</Button>
            <Button type="submit" disabled={busy}>
              {busy && <Loader2 className="size-4 animate-spin" />}
              {submitLabel ?? t('common.save')}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

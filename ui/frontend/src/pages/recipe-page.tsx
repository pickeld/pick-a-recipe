import { useEffect, useMemo, useRef, useState } from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  ArrowLeft, CalendarPlus, CheckCircle2, ChefHat, Copy, Download, ExternalLink, FolderPlus, Heart, ImagePlus,
  Link2, Link2Off, ListPlus, Loader2, MoreHorizontal, Pencil, Printer, Sparkles, Star, Tags, Trash2, Wand2,
} from 'lucide-react'
import { toast } from 'sonner'

import { isUnavailable, kitchen, type Ingredient, type Recipe, type UnitSystem } from '@/lib/kitchen'
import { isoToMinutes } from '@/lib/kitchen-format'
import { cn } from '@/lib/utils'
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import {
  DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator,
  DropdownMenuSub, DropdownMenuSubContent, DropdownMenuSubTrigger, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Label } from '@/components/ui/label'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { Textarea } from '@/components/ui/textarea'
import { Minutes, Rating, RecipeImage, errorText, useMinutesLabel } from '@/components/kitchen/bits'
import { AddToPlanDialog } from '@/components/kitchen/plan-dialog'
import { RecipeEditor, valuesFromRecipe, valuesToPatch } from '@/components/kitchen/recipe-editor'
import { IngredientList, NutritionPanel, ServingsStepper, StepList, UnitToggle } from '@/components/kitchen/recipe-parts'

const UNITS_KEY = 'kitchen-units'

export function useUnitPreference(): [UnitSystem, (u: UnitSystem) => void] {
  const [units, setUnits] = useState<UnitSystem>(() => {
    const v = localStorage.getItem(UNITS_KEY)
    return v === 'metric' || v === 'us' ? v : 'original'
  })
  return [units, (u) => { localStorage.setItem(UNITS_KEY, u); setUnits(u) }]
}

function SubstituteDialog({ ingredient, recipeId, onClose }: { ingredient: Ingredient | null; recipeId: string; onClose: () => void }) {
  const { t } = useTranslation()
  const { data, isLoading, error } = useQuery({
    queryKey: ['kitchen', 'substitute', recipeId, ingredient?.food],
    queryFn: () => kitchen.substitute(ingredient!.food || ingredient!.raw, recipeId),
    enabled: !!ingredient,
    retry: false,
    staleTime: Infinity,
  })
  return (
    <Dialog open={!!ingredient} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('substitute.title', { food: ingredient?.food || ingredient?.raw })}</DialogTitle>
          <DialogDescription>{t('substitute.description')}</DialogDescription>
        </DialogHeader>
        {isLoading && <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />{t('souschef.thinking')}</div>}
        {error && <p className="text-sm text-destructive">{isUnavailable(error) ? t('souschef.unavailable') : errorText(error)}</p>}
        <ul className="grid gap-3">
          {data?.options.map((o, i) => (
            <li key={i} className="rounded-lg border p-3" dir="auto">
              <p className="font-medium">{o.use} <span className="text-sm font-normal text-muted-foreground">· {o.amount}</span></p>
              {o.notes && <p className="text-sm text-muted-foreground">{o.notes}</p>}
            </li>
          ))}
        </ul>
      </DialogContent>
    </Dialog>
  )
}

function ReviewDialog({ recipeId, open, onClose }: { recipeId: string; open: boolean; onClose: () => void }) {
  const { t } = useTranslation()
  const { data, isLoading, error } = useQuery({
    queryKey: ['kitchen', 'review', recipeId],
    queryFn: () => kitchen.review(recipeId),
    enabled: open,
    retry: false,
  })
  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader><DialogTitle>{t('review.title')}</DialogTitle></DialogHeader>
        {isLoading && <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />{t('souschef.thinking')}</div>}
        {error && <p className="text-sm text-destructive">{isUnavailable(error) ? t('souschef.unavailable') : errorText(error)}</p>}
        {data && (
          <div className="grid gap-3" dir="auto">
            <p>{data.summary}</p>
            {data.issues.map((issue, i) => (
              <div key={i} className="rounded-lg border p-3">
                <Badge variant="outline" className="mb-1">{t(`review.kinds.${issue.kind}`)}</Badge>
                <p className="text-sm">{issue.detail}</p>
                <p className="text-sm text-muted-foreground">→ {issue.suggestion}</p>
              </div>
            ))}
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

function CookedDialog({ recipe, servings, open, onClose }: { recipe: Recipe; servings: number | null; open: boolean; onClose: () => void }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [deduct, setDeduct] = useState(false)
  const [note, setNote] = useState('')
  const mark = useMutation({
    mutationFn: () => kitchen.markCooked(recipe.id, { servings, note, deduct_pantry: deduct }),
    onSuccess: (res) => {
      toast.success(deduct ? t('cooked.doneDeducted', { count: res.deducted.length }) : t('cooked.done'))
      void qc.invalidateQueries({ queryKey: ['kitchen', 'recipe', recipe.id] })
      void qc.invalidateQueries({ queryKey: ['kitchen', 'recipes'] })
      onClose()
    },
    onError: (e) => toast.error(errorText(e)),
  })
  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent>
        <DialogHeader><DialogTitle>{t('cooked.title')}</DialogTitle></DialogHeader>
        <div className="flex items-center justify-between gap-3">
          <Label htmlFor="deduct">{t('cooked.deduct')}</Label>
          <Switch id="deduct" checked={deduct} onCheckedChange={setDeduct} />
        </div>
        <Textarea placeholder={t('cooked.note')} value={note} onChange={(e) => setNote(e.target.value)} rows={2} />
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>{t('common.cancel')}</Button>
          <Button onClick={() => mark.mutate()} disabled={mark.isPending}>{t('cooked.save')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function RecipePage() {
  const { recipeId = '' } = useParams()
  const { t } = useTranslation()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const minutesLabel = useMinutesLabel()
  const [units, setUnits] = useUnitPreference()
  const [servings, setServings] = useState<number | null>(null)
  const [checked, setChecked] = useState<Set<number>>(new Set())
  const [imageIndex, setImageIndex] = useState(0)
  const [editing, setEditing] = useState(false)
  const [planOpen, setPlanOpen] = useState(false)
  const [cookedOpen, setCookedOpen] = useState(false)
  const [reviewOpen, setReviewOpen] = useState(false)
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [substitute, setSubstitute] = useState<Ingredient | null>(null)
  const [aiBusy, setAiBusy] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    setServings(null)
    setChecked(new Set())
    setImageIndex(0)
  }, [recipeId])

  const key = ['kitchen', 'recipe', recipeId, servings, units] as const
  const { data: recipe, isLoading, error } = useQuery({
    queryKey: key,
    queryFn: () => kitchen.getRecipe(recipeId, { servings, units }),
    placeholderData: keepPreviousData,
  })
  const { data: collections } = useQuery({ queryKey: ['kitchen', 'collections'], queryFn: kitchen.collections })
  const [note, setNote] = useState<string | null>(null)

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['kitchen', 'recipe', recipeId] })
    void qc.invalidateQueries({ queryKey: ['kitchen', 'recipes'] })
  }

  const meta = useMutation({
    mutationFn: (m: Parameters<typeof kitchen.setMeta>[1]) => kitchen.setMeta(recipeId, m),
    onSuccess: invalidate,
    onError: (e) => toast.error(errorText(e)),
  })

  const target = servings ?? recipe?.base_servings ?? 1
  const initialValues = useMemo(() => (recipe ? valuesFromRecipe(recipe) : null), [recipe])

  if (isLoading) {
    return (
      <div className="mx-auto grid max-w-5xl gap-4">
        <Skeleton className="h-64 rounded-xl" />
        <Skeleton className="h-8 w-1/2" />
        <Skeleton className="h-40" />
      </div>
    )
  }
  if (error || !recipe) {
    return <p className="text-destructive">{error ? errorText(error) : t('recipe.notFound')}</p>
  }

  const missing = recipe.ingredients.filter((i) => !checked.has(i.position))
  const image = recipe.images[imageIndex]?.url ?? recipe.image_url

  const addToShopping = async (onlyMissing: boolean) => {
    try {
      const res = await kitchen.generateShopping({
        recipe_id: recipe.id, servings: target,
        only_foods: onlyMissing ? missing.map((i) => i.food).filter(Boolean) : undefined,
      })
      void qc.invalidateQueries({ queryKey: ['kitchen', 'shopping'] })
      toast.success(t('shopping.addedFromRecipe', { count: res.added, excluded: res.excluded }), {
        action: { label: t('nav.shopping'), onClick: () => navigate('/shopping') },
      })
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  const runAi = async (name: string, fn: () => Promise<unknown>) => {
    setAiBusy(name)
    try {
      await fn()
    } catch (err) {
      toast.error(isUnavailable(err) ? t('souschef.unavailable') : errorText(err))
    } finally {
      setAiBusy(null)
    }
  }

  const share = async () => {
    try {
      const { url } = await kitchen.share(recipe.id)
      const full = `${window.location.origin}${url}`
      await navigator.clipboard?.writeText(full).catch(() => undefined)
      toast.success(t('share.copied'), { description: full, duration: 10000 })
      invalidate()
    } catch (err) {
      toast.error(errorText(err))
    }
  }

  return (
    <div className="recipe-print mx-auto max-w-5xl">
      <div className="no-print mb-3 flex items-center justify-between gap-2">
        <Button variant="ghost" size="sm" asChild>
          <Link to="/library"><ArrowLeft className="size-4 rtl:rotate-180" />{t('nav.library')}</Link>
        </Button>
        <div className="flex flex-wrap items-center gap-1.5">
          <Button size="sm" asChild>
            <Link to={`/library/${recipe.id}/cook?servings=${target}`}><ChefHat className="size-4" />{t('recipe.cook')}</Link>
          </Button>
          <Button size="sm" variant="outline" onClick={() => setPlanOpen(true)}><CalendarPlus className="size-4" />{t('recipe.plan')}</Button>
          <Button size="sm" variant="outline" onClick={() => setCookedOpen(true)}><CheckCircle2 className="size-4" />{t('recipe.cooked')}</Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button size="icon-sm" variant="outline" aria-label={t('common.more')}><MoreHorizontal /></Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-56">
              <DropdownMenuItem onSelect={() => setEditing(true)}><Pencil />{t('common.edit')}</DropdownMenuItem>
              <DropdownMenuItem onSelect={() => fileRef.current?.click()}><ImagePlus />{t('recipe.addPhoto')}</DropdownMenuItem>
              {collections && collections.collections.length > 0 && (
                <DropdownMenuSub>
                  <DropdownMenuSubTrigger><FolderPlus />{t('collections.addTo')}</DropdownMenuSubTrigger>
                  <DropdownMenuSubContent>
                    {collections.collections.map((c) => {
                      const member = recipe.collections.some((rc) => rc.id === c.id)
                      return (
                        <DropdownMenuCheckboxItem
                          key={c.id}
                          checked={member}
                          onSelect={(e) => e.preventDefault()}
                          onCheckedChange={async (v) => {
                            await kitchen.setMembership(c.id, recipe.id, v)
                            invalidate()
                            void qc.invalidateQueries({ queryKey: ['kitchen', 'collections'] })
                          }}
                        >
                          {c.name}
                        </DropdownMenuCheckboxItem>
                      )
                    })}
                  </DropdownMenuSubContent>
                </DropdownMenuSub>
              )}
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={() => void share()}><Link2 />{t('share.create')}</DropdownMenuItem>
              {!!recipe.share_links && (
                <DropdownMenuItem onSelect={async () => { await kitchen.unshare(recipe.id); invalidate(); toast.success(t('share.revoked')) }}>
                  <Link2Off />{t('share.revoke', { count: recipe.share_links })}
                </DropdownMenuItem>
              )}
              <DropdownMenuItem asChild><a href={kitchen.exportUrl(recipe.id)}><Download />{t('recipe.exportJson')}</a></DropdownMenuItem>
              <DropdownMenuItem onSelect={() => window.print()}><Printer />{t('recipe.print')}</DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onSelect={() => setReviewOpen(true)}><Wand2 />{t('review.menu')}</DropdownMenuItem>
              <DropdownMenuItem
                disabled={!!aiBusy}
                onSelect={() => void runAi('tags', async () => {
                  const res = await kitchen.suggestTags(recipe.id, true)
                  invalidate()
                  toast.success(res.tags.length ? t('aiTags.added', { tags: res.tags.join(', ') }) : t('aiTags.none'))
                })}
              >
                <Tags />{t('aiTags.menu')}
              </DropdownMenuItem>
              <DropdownMenuItem
                disabled={!!aiBusy}
                onSelect={() => void runAi('link', async () => {
                  await kitchen.linkSteps(recipe.id)
                  invalidate()
                  toast.success(t('linkSteps.done'))
                })}
              >
                <Sparkles />{t('linkSteps.menu')}
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onSelect={() => setDeleteOpen(true)}><Trash2 />{t('common.delete')}</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
      <input
        ref={fileRef}
        type="file"
        accept="image/*"
        className="hidden"
        onChange={async (e) => {
          const file = e.target.files?.[0]
          e.target.value = ''
          if (!file) return
          try {
            await kitchen.uploadImage(recipe.id, file, !recipe.images.length)
            invalidate()
          } catch (err) {
            toast.error(errorText(err))
          }
        }}
      />

      <div className="grid gap-6 md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <div className="grid content-start gap-2">
          <RecipeImage src={image} alt={recipe.name} className="aspect-[4/3] w-full rounded-xl" />
          {recipe.images.length > 1 && (
            <div className="no-print flex gap-2 overflow-x-auto">
              {recipe.images.map((img, i) => (
                <div key={img.id} className="group relative shrink-0">
                  <button type="button" onClick={() => setImageIndex(i)} className={cn('overflow-hidden rounded-md ring-offset-2', i === imageIndex && 'ring-2 ring-primary')}>
                    <img src={img.url} alt="" className="size-14 object-cover" />
                  </button>
                  <div className="absolute -top-1 -end-1 hidden gap-0.5 group-hover:flex">
                    {i > 0 && (
                      <button type="button" title={t('recipe.makeCover')} className="rounded-full bg-background p-0.5 shadow" onClick={async () => { await kitchen.setCover(recipe.id, img.id); setImageIndex(0); invalidate() }}>
                        <Star className="size-3" />
                      </button>
                    )}
                    <button type="button" title={t('common.delete')} className="rounded-full bg-background p-0.5 shadow" onClick={async () => { await kitchen.deleteImage(recipe.id, img.id); setImageIndex(0); invalidate() }}>
                      <Trash2 className="size-3" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="grid content-start gap-3">
          <div className="flex items-start justify-between gap-3">
            <h2 className="text-2xl font-semibold tracking-tight" dir="auto">{recipe.name}</h2>
            <Button
              variant="ghost"
              size="icon"
              className="no-print shrink-0"
              aria-pressed={recipe.user.favorite}
              aria-label={t('library.favorite')}
              onClick={() => meta.mutate({ favorite: !recipe.user.favorite })}
            >
              <Heart className={cn('size-5', recipe.user.favorite && 'fill-rose-500 text-rose-500')} />
            </Button>
          </div>
          {recipe.description && <p className="text-muted-foreground" dir="auto">{recipe.description}</p>}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
            <Minutes value={recipe.total_minutes} />
            {isoToMinutes(recipe.prep_time) && <span className="text-muted-foreground">{t('recipe.prep')}: {minutesLabel(isoToMinutes(recipe.prep_time))}</span>}
            <div className="no-print"><Rating value={recipe.user.rating} onChange={(v) => meta.mutate({ rating: v })} /></div>
            {recipe.user.cook_count > 0 && (
              <span className="text-muted-foreground">{t('recipe.cookedTimes', { count: recipe.user.cook_count, date: recipe.user.last_cooked })}</span>
            )}
            {recipe.source_url && (
              <a href={recipe.source_url} target="_blank" rel="noopener noreferrer" className="no-print inline-flex items-center gap-1 text-primary hover:underline">
                <ExternalLink className="size-3.5" />{t('recipe.source')}
              </a>
            )}
          </div>
          {recipe.tags.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {recipe.tags.map((tag) => <Badge key={tag} variant="secondary">{tag}</Badge>)}
            </div>
          )}
          <Textarea
            className="no-print"
            rows={2}
            placeholder={t('recipe.notePlaceholder')}
            value={note ?? recipe.user.note}
            onChange={(e) => setNote(e.target.value)}
            onBlur={() => { if (note !== null && note !== recipe.user.note) meta.mutate({ note }) }}
          />
        </div>
      </div>

      <div className="mt-6 grid gap-6 md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <Card className="h-fit">
          <CardHeader className="gap-3">
            <CardTitle>{t('recipe.ingredients')}</CardTitle>
            <div className="no-print flex flex-wrap items-center gap-2">
              <ServingsStepper value={target} onChange={setServings} />
              <span className="text-sm text-muted-foreground">{t('recipe.servingsLabel', { count: target })}</span>
              <UnitToggle value={units} onChange={setUnits} />
            </div>
            {servings !== null && recipe.base_servings && servings !== recipe.base_servings && (
              <p className="no-print text-xs text-muted-foreground">
                {t('recipe.scaledFrom', { count: recipe.base_servings })}{' '}
                <button type="button" className="text-primary hover:underline" onClick={() => setServings(null)}>{t('recipe.reset')}</button>
              </p>
            )}
          </CardHeader>
          <CardContent className="grid gap-3">
            <IngredientList
              ingredients={recipe.ingredients}
              checked={checked}
              onToggle={(p) => setChecked((s) => { const n = new Set(s); if (n.has(p)) n.delete(p); else n.add(p); return n })}
              onSubstitute={setSubstitute}
            />
            <div className="no-print flex flex-wrap gap-2 pt-1">
              <Button size="sm" variant="outline" onClick={() => void addToShopping(false)}><ListPlus className="size-4" />{t('recipe.addAll')}</Button>
              {checked.size > 0 && missing.length > 0 && (
                <Button size="sm" variant="outline" onClick={() => void addToShopping(true)}>
                  <Copy className="size-4" />{t('recipe.addMissing', { count: missing.length })}
                </Button>
              )}
            </div>
            <p className="no-print text-xs text-muted-foreground">{t('recipe.checkHint')}</p>
          </CardContent>
        </Card>

        <div className="grid content-start gap-6">
          <Card>
            <CardHeader><CardTitle>{t('recipe.steps')}</CardTitle></CardHeader>
            <CardContent>
              {recipe.steps.length ? <StepList steps={recipe.steps} recipeName={recipe.name} /> : <p className="text-sm text-muted-foreground">{t('recipe.noSteps')}</p>}
            </CardContent>
          </Card>
          <Card>
            <CardHeader><CardTitle>{t('nutrition.title')}</CardTitle></CardHeader>
            <CardContent>
              <NutritionPanel
                nutrition={recipe.nutrition}
                servings={target}
                onSave={async (v) => { await kitchen.setNutrition(recipe.id, v); invalidate() }}
                onReset={async () => { await kitchen.resetNutrition(recipe.id); invalidate() }}
              />
            </CardContent>
          </Card>
        </div>
      </div>

      {initialValues && (
        <RecipeEditor
          open={editing}
          onOpenChange={setEditing}
          title={t('editor.editTitle')}
          initial={initialValues}
          onSubmit={async (v) => {
            await kitchen.updateRecipe(recipe.id, valuesToPatch(v))
            invalidate()
            toast.success(t('common.saved'))
          }}
        />
      )}
      <AddToPlanDialog open={planOpen} onOpenChange={setPlanOpen} recipeId={recipe.id} recipeName={recipe.name} defaultServings={target} />
      <CookedDialog recipe={recipe} servings={target} open={cookedOpen} onClose={() => setCookedOpen(false)} />
      <ReviewDialog recipeId={recipe.id} open={reviewOpen} onClose={() => setReviewOpen(false)} />
      <SubstituteDialog ingredient={substitute} recipeId={recipe.id} onClose={() => setSubstitute(null)} />
      <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('recipe.deleteTitle')}</AlertDialogTitle>
            <AlertDialogDescription>{t('recipe.deleteBody', { name: recipe.name })}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t('common.cancel')}</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={async () => {
                await kitchen.deleteRecipe(recipe.id)
                void qc.invalidateQueries({ queryKey: ['kitchen', 'recipes'] })
                navigate('/library')
              }}
            >
              {t('common.delete')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      {aiBusy && (
        <div className="no-print fixed bottom-4 end-4 flex items-center gap-2 rounded-full border bg-popover px-3 py-1.5 text-sm shadow-lg">
          <Loader2 className="size-4 animate-spin" />{t('souschef.thinking')}
        </div>
      )}
    </div>
  )
}

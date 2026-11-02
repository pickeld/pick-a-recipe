import { useEffect, useMemo, useState } from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { BookOpen, Download, FolderHeart, Heart, History, Import, Plus, Search, SlidersHorizontal, Trash2 } from 'lucide-react'
import { toast } from 'sonner'

import { kitchen, type ListParams, type RecipeSummary } from '@/lib/kitchen'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { ChipsInput, EmptyState, Minutes, PageHeader, Rating, RecipeImage, errorText } from '@/components/kitchen/bits'
import { EMPTY_EDITOR, RecipeEditor, valuesToPatch } from '@/components/kitchen/recipe-editor'

const PAGE = 48
const SORTS = ['newest', 'name', 'rating', 'quickest', 'recently_cooked', 'least_recent', 'random'] as const
const TIMES = [15, 30, 45, 60, 90] as const

type TagState = 'include' | 'exclude'

function useDebounced<T>(value: T, ms = 250): T {
  const [v, setV] = useState(value)
  useEffect(() => {
    const id = setTimeout(() => setV(value), ms)
    return () => clearTimeout(id)
  }, [value, ms])
  return v
}

export function RecipeCard({ recipe }: { recipe: RecipeSummary }) {
  const { t } = useTranslation()
  return (
    <Link to={`/library/${recipe.id}`} className="group block focus-visible:outline-none">
      <Card className="h-full gap-0 overflow-hidden p-0 transition-shadow group-hover:shadow-md group-focus-visible:ring-2 group-focus-visible:ring-ring">
        <div className="relative aspect-[4/3]">
          <RecipeImage src={recipe.image_url} alt="" className="size-full" />
          {recipe.favorite && (
            <Heart className="absolute top-2 end-2 size-5 fill-rose-500 text-rose-500 drop-shadow" aria-label={t('library.favorite')} />
          )}
        </div>
        <div className="flex flex-1 flex-col gap-1.5 p-3">
          <h3 className="line-clamp-2 font-medium leading-snug" dir="auto">{recipe.name}</h3>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
            <Minutes value={recipe.total_minutes} />
            {recipe.calories ? <span className="text-muted-foreground">{t('library.kcal', { value: Math.round(recipe.calories) })}</span> : null}
            {recipe.rating ? <Rating value={recipe.rating} size="sm" /> : null}
          </div>
          {recipe.tags.length > 0 && (
            <div className="mt-auto flex flex-wrap gap-1 pt-1">
              {recipe.tags.slice(0, 3).map((tag) => (
                <Badge key={tag} variant="outline" className="text-[11px] font-normal">{tag}</Badge>
              ))}
            </div>
          )}
        </div>
      </Card>
    </Link>
  )
}

function CollectionsDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['kitchen', 'collections'], queryFn: kitchen.collections, enabled: open })
  const [name, setName] = useState('')
  const refresh = () => qc.invalidateQueries({ queryKey: ['kitchen', 'collections'] })
  const create = useMutation({
    mutationFn: () => kitchen.createCollection(name),
    onSuccess: () => { setName(''); void refresh() },
    onError: (e) => toast.error(errorText(e)),
  })
  const remove = useMutation({
    mutationFn: (id: number) => kitchen.deleteCollection(id),
    onSuccess: () => void refresh(),
  })
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader><DialogTitle>{t('collections.title')}</DialogTitle></DialogHeader>
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (name.trim()) create.mutate() }}>
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={t('collections.newName')} />
          <Button type="submit" disabled={!name.trim() || create.isPending}><Plus className="size-4" />{t('common.add')}</Button>
        </form>
        <ul className="divide-y">
          {data?.collections.map((c) => (
            <li key={c.id} className="flex items-center justify-between gap-2 py-2">
              <span dir="auto">{c.name} <span className="text-xs text-muted-foreground">({c.count})</span></span>
              <Button variant="ghost" size="icon-sm" aria-label={t('common.delete')} onClick={() => remove.mutate(c.id)}>
                <Trash2 className="size-4" />
              </Button>
            </li>
          ))}
          {data && !data.collections.length && <li className="py-2 text-sm text-muted-foreground">{t('collections.empty')}</li>}
        </ul>
      </DialogContent>
    </Dialog>
  )
}

export function LibraryPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const [query, setQuery] = useState(params.get('q') ?? '')
  const q = useDebounced(query)
  const [sort, setSort] = useState<string>(params.get('sort') ?? 'newest')
  const [favorites, setFavorites] = useState(params.get('favorites') === '1')
  const [maxTime, setMaxTime] = useState<number | null>(Number(params.get('max_time')) || null)
  const [tagStates, setTagStates] = useState<Record<string, TagState>>({})
  const [excludeFoods, setExcludeFoods] = useState<string[]>([])
  const [collection, setCollection] = useState<number | null>(Number(params.get('collection')) || null)
  const [showFilters, setShowFilters] = useState(false)
  const [limit, setLimit] = useState(PAGE)
  const [editorOpen, setEditorOpen] = useState(false)
  const [collectionsOpen, setCollectionsOpen] = useState(false)

  useEffect(() => {
    const next = new URLSearchParams()
    if (q) next.set('q', q)
    if (sort !== 'newest') next.set('sort', sort)
    if (favorites) next.set('favorites', '1')
    if (maxTime) next.set('max_time', String(maxTime))
    if (collection) next.set('collection', String(collection))
    setParams(next, { replace: true })
    setLimit(PAGE)
  }, [q, sort, favorites, maxTime, collection, setParams])

  const listParams: ListParams = useMemo(() => ({
    q,
    sort,
    favorites,
    max_time: maxTime ?? undefined,
    collection: collection ?? undefined,
    tags: Object.entries(tagStates).filter(([, s]) => s === 'include').map(([n]) => n),
    exclude_tags: Object.entries(tagStates).filter(([, s]) => s === 'exclude').map(([n]) => n),
    exclude_ingredients: excludeFoods,
    limit,
  }), [q, sort, favorites, maxTime, collection, tagStates, excludeFoods, limit])

  const { data, isLoading, isFetching, error } = useQuery({
    queryKey: ['kitchen', 'recipes', listParams],
    queryFn: () => kitchen.listRecipes(listParams),
    placeholderData: keepPreviousData,
  })
  const { data: tags } = useQuery({ queryKey: ['kitchen', 'tags'], queryFn: kitchen.tags })
  const { data: collections } = useQuery({ queryKey: ['kitchen', 'collections'], queryFn: kitchen.collections })

  const cycleTag = (name: string) =>
    setTagStates((s) => {
      const next = { ...s }
      if (!s[name]) next[name] = 'include'
      else if (s[name] === 'include') next[name] = 'exclude'
      else delete next[name]
      return next
    })

  const filtersActive = favorites || maxTime || collection || excludeFoods.length || Object.keys(tagStates).length

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title={t('library.title')}
        description={data ? t('library.count', { count: data.total }) : undefined}
        actions={
          <>
            <Button variant="outline" size="sm" asChild><Link to="/cook-log"><History className="size-4" />{t('nav.cookLog')}</Link></Button>
            <Button variant="outline" size="sm" onClick={() => setCollectionsOpen(true)}><FolderHeart className="size-4" />{t('collections.title')}</Button>
            <Button variant="outline" size="sm" asChild><a href={kitchen.libraryExportUrl()}><Download className="size-4" />{t('library.export')}</a></Button>
            <Button variant="outline" size="sm" asChild><Link to="/import"><Import className="size-4" />{t('nav.import')}</Link></Button>
            <Button size="sm" onClick={() => setEditorOpen(true)}><Plus className="size-4" />{t('library.new')}</Button>
          </>
        }
      />

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="relative min-w-56 flex-1">
          <Search className="pointer-events-none absolute start-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t('library.search')} className="ps-8" type="search" />
        </div>
        <Select value={sort} onValueChange={setSort}>
          <SelectTrigger className="w-44" aria-label={t('library.sort')}><SelectValue /></SelectTrigger>
          <SelectContent>
            {SORTS.map((s) => <SelectItem key={s} value={s}>{t(`library.sorts.${s}`)}</SelectItem>)}
          </SelectContent>
        </Select>
        <Button variant={favorites ? 'default' : 'outline'} size="sm" onClick={() => setFavorites((f) => !f)} aria-pressed={favorites}>
          <Heart className={cn('size-4', favorites && 'fill-current')} />{t('library.favorites')}
        </Button>
        <Button variant={showFilters || filtersActive ? 'secondary' : 'outline'} size="sm" onClick={() => setShowFilters((s) => !s)} aria-expanded={showFilters}>
          <SlidersHorizontal className="size-4" />{t('library.filters')}
        </Button>
      </div>

      {showFilters && (
        <Card className="mb-4 gap-3 p-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-sm text-muted-foreground">{t('library.maxTime')}</span>
            {TIMES.map((m) => (
              <Button key={m} size="xs" variant={maxTime === m ? 'default' : 'outline'} onClick={() => setMaxTime(maxTime === m ? null : m)}>
                {t('time.m', { count: m })}
              </Button>
            ))}
            {collections && collections.collections.length > 0 && (
              <Select value={collection ? String(collection) : 'all'} onValueChange={(v) => setCollection(v === 'all' ? null : Number(v))}>
                <SelectTrigger size="sm" className="ms-auto w-48"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">{t('library.allCollections')}</SelectItem>
                  {collections.collections.map((c) => <SelectItem key={c.id} value={String(c.id)}>{c.name}</SelectItem>)}
                </SelectContent>
              </Select>
            )}
          </div>
          {tags && tags.tags.length > 0 && (
            <div>
              <p className="mb-1.5 text-xs text-muted-foreground">{t('library.tagsHint')}</p>
              <div className="flex flex-wrap gap-1.5">
                {tags.tags.slice(0, 40).map((tag) => {
                  const state = tagStates[tag.name]
                  return (
                    <button key={tag.name} type="button" onClick={() => cycleTag(tag.name)}>
                      <Badge
                        variant={state === 'include' ? 'default' : state === 'exclude' ? 'destructive' : 'outline'}
                        className={cn('cursor-pointer', state === 'exclude' && 'line-through')}
                      >
                        {tag.name} <span className="opacity-60">{tag.count}</span>
                      </Badge>
                    </button>
                  )
                })}
              </div>
            </div>
          )}
          <div className="grid gap-1.5">
            <span className="text-xs text-muted-foreground">{t('library.excludeIngredients')}</span>
            <ChipsInput value={excludeFoods} onChange={setExcludeFoods} placeholder={t('library.excludeHint')} />
          </div>
        </Card>
      )}

      {error ? (
        <p className="text-destructive">{errorText(error)}</p>
      ) : isLoading ? (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {Array.from({ length: 10 }, (_, i) => <Skeleton key={i} className="aspect-[3/4] rounded-xl" />)}
        </div>
      ) : data && data.items.length ? (
        <>
          <div className={cn('grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5', isFetching && 'opacity-70')}>
            {data.items.map((r) => <RecipeCard key={r.id} recipe={r} />)}
          </div>
          {data.total > data.items.length && (
            <div className="mt-6 flex justify-center">
              <Button variant="outline" onClick={() => setLimit((l) => l + PAGE)} disabled={isFetching}>{t('library.more')}</Button>
            </div>
          )}
        </>
      ) : (
        <EmptyState icon={<BookOpen />} title={q || filtersActive ? t('library.noMatches') : t('library.empty')}>
          {!q && !filtersActive && t('library.emptyHint')}
        </EmptyState>
      )}

      <RecipeEditor
        open={editorOpen}
        onOpenChange={setEditorOpen}
        title={t('library.new')}
        initial={EMPTY_EDITOR}
        onSubmit={async (v) => {
          const recipe = await kitchen.createRecipe({ ...valuesToPatch(v), name: v.name, ingredients: valuesToPatch(v).ingredients ?? [], steps: valuesToPatch(v).steps ?? [] })
          toast.success(t('library.created'))
          navigate(`/library/${recipe.id}`)
        }}
      />
      <CollectionsDialog open={collectionsOpen} onOpenChange={setCollectionsOpen} />
    </div>
  )
}

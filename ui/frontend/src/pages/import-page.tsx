import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'
import { Camera, FileUp, Link2, Loader2, Pencil, Server, Upload } from 'lucide-react'
import { toast } from 'sonner'

import { api } from '@/lib/api'
import { isUnavailable, kitchen, type OnDuplicate, type SchemaRecipe, type StagedImport } from '@/lib/kitchen'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Progress } from '@/components/ui/progress'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { Checkbox, PageHeader, errorText } from '@/components/kitchen/bits'
import { RecipeEditor, valuesFromSchema, valuesToSchema } from '@/components/kitchen/recipe-editor'

function DuplicateSelect({ value, onChange }: { value: OnDuplicate; onChange: (v: OnDuplicate) => void }) {
  const { t } = useTranslation()
  return (
    <div className="flex items-center gap-2 text-sm">
      <Label className="shrink-0">{t('import.duplicates')}</Label>
      <Select value={value} onValueChange={(v) => onChange(v as OnDuplicate)}>
        <SelectTrigger className="h-8 w-40"><SelectValue /></SelectTrigger>
        <SelectContent>
          {(['skip', 'replace', 'keep_both'] as const).map((o) => <SelectItem key={o} value={o}>{t(`import.onDuplicate.${o}`)}</SelectItem>)}
        </SelectContent>
      </Select>
    </div>
  )
}

function ReviewPanel({ staged, onDone }: { staged: StagedImport; onDone: () => void }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [selected, setSelected] = useState<Set<number>>(() => new Set(staged.recipes.filter((r) => !r.duplicate_of).map((r) => r.index)))
  const [onDuplicate, setOnDuplicate] = useState<OnDuplicate>('skip')
  const [useImage, setUseImage] = useState(true)
  const [override, setOverride] = useState<SchemaRecipe | null>(null)
  const [editing, setEditing] = useState(false)
  const editorInitial = useMemo(() => valuesFromSchema(override ?? staged.draft ?? { name: '' }), [override, staged.draft])

  const commit = useMutation({
    mutationFn: () => kitchen.commitImport({
      token: staged.token,
      indexes: [...selected],
      on_duplicate: onDuplicate,
      overrides: override ? { 0: override } : undefined,
      use_image: useImage,
    }),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ['kitchen', 'recipes'] })
      if (r.failed.length) toast.warning(t('import.someFailed', { count: r.failed.length }), { description: r.failed.map((f) => `${f.name}: ${f.error}`).join('\n') })
      toast.success(t('import.imported', { count: r.imported }), { description: r.skipped ? t('import.skipped', { count: r.skipped }) : undefined })
      onDone()
      if (r.ids.length === 1) navigate(`/library/${r.ids[0]}`)
    },
    onError: (e) => toast.error(errorText(e)),
  })

  const allOn = selected.size === staged.recipes.length
  const hasDuplicates = staged.recipes.some((r) => r.duplicate_of && selected.has(r.index))

  return (
    <Card>
      <CardHeader>
        <CardTitle>{t('import.review', { count: staged.count })}</CardTitle>
        <CardDescription>{t('import.reviewHint')}</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        {staged.recipes.length > 1 && (
          <label className="flex items-center gap-2 text-sm">
            <Checkbox checked={allOn} onChange={(v) => setSelected(new Set(v ? staged.recipes.map((r) => r.index) : []))} />
            {t('import.selectAll')}
          </label>
        )}
        <ul className="max-h-96 divide-y overflow-y-auto rounded-lg border">
          {staged.recipes.map((r) => (
            <li key={r.index} className="flex items-center gap-3 px-3 py-2">
              <Checkbox
                checked={selected.has(r.index)}
                label={r.name}
                onChange={(v) => setSelected((s) => {
                  const next = new Set(s)
                  if (v) next.add(r.index)
                  else next.delete(r.index)
                  return next
                })}
              />
              <div className="min-w-0 flex-1">
                <p className="truncate font-medium" dir="auto">{(r.index === 0 && override?.name) || r.name}</p>
                <p className="text-xs text-muted-foreground">{t('import.counts', { ingredients: r.ingredients, steps: r.steps })}</p>
              </div>
              {r.duplicate_of && (
                <Badge variant="outline" asChild><Link to={`/library/${r.duplicate_of}`}>{t('import.duplicate')}</Link></Badge>
              )}
              {staged.draft && r.index === 0 && (
                <Button size="xs" variant="outline" onClick={() => setEditing(true)}><Pencil className="size-3.5" />{t('common.edit')}</Button>
              )}
            </li>
          ))}
        </ul>
        <div className="flex flex-wrap items-center gap-4">
          {hasDuplicates && <DuplicateSelect value={onDuplicate} onChange={setOnDuplicate} />}
          {staged.has_image && (
            <label className="flex items-center gap-2 text-sm">
              <Switch checked={useImage} onCheckedChange={setUseImage} />
              {t('import.useImage')}
            </label>
          )}
          <div className="ms-auto flex gap-2">
            <Button variant="ghost" onClick={onDone}>{t('common.cancel')}</Button>
            <Button onClick={() => commit.mutate()} disabled={!selected.size || commit.isPending}>
              {commit.isPending && <Loader2 className="size-4 animate-spin" />}
              {t('import.save', { count: selected.size })}
            </Button>
          </div>
        </div>
      </CardContent>
      {staged.draft && (
        <RecipeEditor
          open={editing}
          onOpenChange={setEditing}
          title={t('import.editDraft')}
          initial={editorInitial}
          submitLabel={t('common.save')}
          onSubmit={async (values) => {
            setOverride({ ...(staged.draft ?? {}), ...valuesToSchema(values) })
            setEditing(false)
          }}
        />
      )}
    </Card>
  )
}

function RemoteImport({ kind }: { kind: 'mealie' | 'tandoor' }) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [jobId, setJobId] = useState<string | null>(null)
  const [onDuplicate, setOnDuplicate] = useState<OnDuplicate>('skip')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const list = useQuery({ queryKey: ['kitchen', 'import', kind], queryFn: () => kitchen.remoteList(kind), enabled: false, retry: false })
  const job = useQuery({
    queryKey: ['kitchen', 'import', 'job', jobId],
    queryFn: () => kitchen.importJob(jobId!),
    enabled: !!jobId,
    refetchInterval: (q) => (q.state.data && q.state.data.status !== 'running' ? false : 1000),
  })

  useEffect(() => {
    if (job.data && job.data.status !== 'running') void qc.invalidateQueries({ queryKey: ['kitchen', 'recipes'] })
  }, [job.data, qc])

  const start = async (ids: string[] | 'all') => {
    try {
      const r = await kitchen.remoteImport(kind, { ids, on_duplicate: onDuplicate })
      setJobId(r.job_id)
    } catch (e) {
      toast.error(errorText(e))
    }
  }

  const recipes = list.data?.recipes ?? []
  const fresh = recipes.filter((r) => !r.duplicate_of)
  const j = job.data

  return (
    <div className="grid gap-3">
      {j ? (
        <div className="grid gap-2 rounded-lg border p-3">
          <div className="flex items-center gap-2 text-sm">
            {j.status === 'running' && <Loader2 className="size-4 animate-spin" />}
            <span className="font-medium">{t(`import.jobStatus.${j.status}`)}</span>
            <span className="ms-auto tabular-nums text-muted-foreground">{j.done}/{j.total}</span>
          </div>
          <Progress value={j.total ? (j.done / j.total) * 100 : 0} />
          <p className="text-xs text-muted-foreground">
            {t('import.imported', { count: j.imported })} · {t('import.skipped', { count: j.skipped })}
            {j.failed.length > 0 && ` · ${t('import.someFailed', { count: j.failed.length })}`}
          </p>
          {j.error && <p className="text-sm text-destructive">{j.error}</p>}
          <div className="flex gap-2">
            {j.status === 'running'
              ? <Button size="sm" variant="outline" onClick={() => void kitchen.cancelImportJob(j.id)}>{t('import.stop')}</Button>
              : <Button size="sm" variant="outline" onClick={() => { setJobId(null); void list.refetch() }}>{t('common.back')}</Button>}
          </div>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" onClick={() => void list.refetch()} disabled={list.isFetching}>
              {list.isFetching && <Loader2 className="size-4 animate-spin" />}{t('import.browse')}
            </Button>
            <DuplicateSelect value={onDuplicate} onChange={setOnDuplicate} />
            <Button className="ms-auto" onClick={() => void start('all')}><Upload className="size-4" />{t('import.importAll')}</Button>
          </div>
          {list.error && <p className="text-sm text-destructive">{errorText(list.error)}</p>}
          {recipes.length > 0 && (
            <>
              <p className="text-sm text-muted-foreground">{t('import.remoteCount', { count: recipes.length, fresh: fresh.length })}</p>
              <ul className="max-h-96 divide-y overflow-y-auto rounded-lg border">
                {recipes.map((r) => (
                  <li key={r.source_id} className="flex items-center gap-3 px-3 py-1.5 text-sm">
                    <Checkbox
                      checked={selected.has(r.source_id)}
                      label={r.name}
                      onChange={(v) => setSelected((s) => {
                        const next = new Set(s)
                        if (v) next.add(r.source_id)
                        else next.delete(r.source_id)
                        return next
                      })}
                    />
                    <span className="flex-1 truncate" dir="auto">{r.name}</span>
                    {r.duplicate_of && <Badge variant="outline">{t('import.duplicate')}</Badge>}
                  </li>
                ))}
              </ul>
              <Button disabled={!selected.size} onClick={() => void start([...selected])}>{t('import.save', { count: selected.size })}</Button>
            </>
          )}
        </>
      )}
    </div>
  )
}

export function ImportPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [staged, setStaged] = useState<StagedImport | null>(null)
  const [url, setUrl] = useState('')
  const [json, setJson] = useState('')
  const { data: sources } = useQuery({ queryKey: ['kitchen', 'import', 'sources'], queryFn: kitchen.importSources })

  const stage = useMutation({
    mutationFn: async (fn: () => Promise<StagedImport | { pipeline: true; duplicate_of: string | null }>) => fn(),
    onSuccess: async (r) => {
      if ('pipeline' in r) {
        if (r.duplicate_of) toast.info(t('import.alreadyHave'), { action: { label: t('common.open'), onClick: () => navigate(`/library/${r.duplicate_of}`) } })
        const job = await api.createJob(url.trim())
        toast.success(t('import.sentToPipeline'))
        navigate(`/jobs/${job.job_id}`)
        return
      }
      if (!r.count) toast.error(t('import.nothingFound'))
      else setStaged(r)
    },
    onError: (e) => toast.error(isUnavailable(e) ? t('souschef.unavailable') : errorText(e)),
  })

  const busy = stage.isPending
  const remoteKinds = (['mealie', 'tandoor'] as const).filter((k) => sources?.admin && sources[k])

  return (
    <div className="mx-auto grid max-w-3xl gap-4">
      <PageHeader title={t('import.title')} description={t('import.description')} />

      {staged ? (
        <ReviewPanel staged={staged} onDone={() => setStaged(null)} />
      ) : (
        <Tabs defaultValue="url">
          <TabsList className="flex-wrap">
            <TabsTrigger value="url"><Link2 className="size-4" />{t('import.tabs.url')}</TabsTrigger>
            <TabsTrigger value="file"><FileUp className="size-4" />{t('import.tabs.file')}</TabsTrigger>
            <TabsTrigger value="photos"><Camera className="size-4" />{t('import.tabs.photos')}</TabsTrigger>
            <TabsTrigger value="json">{t('import.tabs.json')}</TabsTrigger>
            {remoteKinds.map((k) => <TabsTrigger key={k} value={k}><Server className="size-4" />{k === 'mealie' ? 'Mealie' : 'Tandoor'}</TabsTrigger>)}
          </TabsList>

          <TabsContent value="url">
            <Card><CardContent className="grid gap-3 pt-6">
              <p className="text-sm text-muted-foreground">{t('import.urlHint')}</p>
              <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); if (url.trim()) stage.mutate(() => kitchen.importUrl(url.trim())) }}>
                <Input type="url" dir="ltr" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://" />
                <Button type="submit" disabled={busy || !url.trim()}>{busy && <Loader2 className="size-4 animate-spin" />}{t('import.fetch')}</Button>
              </form>
            </CardContent></Card>
          </TabsContent>

          <TabsContent value="file">
            <Card><CardContent className="grid gap-3 pt-6">
              <p className="text-sm text-muted-foreground">{t('import.fileHint')}</p>
              <Input
                type="file"
                accept=".paprikarecipes,.paprikarecipe,.pdf,.json,application/pdf,application/json"
                disabled={busy}
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  e.target.value = ''
                  if (f) stage.mutate(() => kitchen.importFile(f))
                }}
              />
              {busy && <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />{t('import.reading')}</p>}
            </CardContent></Card>
          </TabsContent>

          <TabsContent value="photos">
            <Card><CardContent className="grid gap-3 pt-6">
              <p className="text-sm text-muted-foreground">{t('import.photosHint')}</p>
              <Input
                type="file"
                accept="image/*"
                multiple
                disabled={busy}
                onChange={(e) => {
                  const files = [...(e.target.files ?? [])].slice(0, 8)
                  e.target.value = ''
                  if (files.length) stage.mutate(() => kitchen.importPhotos(files))
                }}
              />
              {busy && <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" />{t('import.reading')}</p>}
            </CardContent></Card>
          </TabsContent>

          <TabsContent value="json">
            <Card><CardContent className="grid gap-3 pt-6">
              <p className="text-sm text-muted-foreground">{t('import.jsonHint')}</p>
              <Textarea dir="ltr" rows={10} className="font-mono text-xs" value={json} onChange={(e) => setJson(e.target.value)} placeholder='{"@type": "Recipe", ...}' />
              <Button className="justify-self-end" disabled={busy || !json.trim()} onClick={() => stage.mutate(() => kitchen.importSchema(json))}>{t('import.fetch')}</Button>
            </CardContent></Card>
          </TabsContent>

          {remoteKinds.map((k) => (
            <TabsContent key={k} value={k}>
              <Card><CardContent className="pt-6"><RemoteImport kind={k} /></CardContent></Card>
            </TabsContent>
          ))}
        </Tabs>
      )}
    </div>
  )
}

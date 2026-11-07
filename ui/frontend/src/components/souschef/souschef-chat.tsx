import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'
import { ChefHat, Heart, Lightbulb, RotateCcw, Send, SlidersHorizontal, Sparkles, Wand2, X } from 'lucide-react'
import { toast } from 'sonner'

import { isUnavailable, kitchen, type Candidate, type ChatTurn, type SchemaRecipe, type SuggestResult } from '@/lib/kitchen'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { ChipsInput, Minutes, RecipeImage, errorText } from '@/components/kitchen/bits'
import { RecipeEditor, valuesFromSchema, valuesToSchema } from '@/components/kitchen/recipe-editor'

type Turn =
  | { role: 'user'; text: string }
  | { role: 'assistant'; result: SuggestResult }

const TIME_OPTIONS = ['any', '15', '30', '45', '60', '90']

function PickCard({ candidate, why, substitutions }: {
  candidate: Candidate
  why?: string
  substitutions?: { missing: string; use: string }[]
}) {
  const { t } = useTranslation()
  return (
    <Card className="gap-0 overflow-hidden py-0">
      <Link to={`/library/${candidate.id}`} className="flex gap-3 p-3 hover:bg-muted/50">
        <RecipeImage src={candidate.image_url} alt={candidate.name} className="size-16 shrink-0 rounded-md" />
        <div className="min-w-0 flex-1">
          <div className="flex items-start gap-2">
            <p className="flex-1 font-medium leading-tight" dir="auto">{candidate.name}</p>
            {candidate.favorite && <Heart className="size-4 shrink-0 fill-rose-500 text-rose-500" />}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs">
            <Minutes value={candidate.total_minutes} />
            {candidate.have.length + candidate.missing.length > 0 && (
              <Badge variant="outline">{t('souschef.coverage', { pct: Math.round(candidate.coverage * 100) })}</Badge>
            )}
          </div>
          {why && <p className="mt-1.5 text-sm" dir="auto">{why}</p>}
        </div>
      </Link>
      {(candidate.have.length > 0 || candidate.missing.length > 0) && (
        <div className="flex flex-wrap gap-1 border-t px-3 py-2 text-xs">
          {candidate.have.map((h) => <Badge key={`h-${h}`} variant="secondary" className="bg-emerald-500/15 text-emerald-700 dark:text-emerald-400">{h}</Badge>)}
          {candidate.missing.map((m) => <Badge key={`m-${m}`} variant="outline" className="text-muted-foreground">{t('souschef.missing', { name: m })}</Badge>)}
        </div>
      )}
      {substitutions && substitutions.length > 0 && (
        <ul className="border-t px-3 py-2 text-xs text-muted-foreground">
          {substitutions.map((s) => (
            <li key={s.missing} dir="auto"><Lightbulb className="me-1 inline size-3.5" />{t('souschef.swap', { missing: s.missing, use: s.use })}</li>
          ))}
        </ul>
      )}
    </Card>
  )
}

function AssistantTurn({ result }: { result: SuggestResult }) {
  const { t } = useTranslation()
  const picked = new Set(result.picks.map((p) => p.recipe.id))
  // Without anything to match on, the rest of the pool is just the library in some order.
  const others = result.candidates
    .filter((c) => !picked.has(c.id) && (c.have.length > 0 || (c.mentioned?.length ?? 0) > 0))
    .slice(0, 6)
  return (
    <div className="grid gap-3">
      {result.reply && (
        <div className="flex gap-2">
          <ChefHat className="mt-0.5 size-5 shrink-0 text-primary" />
          <p className="whitespace-pre-line text-sm" dir="auto">{result.reply}</p>
        </div>
      )}
      {result.error && <p className="text-xs text-muted-foreground">{t('souschef.aiFallback')}</p>}
      {result.picks.length > 0 && (
        <div className="grid gap-2">
          {result.picks.map((p) => <PickCard key={p.recipe.id} candidate={p.recipe} why={p.why} substitutions={p.substitutions} />)}
        </div>
      )}
      {!result.picks.length && !result.candidates.length && (
        <p className="text-sm text-muted-foreground">{t('souschef.noMatches')}</p>
      )}
      {others.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-muted-foreground">{t('souschef.moreMatches', { count: others.length })}</summary>
          <div className="mt-2 grid gap-2">
            {others.map((c) => <PickCard key={c.id} candidate={c} />)}
          </div>
        </details>
      )}
    </div>
  )
}

export function SousChefChat({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [ingredients, setIngredients] = useState<string[]>([])
  const [diets, setDiets] = useState<string[]>([])
  const [maxTime, setMaxTime] = useState('any')
  const [showOptions, setShowOptions] = useState(false)
  const [message, setMessage] = useState('')
  const [turns, setTurns] = useState<Turn[]>([])
  const [busy, setBusy] = useState<'suggest' | 'invent' | null>(null)
  const [draft, setDraft] = useState<SchemaRecipe | null>(null)
  const endRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  const { data: dietData } = useQuery({ queryKey: ['kitchen', 'souschef', 'diets'], queryFn: kitchen.diets, staleTime: Infinity })
  const maxMinutes = maxTime === 'any' ? null : Number(maxTime)
  const draftValues = useMemo(() => valuesFromSchema(draft ?? { name: '' }), [draft])
  const optionCount = ingredients.length + diets.length + (maxMinutes ? 1 : 0)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [turns, busy])

  useEffect(() => {
    if (open) inputRef.current?.focus()
  }, [open])

  const history = (): ChatTurn[] =>
    turns.slice(-8).map((tu) => (tu.role === 'user'
      ? { role: 'user', text: tu.text }
      : { role: 'assistant', text: tu.result.reply || tu.result.picks.map((p) => p.recipe.name).join(', ') }))

  const run = async (kind: 'suggest' | 'invent', text: string) => {
    if (busy) return
    setBusy(kind)
    setShowOptions(false)
    if (text) setTurns((prev) => [...prev, { role: 'user', text }])
    try {
      if (kind === 'invent') {
        const { draft: d } = await kitchen.invent({ ingredients, message: text, diets, max_minutes: maxMinutes })
        setDraft(d)
      } else {
        const result = await kitchen.suggest({
          ingredients, message: text, history: history(), max_minutes: maxMinutes, diets,
          lang: i18n.language,
        })
        setTurns((prev) => [...prev, { role: 'assistant', result }])
      }
    } catch (err) {
      toast.error(isUnavailable(err) ? t('souschef.unavailable') : errorText(err))
    } finally {
      setBusy(null)
    }
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const text = message.trim()
    setMessage('')
    void run('suggest', text)
  }

  const invent = () => {
    const text = message.trim()
    setMessage('')
    void run('invent', text)
  }

  return (
    <>
      <header className="flex items-center gap-2 border-b px-3 py-2">
        <div className="flex size-8 shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground">
          <ChefHat className="size-4" />
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold leading-tight">{t('nav.souschef')}</p>
          <p className="truncate text-xs text-muted-foreground">{t('souschef.description')}</p>
        </div>
        <Button
          variant={showOptions ? 'secondary' : 'ghost'}
          size="icon"
          className="relative size-8"
          aria-pressed={showOptions}
          aria-label={t('souschef.options')}
          title={t('souschef.options')}
          onClick={() => setShowOptions((v) => !v)}
        >
          <SlidersHorizontal className="size-4" />
          {optionCount > 0 && (
            <span className="absolute -top-0.5 -end-0.5 flex size-4 items-center justify-center rounded-full bg-primary text-[10px] text-primary-foreground">
              {optionCount}
            </span>
          )}
        </Button>
        <Button
          variant="ghost"
          size="icon"
          className="size-8"
          disabled={!turns.length || !!busy}
          aria-label={t('souschef.newChat')}
          title={t('souschef.newChat')}
          onClick={() => setTurns([])}
        >
          <RotateCcw className="size-4" />
        </Button>
        <Button variant="ghost" size="icon" className="size-8" aria-label={t('souschef.close')} title={t('souschef.close')} onClick={onClose}>
          <X className="size-4" />
        </Button>
      </header>

      {showOptions && (
        <div className="grid gap-3 border-b bg-muted/30 p-3">
          <div className="grid gap-1.5">
            <Label>{t('souschef.haveIngredients')}</Label>
            <ChipsInput value={ingredients} onChange={setIngredients} placeholder={t('souschef.ingredientsPlaceholder')} />
          </div>
          <div className="grid gap-1.5">
            <Label>{t('souschef.maxTime')}</Label>
            <Select value={maxTime} onValueChange={setMaxTime}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {TIME_OPTIONS.map((o) => (
                  <SelectItem key={o} value={o}>{o === 'any' ? t('souschef.anyTime') : t('time.m', { count: Number(o) })}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {dietData && dietData.diets.length > 0 && (
            <div className="grid gap-1.5">
              <Label>{t('souschef.diet')}</Label>
              <div className="flex flex-wrap gap-1.5">
                {dietData.diets.map((d) => {
                  const on = diets.includes(d)
                  return (
                    <button
                      key={d}
                      type="button"
                      aria-pressed={on}
                      onClick={() => setDiets(on ? diets.filter((x) => x !== d) : [...diets, d])}
                      className={cn('rounded-full border px-2.5 py-0.5 text-xs', on ? 'border-primary bg-primary text-primary-foreground' : 'hover:bg-muted')}
                    >
                      {t(`diets.${d}`)}
                    </button>
                  )
                })}
              </div>
            </div>
          )}
          <Button size="sm" onClick={() => void run('suggest', '')} disabled={!!busy}><Sparkles className="size-4" />{t('souschef.findRecipes')}</Button>
        </div>
      )}

      <div className="flex-1 space-y-5 overflow-y-auto p-4">
        {!turns.length && !busy && (
          <div className="flex h-full flex-col items-center justify-center gap-3 py-8 text-center text-muted-foreground">
            <ChefHat className="size-10" />
            <p className="max-w-xs text-sm">{t('souschef.intro')}</p>
            <div className="flex flex-wrap justify-center gap-2">
              <Button size="sm" onClick={() => void run('suggest', '')}><Sparkles className="size-4" />{t('souschef.findRecipes')}</Button>
              <Button size="sm" variant="outline" onClick={invent}><Wand2 className="size-4" />{t('souschef.inventNew')}</Button>
            </div>
          </div>
        )}
        {turns.map((tu, i) => (tu.role === 'user' ? (
          <div key={i} className="flex justify-end">
            <p className="max-w-[85%] rounded-2xl bg-primary px-3 py-2 text-sm text-primary-foreground" dir="auto">{tu.text}</p>
          </div>
        ) : (
          <AssistantTurn key={i} result={tu.result} />
        )))}
        {busy && <p className="animate-pulse text-sm text-muted-foreground">{t('souschef.thinking')}</p>}
        <div ref={endRef} />
      </div>

      <form onSubmit={submit} className="flex items-end gap-2 border-t p-3">
        <Textarea
          ref={inputRef}
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              e.currentTarget.form?.requestSubmit()
            }
          }}
          rows={1}
          className="max-h-32 min-h-10 resize-none"
          placeholder={t('souschef.messagePlaceholder')}
          dir="auto"
        />
        <Button type="button" variant="outline" size="icon" disabled={!!busy} onClick={invent} aria-label={t('souschef.inventNew')} title={t('souschef.inventNew')}>
          <Wand2 />
        </Button>
        <Button type="submit" size="icon" disabled={!!busy} aria-label={t('souschef.send')}><Send className="rtl:-scale-x-100" /></Button>
      </form>

      <RecipeEditor
        open={!!draft}
        onOpenChange={(v) => !v && setDraft(null)}
        title={t('souschef.draftTitle')}
        description={t('souschef.draftHint')}
        initial={draftValues}
        submitLabel={t('souschef.saveDraft')}
        onSubmit={async (values) => {
          const { id } = await kitchen.saveDraft({ ...draft, ...valuesToSchema(values) })
          setDraft(null)
          void qc.invalidateQueries({ queryKey: ['kitchen', 'recipes'] })
          toast.success(t('souschef.draftSaved'))
          navigate(`/library/${id}`)
        }}
      />
    </>
  )
}

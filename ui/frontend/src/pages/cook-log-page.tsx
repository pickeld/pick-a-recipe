import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { ChevronLeft, ChevronRight, History, Trash2 } from 'lucide-react'

import { kitchen } from '@/lib/kitchen'
import { parseISODate } from '@/lib/kitchen-format'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { EmptyState, PageHeader } from '@/components/kitchen/bits'

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <Card className="gap-1 p-4">
      <p className="text-2xl font-semibold tabular-nums">{value}</p>
      <p className="text-xs text-muted-foreground">{label}</p>
    </Card>
  )
}

export function CookLogPage() {
  const { t, i18n } = useTranslation()
  const qc = useQueryClient()
  const [year, setYear] = useState(() => new Date().getFullYear())
  const stats = useQuery({ queryKey: ['kitchen', 'stats', year], queryFn: () => kitchen.stats(year) })
  const log = useQuery({ queryKey: ['kitchen', 'cook-log'], queryFn: () => kitchen.cookLog() })
  const remove = useMutation({
    mutationFn: (id: number) => kitchen.deleteCookEntry(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['kitchen', 'cook-log'] })
      void qc.invalidateQueries({ queryKey: ['kitchen', 'stats'] })
      void qc.invalidateQueries({ queryKey: ['kitchen', 'recipes'] })
    },
  })

  const monthFmt = new Intl.DateTimeFormat(i18n.language, { month: 'short' })
  const dateFmt = new Intl.DateTimeFormat(i18n.language, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
  const s = stats.data
  const peak = Math.max(1, ...(s?.by_month ?? [0]))

  return (
    <div className="mx-auto grid max-w-4xl gap-6">
      <PageHeader
        title={t('cookLog.title')}
        description={t('cookLog.description')}
        actions={
          <div className="flex items-center gap-1">
            <Button size="icon-sm" variant="ghost" onClick={() => setYear((y) => y - 1)} aria-label={t('cookLog.prevYear')}><ChevronLeft className="rtl:rotate-180" /></Button>
            <span className="w-12 text-center font-medium tabular-nums">{year}</span>
            <Button size="icon-sm" variant="ghost" onClick={() => setYear((y) => y + 1)} disabled={year >= new Date().getFullYear()} aria-label={t('cookLog.nextYear')}><ChevronRight className="rtl:rotate-180" /></Button>
          </div>
        }
      />

      {s && (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label={t('cookLog.timesCooked')} value={s.times_cooked} />
            <Stat label={t('cookLog.distinct')} value={s.distinct_recipes} />
            <Stat label={t('cookLog.librarySize')} value={s.library_size} />
            <Stat label={t('cookLog.added')} value={s.added_this_year} />
          </div>
          <div className="grid gap-4 md:grid-cols-2">
            <Card>
              <CardHeader><CardTitle className="text-base">{t('cookLog.byMonth')}</CardTitle></CardHeader>
              <CardContent>
                <div className="flex h-32 items-end gap-1" role="img" aria-label={t('cookLog.byMonth')}>
                  {s.by_month.map((n, m) => (
                    <div key={m} className="flex flex-1 flex-col items-center gap-1">
                      <div className="w-full rounded-t bg-primary/80" style={{ height: `${(n / peak) * 100}%`, minHeight: n ? 4 : 0 }} title={String(n)} />
                      <span className="text-[10px] text-muted-foreground">{monthFmt.format(new Date(year, m, 1))}</span>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle className="text-base">{t('cookLog.top')}</CardTitle></CardHeader>
              <CardContent>
                {s.top.length ? (
                  <ol className="grid gap-1.5 text-sm">
                    {s.top.map((r, i) => (
                      <li key={r.recipe_id} className="flex items-center gap-2">
                        <span className="w-5 text-muted-foreground tabular-nums">{i + 1}.</span>
                        <Link to={`/library/${r.recipe_id}`} className="flex-1 truncate hover:underline" dir="auto">{r.name}</Link>
                        <span className="tabular-nums text-muted-foreground">×{r.times}</span>
                      </li>
                    ))}
                  </ol>
                ) : <p className="text-sm text-muted-foreground">{t('cookLog.none')}</p>}
              </CardContent>
            </Card>
          </div>
        </>
      )}

      <section>
        <h3 className="mb-2 font-semibold">{t('cookLog.history')}</h3>
        {!log.data?.entries.length ? (
          <EmptyState icon={<History />} title={t('cookLog.none')}>{t('cookLog.hint')}</EmptyState>
        ) : (
          <ul className="divide-y rounded-lg border">
            {log.data.entries.map((e) => (
              <li key={e.id} className="flex items-center gap-3 px-3 py-2 text-sm">
                <span className="w-36 shrink-0 text-muted-foreground">{dateFmt.format(parseISODate(e.cooked_on))}</span>
                <Link to={`/library/${e.recipe_id}`} className="flex-1 truncate font-medium hover:underline" dir="auto">{e.name}</Link>
                {e.servings && <span className="text-muted-foreground">{t('recipe.servingsLabel', { count: e.servings })}</span>}
                {e.note && <span className="hidden max-w-48 truncate text-muted-foreground sm:inline" dir="auto">{e.note}</span>}
                <Button size="icon-xs" variant="ghost" onClick={() => remove.mutate(e.id)} aria-label={t('common.delete')}><Trash2 /></Button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}

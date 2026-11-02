import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Search } from 'lucide-react'

import { kitchen, type RecipeSummary } from '@/lib/kitchen'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Minutes, RecipeImage } from '@/components/kitchen/bits'

/** Choose a library recipe, or type a free-text meal ("Eat out", "Pizza night"). */
export function RecipePicker({
  open, onOpenChange, title, onPick, allowText = true,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  title: string
  onPick: (choice: { recipe: RecipeSummary } | { title: string }, servings: number | null) => void
  allowText?: boolean
}) {
  const { t } = useTranslation()
  const [q, setQ] = useState('')
  const [debounced, setDebounced] = useState('')
  const [servings, setServings] = useState('')
  const [text, setText] = useState('')

  useEffect(() => {
    const id = setTimeout(() => setDebounced(q), 200)
    return () => clearTimeout(id)
  }, [q])
  useEffect(() => {
    if (open) { setQ(''); setText(''); setServings('') }
  }, [open])

  const { data } = useQuery({
    queryKey: ['kitchen', 'recipes', 'picker', debounced],
    queryFn: () => kitchen.listRecipes({ q: debounced, limit: 30, sort: debounced ? 'name' : 'recently_cooked' }),
    enabled: open,
  })

  const serv = Number(servings.replace(',', '.')) || null

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader><DialogTitle>{title}</DialogTitle></DialogHeader>
        <div className="relative">
          <Search className="pointer-events-none absolute start-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
          <Input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('library.search')} className="ps-8" type="search" />
        </div>
        <ul className="-mx-1 max-h-72 overflow-y-auto">
          {data?.items.map((r) => (
            <li key={r.id}>
              <button type="button" onClick={() => onPick({ recipe: r }, serv)} className="flex w-full items-center gap-3 rounded-lg px-1 py-1.5 text-start hover:bg-muted">
                <RecipeImage src={r.image_url} alt="" className="size-10 shrink-0 rounded-md" />
                <span className="min-w-0 flex-1">
                  <span className="block truncate" dir="auto">{r.name}</span>
                  <Minutes value={r.total_minutes} className="text-xs" />
                </span>
              </button>
            </li>
          ))}
          {data && !data.items.length && <li className="px-1 py-2 text-sm text-muted-foreground">{t('library.noMatches')}</li>}
        </ul>
        <div className="grid grid-cols-[1fr_auto] items-end gap-2 border-t pt-3">
          {allowText ? (
            <div className="grid gap-1">
              <Label htmlFor="pick-text">{t('plan.freeText')}</Label>
              <Input id="pick-text" value={text} onChange={(e) => setText(e.target.value)} placeholder={t('plan.freeTextHint')} />
            </div>
          ) : <span />}
          <div className="grid w-24 gap-1">
            <Label htmlFor="pick-serv">{t('plan.servings')}</Label>
            <Input id="pick-serv" inputMode="decimal" value={servings} onChange={(e) => setServings(e.target.value)} />
          </div>
        </div>
        {allowText && (
          <DialogFooter>
            <Button disabled={!text.trim()} onClick={() => onPick({ title: text.trim() }, serv)}>{t('plan.addText')}</Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  )
}

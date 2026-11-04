import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { Loader2 } from 'lucide-react'
import { toast } from 'sonner'

import { kitchen, SLOTS, type Slot } from '@/lib/kitchen'
import { toISODate } from '@/lib/kitchen-format'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { errorText } from '@/components/kitchen/bits'

export function AddToPlanDialog({
  open, onOpenChange, recipeId, recipeName, defaultServings, defaultDate, defaultSlot = 'dinner',
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
  recipeId: string
  recipeName: string
  defaultServings?: number | null
  defaultDate?: string
  defaultSlot?: Slot
}) {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [date, setDate] = useState(defaultDate ?? toISODate(new Date()))
  const [slot, setSlot] = useState<Slot>(defaultSlot)
  const [servings, setServings] = useState(defaultServings ? String(defaultServings) : '')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (open) {
      setDate(defaultDate ?? toISODate(new Date()))
      setSlot(defaultSlot)
      setServings(defaultServings ? String(defaultServings) : '')
    }
  }, [open, defaultDate, defaultSlot, defaultServings])

  const save = async () => {
    setBusy(true)
    try {
      await kitchen.addPlanEntry({ date, slot, recipe_id: recipeId, servings: Number(servings) || null })
      void qc.invalidateQueries({ queryKey: ['kitchen', 'plan'] })
      toast.success(t('plan.added', { name: recipeName }))
      onOpenChange(false)
    } catch (err) {
      toast.error(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader><DialogTitle>{t('plan.addTitle')}</DialogTitle></DialogHeader>
        <p className="text-sm text-muted-foreground" dir="auto">{recipeName}</p>
        <div className="grid grid-cols-2 gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="plan-date">{t('plan.date')}</Label>
            <Input id="plan-date" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
          </div>
          <div className="grid gap-1.5">
            <Label>{t('plan.slot')}</Label>
            <Select value={slot} onValueChange={(v) => setSlot(v as Slot)}>
              <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                {SLOTS.map((s) => <SelectItem key={s} value={s}>{t(`plan.slots.${s}`)}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div className="col-span-2 grid gap-1.5">
            <Label htmlFor="plan-serv">{t('plan.servings')}</Label>
            <Input id="plan-serv" inputMode="decimal" value={servings} onChange={(e) => setServings(e.target.value)} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>{t('common.cancel')}</Button>
          <Button onClick={save} disabled={busy || !date}>{busy && <Loader2 className="size-4 animate-spin" />}{t('common.add')}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

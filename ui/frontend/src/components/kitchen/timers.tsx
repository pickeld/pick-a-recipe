import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Pause, Play, Plus, Timer, X } from 'lucide-react'
import { toast } from 'sonner'

import { formatClock } from '@/lib/kitchen-format'
import type { StepTimer } from '@/lib/kitchen'
import { addTime, beep, remaining, removeTimer, setTimerDoneHandler, startTimer, togglePause, useTimers } from '@/lib/timer-store'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { useMinutesLabel } from '@/components/kitchen/bits'

export function TimerChip({ timer, recipe, className }: { timer: StepTimer; recipe: string; className?: string }) {
  const { t } = useTranslation()
  const minutes = useMinutesLabel()
  const label = timer.max_seconds
    ? `${minutes(Math.round(timer.seconds / 60))}–${minutes(Math.round(timer.max_seconds / 60))}`
    : timer.seconds < 60 ? t('time.s', { count: timer.seconds }) : minutes(Math.round(timer.seconds / 60))
  return (
    <button
      type="button"
      onClick={() => startTimer(timer.max_seconds ?? timer.seconds, timer.label || label, recipe)}
      className={cn('inline-flex items-center gap-1 rounded-full border border-primary/30 bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary hover:bg-primary/20', className)}
      title={t('timers.start')}
    >
      <Timer className="size-3.5" />
      {label}
    </button>
  )
}

/** Running timers, pinned to the bottom of the screen on every page. */
export function TimerTray() {
  const { t } = useTranslation()
  const { timers } = useTimers()

  useEffect(() => {
    setTimerDoneHandler((timer) => {
      beep()
      toast.success(t('timers.done', { label: timer.label }), { description: timer.recipe, duration: 15000 })
      if ('Notification' in window && Notification.permission === 'granted' && document.hidden) {
        new Notification(t('timers.done', { label: timer.label }), { body: timer.recipe })
      }
    })
  }, [t])

  if (!timers.length) return null
  return (
    <div className="no-print fixed bottom-4 start-1/2 z-40 flex max-w-[calc(100vw-2rem)] -translate-x-1/2 flex-wrap justify-center gap-2 rtl:translate-x-1/2">
      {timers.map((timer) => (
        <div
          key={timer.id}
          className={cn(
            'flex items-center gap-2 rounded-full border bg-popover py-1 ps-3 pe-1 shadow-lg',
            timer.done && 'animate-pulse border-primary bg-primary text-primary-foreground',
          )}
        >
          <span className="max-w-40 truncate text-sm" dir="auto">{timer.label}</span>
          <span className="font-mono text-sm tabular-nums">{formatClock(remaining(timer))}</span>
          {!timer.done && (
            <Button size="icon-xs" variant="ghost" onClick={() => togglePause(timer.id)} aria-label={timer.pausedLeft === null ? t('timers.pause') : t('timers.resume')}>
              {timer.pausedLeft === null ? <Pause /> : <Play />}
            </Button>
          )}
          <Button size="icon-xs" variant="ghost" onClick={() => addTime(timer.id, 60)} aria-label={t('timers.addMinute')}>
            <Plus />
          </Button>
          <Button size="icon-xs" variant="ghost" onClick={() => removeTimer(timer.id)} aria-label={t('timers.dismiss')}>
            <X />
          </Button>
        </div>
      ))}
    </div>
  )
}

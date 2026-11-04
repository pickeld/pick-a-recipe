import { useState, type KeyboardEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { ChefHat, Clock, ImageOff, Star, X } from 'lucide-react'

import { splitMinutes } from '@/lib/kitchen-format'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'

export function useMinutesLabel() {
  const { t } = useTranslation()
  return (minutes: number | null | undefined) => {
    if (!minutes) return ''
    const { h, m } = splitMinutes(minutes)
    if (h && m) return t('time.hm', { h, m })
    if (h) return t('time.h', { count: h })
    return t('time.m', { count: m })
  }
}

export function Minutes({ value, className }: { value: number | null | undefined; className?: string }) {
  const label = useMinutesLabel()(value)
  if (!label) return null
  return (
    <span className={cn('inline-flex items-center gap-1 text-muted-foreground', className)}>
      <Clock className="size-3.5" />
      {label}
    </span>
  )
}

export function Rating({
  value, onChange, size = 'md',
}: { value: number | null; onChange?: (v: number | null) => void; size?: 'sm' | 'md' }) {
  const { t } = useTranslation()
  const [hover, setHover] = useState<number | null>(null)
  const shown = hover ?? value ?? 0
  const cls = size === 'sm' ? 'size-3.5' : 'size-5'
  return (
    <div className="inline-flex items-center gap-0.5" onMouseLeave={() => setHover(null)} role={onChange ? 'radiogroup' : undefined} aria-label={t('recipe.rating')}>
      {[1, 2, 3, 4, 5].map((n) => {
        const star = <Star className={cn(cls, n <= shown ? 'fill-amber-400 text-amber-400' : 'text-muted-foreground/40')} />
        if (!onChange) return <span key={n}>{star}</span>
        return (
          <button
            key={n}
            type="button"
            role="radio"
            aria-checked={value === n}
            aria-label={t('recipe.stars', { count: n })}
            className="rounded p-0.5 hover:scale-110"
            onMouseEnter={() => setHover(n)}
            onClick={() => onChange(value === n ? null : n)}
          >
            {star}
          </button>
        )
      })}
    </div>
  )
}

const PLACEHOLDER_TONES = [
  'from-amber-100 to-orange-200 text-orange-700/60 dark:from-amber-950 dark:to-orange-900 dark:text-orange-300/50',
  'from-emerald-100 to-teal-200 text-teal-700/60 dark:from-emerald-950 dark:to-teal-900 dark:text-teal-300/50',
  'from-rose-100 to-pink-200 text-rose-700/60 dark:from-rose-950 dark:to-pink-900 dark:text-rose-300/50',
  'from-sky-100 to-indigo-200 text-indigo-700/60 dark:from-sky-950 dark:to-indigo-900 dark:text-indigo-300/50',
  'from-yellow-100 to-amber-200 text-amber-700/60 dark:from-yellow-950 dark:to-amber-900 dark:text-amber-300/50',
  'from-violet-100 to-purple-200 text-purple-700/60 dark:from-violet-950 dark:to-purple-900 dark:text-purple-300/50',
]

function toneFor(seed: string): string {
  let h = 0x811c9dc5
  for (const ch of seed) h = Math.imul(h ^ ch.charCodeAt(0), 0x01000193)
  h = Math.imul(h ^ (h >>> 16), 0x45d9f3b)
  h ^= h >>> 16
  return PLACEHOLDER_TONES[(h >>> 0) % PLACEHOLDER_TONES.length]
}

export function RecipeImage({
  src, alt, className, seed,
}: { src: string | null | undefined; alt: string; className?: string; seed?: string }) {
  const [brokenSrc, setBrokenSrc] = useState<string | null>(null)
  if (!src || brokenSrc === src) {
    const key = seed ?? alt
    return (
      <div className={cn('flex items-center justify-center', key ? cn('bg-gradient-to-br', toneFor(key)) : 'bg-muted text-muted-foreground', className)}>
        {key ? <ChefHat className="size-1/4 max-h-16 max-w-16 min-h-6 min-w-6" strokeWidth={1.5} /> : <ImageOff className="size-6 opacity-50" />}
      </div>
    )
  }
  return <img src={src} alt={alt} loading="lazy" onError={() => setBrokenSrc(src)} className={cn('object-cover', className)} />
}

export function Checkbox({
  checked, onChange, label, className,
}: { checked: boolean; onChange: (v: boolean) => void; label?: string; className?: string }) {
  return (
    <input
      type="checkbox"
      checked={checked}
      aria-label={label}
      onChange={(e) => onChange(e.target.checked)}
      className={cn('size-4 shrink-0 cursor-pointer accent-primary', className)}
    />
  )
}

export function EmptyState({ icon, title, children }: { icon: ReactNode; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed p-10 text-center">
      <div className="text-muted-foreground [&_svg]:size-8">{icon}</div>
      <p className="font-medium">{title}</p>
      {children && <div className="text-sm text-muted-foreground">{children}</div>}
    </div>
  )
}

/** Type a word and press Enter (or comma) to add it as a chip. */
export function ChipsInput({
  value, onChange, placeholder, className,
}: { value: string[]; onChange: (v: string[]) => void; placeholder?: string; className?: string }) {
  const [draft, setDraft] = useState('')
  const { t } = useTranslation()
  const commit = (text: string) => {
    const parts = text.split(/[,\n]/).map((s) => s.trim()).filter(Boolean)
    if (!parts.length) return
    const next = [...value]
    for (const p of parts) if (!next.some((v) => v.toLowerCase() === p.toLowerCase())) next.push(p)
    onChange(next)
    setDraft('')
  }
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault()
      commit(draft)
    } else if (e.key === 'Backspace' && !draft && value.length) {
      onChange(value.slice(0, -1))
    }
  }
  return (
    <div className={cn('flex min-h-9 flex-wrap items-center gap-1.5 rounded-lg border border-input px-2 py-1 focus-within:ring-3 focus-within:ring-ring/50', className)}>
      {value.map((v) => (
        <Badge key={v} variant="secondary" className="gap-1">
          {v}
          <button type="button" aria-label={t('common.remove', { name: v })} onClick={() => onChange(value.filter((x) => x !== v))}>
            <X className="size-3" />
          </button>
        </Badge>
      ))}
      <Input
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={onKey}
        onBlur={() => commit(draft)}
        placeholder={value.length ? '' : placeholder}
        className="h-7 min-w-24 flex-1 border-0 p-0 shadow-none focus-visible:ring-0 dark:bg-transparent"
      />
    </div>
  )
}

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0">
        <h2 className="text-xl font-semibold tracking-tight">{title}</h2>
        {description && <p className="text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

export function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

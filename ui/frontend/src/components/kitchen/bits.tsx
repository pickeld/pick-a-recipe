import { useState, type KeyboardEvent, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Clock, ImageOff, Star, X } from 'lucide-react'

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

export function RecipeImage({ src, alt, className }: { src: string | null | undefined; alt: string; className?: string }) {
  if (!src) {
    return (
      <div className={cn('flex items-center justify-center bg-muted text-muted-foreground', className)}>
        <ImageOff className="size-6 opacity-50" />
      </div>
    )
  }
  return <img src={src} alt={alt} loading="lazy" className={cn('object-cover', className)} />
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

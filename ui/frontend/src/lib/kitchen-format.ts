/** Date, duration and amount helpers for the kitchen pages. No DOM, no React. */

const RTL_LANGUAGES = new Set(['he', 'iw', 'yi', 'ar', 'fa', 'ur'])
const RTL_LETTERS = /[\u0590-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFF]/g
const LTR_LETTERS = /[A-Za-z\u00C0-\u024F\u0400-\u04FF]/g

/**
 * Reading direction of recipe content, independent of the interface language.
 * The text itself decides; the stored language tag is only a fallback, since an
 * imported page can declare a language it is not written in. Undefined means no
 * signal either way, so the caller should inherit the interface direction.
 */
export function textDirection(language: string | null | undefined, ...texts: (string | null | undefined)[]): 'rtl' | 'ltr' | undefined {
  const sample = texts.filter(Boolean).join(' ')
  const rtl = sample.match(RTL_LETTERS)?.length ?? 0
  const ltr = sample.match(LTR_LETTERS)?.length ?? 0
  if (rtl || ltr) return rtl >= ltr ? 'rtl' : 'ltr'
  const base = (language ?? '').toLowerCase().split(/[-_]/)[0]
  if (!base) return undefined
  return RTL_LANGUAGES.has(base) ? 'rtl' : 'ltr'
}

const HEBREW_UNITS: Record<string, [one: string, many: string]> = {
  tsp: ['כפית', 'כפיות'],
  tbsp: ['כף', 'כפות'],
  cup: ['כוס', 'כוסות'],
  g: ['גרם', 'גרם'],
  kg: ['ק״ג', 'ק״ג'],
  ml: ['מ״ל', 'מ״ל'],
  l: ['ליטר', 'ליטר'],
  pinch: ['קורט', 'קורט'],
  piece: ['', ''],
}

/** A canonical unit as the recipe's own language would write it. */
export function unitLabel(unit: string, amount: number | null, dir?: 'rtl' | 'ltr'): string {
  if (unit === 'piece') return ''
  const hebrew = dir === 'rtl' ? HEBREW_UNITS[unit] : undefined
  if (!hebrew) return unit
  return amount !== null && amount <= 1 ? hebrew[0] : hebrew[1]
}

/**
 * Amount and the rest of an ingredient line. When the amount was neither scaled
 * nor converted the line is shown as it was written, because the stored unit is
 * canonical ("tbsp" for "כפות", "piece" for "cloves") and would read wrong.
 */
export function ingredientText(
  i: { food: string; notes: string; raw: string; quantity: string; unit: string; amount: number | null; display_quantity: string; display_unit: string },
  dir?: 'rtl' | 'ltr',
): { amount: string; rest: string } {
  if (!i.food) return { amount: '', rest: i.raw }
  const unchanged = i.display_quantity === i.quantity && i.display_unit === i.unit
  const at = unchanged && i.quantity ? i.raw.indexOf(i.food) : -1
  if (at > 0) return { amount: i.raw.slice(0, at).trim(), rest: i.raw.slice(at).trim() }
  const amount = [i.display_quantity, unitLabel(i.display_unit, i.amount, dir)].filter(Boolean).join(' ')
  const rest = [i.food, i.notes ? `(${i.notes})` : ''].filter(Boolean).join(' ')
  return { amount, rest }
}

export function recipeDirection(recipe: {
  language?: string | null
  name?: string | null
  ingredients?: { raw?: string | null }[]
  steps?: { text?: string | null }[]
}): 'rtl' | 'ltr' | undefined {
  return textDirection(
    recipe.language,
    recipe.name,
    ...(recipe.ingredients ?? []).slice(0, 5).map((i) => i.raw),
    ...(recipe.steps ?? []).slice(0, 3).map((s) => s.text),
  )
}

/** YYYY-MM-DD in local time (toISOString would shift across midnight). */
export function toISODate(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

export function parseISODate(iso: string): Date {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(y, (m || 1) - 1, d || 1)
}

export function addDays(iso: string, days: number): string {
  const d = parseISODate(iso)
  d.setDate(d.getDate() + days)
  return toISODate(d)
}

/** First day of the week containing ``iso``. Weeks start on Sunday by default. */
export function weekStart(iso: string, firstDay = 0): string {
  const d = parseISODate(iso)
  const diff = (d.getDay() - firstDay + 7) % 7
  return addDays(iso, -diff)
}

export function weekDays(start: string): string[] {
  return Array.from({ length: 7 }, (_, i) => addDays(start, i))
}

/** Six full weeks covering the month of ``iso``, for a calendar grid. */
export function monthGrid(iso: string, firstDay = 0): string[] {
  const d = parseISODate(iso)
  const first = toISODate(new Date(d.getFullYear(), d.getMonth(), 1))
  const start = weekStart(first, firstDay)
  return Array.from({ length: 42 }, (_, i) => addDays(start, i))
}

export function sameMonth(a: string, b: string): boolean {
  return a.slice(0, 7) === b.slice(0, 7)
}

/** Minutes in an ISO 8601 duration such as PT1H15M, or null. */
export function isoToMinutes(value: string | null | undefined): number | null {
  if (!value) return null
  const m = /^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$/i.exec(value.trim())
  if (!m) return null
  const total = Number(m[1] ?? 0) * 1440 + Number(m[2] ?? 0) * 60 + Number(m[3] ?? 0) + Math.round(Number(m[4] ?? 0) / 60)
  return total > 0 ? total : null
}

export function minutesToIso(minutes: number | null | undefined): string {
  if (!minutes || minutes <= 0) return ''
  const h = Math.floor(minutes / 60)
  const m = Math.round(minutes % 60)
  return `PT${h ? `${h}H` : ''}${m ? `${m}M` : ''}`
}

/** Hours and minutes, for "1 h 15 min" style labels. */
export function splitMinutes(minutes: number): { h: number; m: number } {
  return { h: Math.floor(minutes / 60), m: Math.round(minutes % 60) }
}

/** m:ss or h:mm:ss for a countdown. */
export function formatClock(totalSeconds: number): string {
  const s = Math.max(0, Math.round(totalSeconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  const sec = String(s % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`
}

/** The next servings value when stepping up or down. Finer steps for small amounts. */
export function stepServings(current: number, direction: 1 | -1): number {
  const step = current < 1 || (direction === -1 && current <= 1) ? 0.25 : current < 2 || (direction === -1 && current <= 2) ? 0.5 : 1
  const next = Math.round((current + direction * step) * 100) / 100
  return Math.min(500, Math.max(0.25, next))
}

const FRACTIONS: [number, string][] = [
  [1 / 8, '⅛'], [1 / 4, '¼'], [1 / 3, '⅓'], [1 / 2, '½'], [2 / 3, '⅔'], [3 / 4, '¾'],
]

/** A friendly amount: fractions for small kitchen quantities, trimmed decimals otherwise. */
export function formatAmount(value: number | null | undefined, unit = ''): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return ''
  const metric = /^(g|kg|ml|l|גרם|ק"ג|מ"ל|ליטר)$/i.test(unit.trim())
  if (!metric && value < 20) {
    const whole = Math.floor(value)
    const frac = value - whole
    if (frac < 0.06) return String(whole || (value > 0 ? FRACTIONS[0][1] : 0))
    if (frac > 0.94) return String(whole + 1)
    const [, glyph] = FRACTIONS.reduce((best, cur) => (Math.abs(cur[0] - frac) < Math.abs(best[0] - frac) ? cur : best))
    return whole ? `${whole}${glyph}` : glyph
  }
  const rounded = value >= 100 ? Math.round(value) : Math.round(value * 100) / 100
  return String(rounded)
}

/** Parse a user-typed list of ingredients: commas, new lines or Hebrew "ו" separators. */
export function splitIngredients(text: string): string[] {
  return text
    .split(/[,\n،;]+/)
    .map((s) => s.trim())
    .filter(Boolean)
    .slice(0, 60)
}

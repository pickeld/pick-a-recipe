export type Language = 'he' | 'en'

/** A saved choice wins; otherwise Hebrew only when the browser prefers it, English for everyone else. */
export function pickLanguage(stored: string | null, browserLanguages: readonly string[]): Language {
  if (stored === 'he' || stored === 'en') return stored
  const preferred = (browserLanguages[0] ?? '').toLowerCase()
  return preferred.startsWith('he') || preferred.startsWith('iw') ? 'he' : 'en'
}

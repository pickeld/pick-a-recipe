/** Merge a settings draft with the values currently in the inputs.

Password managers fill secret fields without telling React, so the draft can
miss a key the user can see. The live value is what gets saved. A live value
equal to what is already stored is left out, so an untouched field cannot
wipe a saved key.
*/
export function mergeLiveValues(
  draft: Record<string, string>,
  live: Record<string, string | undefined>,
  saved: Record<string, string>,
): Record<string, string> {
  const patch: Record<string, string> = { ...draft }
  for (const [key, value] of Object.entries(live)) {
    if (value === undefined) continue
    if (value !== (saved[key] ?? '')) patch[key] = value
  }
  return patch
}

export interface FailureNotice {
  key: string
  message: string
}

/** Notices still worth showing: drop blanks, dismissals, and repeated text. */
export function visibleFailureNotices(
  live: readonly FailureNotice[],
  history: readonly FailureNotice[],
  dismissed: readonly string[],
): FailureNotice[] {
  const dismissedSet = new Set(dismissed)
  const seen = new Set<string>()
  const out: FailureNotice[] = []
  for (const notice of [...live, ...history]) {
    const message = notice.message.trim()
    if (!message || dismissedSet.has(notice.key) || seen.has(message)) continue
    seen.add(message)
    out.push({ key: notice.key, message })
  }
  return out
}

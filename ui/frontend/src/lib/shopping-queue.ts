/**
 * Shopping list edits made while offline.
 *
 * Edits are queued with the time they were made and replayed through
 * /api/kitchen/shopping/sync once the connection is back. The server keeps
 * the newest edit per item, so a phone that was offline cannot undo a
 * check-off someone else made in the meantime. Items added offline get a
 * negative temporary id until the server assigns a real one.
 */
import type { ShoppingItem, ShoppingPayload } from './kitchen.ts'

export type QueuedOp =
  | { op: 'update'; id: number; patch: Record<string, unknown>; client_ts: string }
  | { op: 'delete'; id: number; client_ts: string }
  | { op: 'add'; temp_id: number; list_id: number; food: string; client_ts: string }

/** Add an edit, folding it into earlier edits of the same item. */
export function enqueue(queue: QueuedOp[], next: QueuedOp): QueuedOp[] {
  if (next.op === 'add') return [...queue, next]
  const id = next.id
  const pendingAdd = queue.find((q) => q.op === 'add' && q.temp_id === id)
  if (pendingAdd) {
    if (next.op === 'delete') return queue.filter((q) => q !== pendingAdd && !(q.op !== 'add' && q.id === id))
    if (typeof next.patch.food === 'string') {
      return queue.map((q) => (q === pendingAdd ? { ...pendingAdd, food: next.patch.food as string } : q))
    }
  }
  if (next.op === 'delete') {
    return [...queue.filter((q) => q.op === 'add' || q.id !== id), next]
  }
  const prior = queue.find((q): q is Extract<QueuedOp, { op: 'update' }> => q.op === 'update' && q.id === id)
  if (prior) {
    return queue.map((q) => (q === prior ? { ...prior, patch: { ...prior.patch, ...next.patch }, client_ts: next.client_ts } : q))
  }
  return [...queue, next]
}

/** Point queued edits at the real id once a temporary item has been created. */
export function remapTempId(queue: QueuedOp[], tempId: number, realId: number): QueuedOp[] {
  return queue.map((q) => (q.op !== 'add' && q.id === tempId ? { ...q, id: realId } : q))
}

export function allItems(payload: ShoppingPayload): ShoppingItem[] {
  return [...payload.groups.flatMap((g) => g.items), ...payload.checked, ...payload.excluded]
}

export function regroup(payload: ShoppingPayload, items: ShoppingItem[]): ShoppingPayload {
  const order = new Map(payload.aisles.map((a, i) => [a, i]))
  const groups = new Map<string, ShoppingItem[]>()
  for (const item of items) {
    if (item.excluded || item.checked) continue
    const list = groups.get(item.aisle) ?? []
    list.push(item)
    groups.set(item.aisle, list)
  }
  return {
    ...payload,
    groups: [...groups.entries()]
      .sort(([a], [b]) => (order.get(a) ?? 99) - (order.get(b) ?? 99))
      .map(([aisle, list]) => ({ aisle, items: list })),
    checked: items.filter((i) => i.checked && !i.excluded),
    excluded: items.filter((i) => i.excluded),
  }
}

/** What the list looks like with the queued edits applied. */
export function applyQueue(payload: ShoppingPayload, queue: QueuedOp[]): ShoppingPayload {
  if (!queue.length) return payload
  let items = allItems(payload)
  for (const q of queue) {
    if (q.op === 'add') {
      if (q.list_id !== payload.list_id) continue
      items = [...items, {
        id: q.temp_id, list_id: q.list_id, food: q.food, quantity: null, unit: '', aisle: 'other',
        checked: false, excluded: false, position: 9999, note: '', sources: [], manual: true, updated_at: q.client_ts,
      }]
    } else if (q.op === 'delete') {
      items = items.filter((i) => i.id !== q.id)
    } else {
      items = items.map((i) => (i.id === q.id ? { ...i, ...(q.patch as Partial<ShoppingItem>) } : i))
    }
  }
  return regroup(payload, items)
}

export function nowIso(): string {
  return new Date().toISOString()
}

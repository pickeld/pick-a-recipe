import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  addDays, formatAmount, formatClock, ingredientText, isoToMinutes, minutesToIso, monthGrid, recipeDirection, splitIngredients,
  stepServings, textDirection, weekStart,
} from './kitchen-format.ts'
import { applyQueue, enqueue, remapTempId, type QueuedOp } from './shopping-queue.ts'
import type { ShoppingItem, ShoppingPayload } from './kitchen.ts'

describe('text direction', () => {
  it('follows the script the recipe is written in', () => {
    assert.equal(textDirection('', 'שקשוקה עם פלפלים'), 'rtl')
    assert.equal(textDirection('', 'Shakshuka'), 'ltr')
    assert.equal(textDirection('', 'סלט with feta וזיתים'), 'rtl')
  })

  it('trusts the text over a wrong language tag and falls back to the tag', () => {
    assert.equal(textDirection('en', 'עוגת שוקולד'), 'rtl')
    assert.equal(textDirection('he-IL', '200', '½'), 'rtl')
    assert.equal(textDirection('en'), 'ltr')
    assert.equal(textDirection('', '1/2'), undefined)
  })

  it('reads a recipe from its name, ingredients and steps', () => {
    const recipe = { language: '', name: 'Ptitim', ingredients: [{ raw: '2 כוסות פתיתים' }, { raw: 'מלח' }], steps: [{ text: 'לבשל 8 דקות' }] }
    assert.equal(recipeDirection(recipe), 'rtl')
  })
})

describe('ingredient text', () => {
  const base = { notes: '', amount: 2, scalable: true }

  it('keeps the words of an unchanged line instead of the canonical unit', () => {
    const line = { ...base, food: 'שמן זית', raw: '2 כפות שמן זית', quantity: '2', unit: 'tbsp', display_quantity: '2', display_unit: 'tbsp' }
    assert.deepEqual(ingredientText(line, 'rtl'), { amount: '2 כפות', rest: 'שמן זית' })
    const garlic = { ...base, amount: 3, food: 'garlic', raw: '3 cloves garlic', quantity: '3', unit: 'piece', display_quantity: '3', display_unit: 'piece' }
    assert.deepEqual(ingredientText(garlic), { amount: '3 cloves', rest: 'garlic' })
  })

  it('names a scaled unit in the language of the recipe', () => {
    const scaled = { ...base, amount: 4, food: 'שמן זית', raw: '2 כפות שמן זית', quantity: '2', unit: 'tbsp', display_quantity: '4', display_unit: 'tbsp' }
    assert.deepEqual(ingredientText(scaled, 'rtl'), { amount: '4 כפות', rest: 'שמן זית' })
    assert.deepEqual(ingredientText({ ...scaled, amount: 1, display_quantity: '1' }, 'rtl'), { amount: '1 כף', rest: 'שמן זית' })
    assert.deepEqual(ingredientText({ ...scaled, food: 'olive oil', raw: '2 tbsp olive oil' }), { amount: '4 tbsp', rest: 'olive oil' })
  })
})

describe('dates', () => {
  it('starts weeks on Sunday and crosses month ends', () => {
    assert.equal(weekStart('2026-11-04'), '2026-11-01')
    assert.equal(weekStart('2026-11-01'), '2026-11-01')
    assert.equal(weekStart('2026-11-04', 1), '2026-11-02')
    assert.equal(addDays('2026-12-31', 1), '2027-01-01')
  })

  it('builds a six-week month grid that starts on the first week day', () => {
    const grid = monthGrid('2026-11-15')
    assert.equal(grid.length, 42)
    assert.equal(grid[0], '2026-11-01')
    assert.ok(grid.includes('2026-11-30'))
  })
})

describe('durations and amounts', () => {
  it('round-trips ISO durations', () => {
    assert.equal(isoToMinutes('PT1H15M'), 75)
    assert.equal(isoToMinutes('PT45M'), 45)
    assert.equal(isoToMinutes('nonsense'), null)
    assert.equal(minutesToIso(75), 'PT1H15M')
    assert.equal(minutesToIso(0), '')
  })

  it('formats countdowns', () => {
    assert.equal(formatClock(65), '1:05')
    assert.equal(formatClock(3725), '1:02:05')
  })

  it('steps servings finely below two', () => {
    assert.equal(stepServings(4, -1), 3)
    assert.equal(stepServings(2, -1), 1.5)
    assert.equal(stepServings(1, -1), 0.75)
    assert.equal(stepServings(0.25, -1), 0.25)
    assert.equal(stepServings(1.5, 1), 2)
  })

  it('uses fractions for spoons and decimals for grams', () => {
    assert.equal(formatAmount(0.5, 'tbsp'), '½')
    assert.equal(formatAmount(1.33, 'cup'), '1⅓')
    assert.equal(formatAmount(250, 'g'), '250')
    assert.equal(formatAmount(0.75, 'kg'), '0.75')
    assert.equal(formatAmount(null), '')
  })

  it('splits typed ingredient lists', () => {
    assert.deepEqual(splitIngredients('eggs, tomatoes\n onion ;'), ['eggs', 'tomatoes', 'onion'])
  })
})

function item(id: number, patch: Partial<ShoppingItem> = {}): ShoppingItem {
  return {
    id, list_id: 1, food: `food ${id}`, quantity: null, unit: '', aisle: 'produce', checked: false,
    excluded: false, position: id, note: '', sources: [], manual: false, updated_at: '', ...patch,
  }
}

function payload(items: ShoppingItem[]): ShoppingPayload {
  return {
    list_id: 1,
    aisles: ['produce', 'dairy', 'other'],
    groups: [{ aisle: 'produce', items: items.filter((i) => !i.checked) }],
    checked: items.filter((i) => i.checked),
    excluded: [],
  }
}

describe('offline shopping queue', () => {
  const ts = '2026-11-02T10:00:00.000Z'

  it('merges repeated edits of one item into one update', () => {
    let q: QueuedOp[] = []
    q = enqueue(q, { op: 'update', id: 3, patch: { checked: true }, client_ts: ts })
    q = enqueue(q, { op: 'update', id: 3, patch: { checked: false, note: 'x' }, client_ts: '2026-11-02T10:01:00.000Z' })
    assert.equal(q.length, 1)
    assert.deepEqual(q[0], { op: 'update', id: 3, patch: { checked: false, note: 'x' }, client_ts: '2026-11-02T10:01:00.000Z' })
  })

  it('drops an item added and deleted while offline', () => {
    let q: QueuedOp[] = []
    q = enqueue(q, { op: 'add', temp_id: -1, list_id: 1, food: 'milk', client_ts: ts })
    q = enqueue(q, { op: 'update', id: -1, patch: { food: 'oat milk' }, client_ts: ts })
    assert.equal(q.length, 1)
    assert.equal((q[0] as { food: string }).food, 'oat milk')
    q = enqueue(q, { op: 'delete', id: -1, client_ts: ts })
    assert.deepEqual(q, [])
  })

  it('replaces pending updates with a delete', () => {
    let q: QueuedOp[] = []
    q = enqueue(q, { op: 'update', id: 2, patch: { checked: true }, client_ts: ts })
    q = enqueue(q, { op: 'delete', id: 2, client_ts: ts })
    assert.deepEqual(q.map((o) => o.op), ['delete'])
  })

  it('shows queued edits optimistically', () => {
    const base = payload([item(1), item(2)])
    const view = applyQueue(base, [
      { op: 'update', id: 1, patch: { checked: true }, client_ts: ts },
      { op: 'add', temp_id: -5, list_id: 1, food: 'bread', client_ts: ts },
      { op: 'delete', id: 2, client_ts: ts },
    ])
    assert.deepEqual(view.checked.map((i) => i.id), [1])
    assert.deepEqual(view.groups.flatMap((g) => g.items.map((i) => i.food)), ['bread'])
    assert.equal(view.groups[0].aisle, 'other')
  })

  it('points edits at the real id once an add is synced', () => {
    const q = remapTempId([{ op: 'update', id: -1, patch: { checked: true }, client_ts: ts }], -1, 42)
    assert.equal((q[0] as { id: number }).id, 42)
  })
})

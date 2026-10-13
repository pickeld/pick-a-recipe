import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { describe, it } from 'node:test'

import { pickUnseenPreview } from './preview-queue.ts'

describe('pickUnseenPreview', () => {
  it('opens a recipe that arrived while the user was not on the home page', () => {
    const tasksPageQueue = [
      { upload_id: 'already-dismissed', recipe: { name: 'Old' } },
      { upload_id: 'fresh', recipe: { name: 'Shakshuka' } },
    ]
    const next = pickUnseenPreview(tasksPageQueue, new Set(['already-dismissed']))
    assert.equal(next?.upload_id, 'fresh')
  })

  it('does not reopen an upload the dialog already showed', () => {
    const next = pickUnseenPreview(
      [{ upload_id: 'shown' }],
      new Set(['shown']),
    )
    assert.equal(next, undefined)
  })

  it('skips a row that has no upload id', () => {
    const next = pickUnseenPreview(
      [{ upload_id: '' }, { upload_id: null }, { upload_id: 'real' }],
      new Set(),
    )
    assert.equal(next?.upload_id, 'real')
  })
})

describe('preview dialog is not tied to the home page', () => {
  it('mounts the dialog in the app shell and leaves the home page without it', () => {
    const shell = readFileSync(
      new URL('../components/app-shell.tsx', import.meta.url),
      'utf8',
    )
    const home = readFileSync(
      new URL('../pages/home-page.tsx', import.meta.url),
      'utf8',
    )
    assert.match(shell, /<RecipePreviewHost \/>/)
    assert.doesNotMatch(home, /recipe_preview|RecipePreviewHost/)
  })
})

describe('a Gemini failure stays on screen', () => {
  it('keeps job-failed toasts up until the user dismisses them', () => {
    for (const path of [
      '../components/app-shell.tsx',
      '../pages/home-page.tsx',
      '../pages/job-page.tsx',
    ]) {
      const source = readFileSync(new URL(path, import.meta.url), 'utf8')
      assert.match(
        source,
        /toast\.error\([\s\S]*?duration:\s*Infinity[\s\S]*?job-failed-/,
        path,
      )
    }
  })
})

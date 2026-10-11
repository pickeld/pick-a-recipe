import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import { mergeLiveValues, visibleFailureNotices } from './settings-form.ts'

describe('mergeLiveValues', () => {
  it('keeps a key the draft missed when the input holds a new value', () => {
    const patch = mergeLiveValues(
      { llm_provider: 'gemini' },
      { gemini_api_key: 'gemini-test-key', gemini_model: 'gemini-2.5-flash' },
      { gemini_api_key: '', gemini_model: 'gemini-2.5-flash' },
    )
    assert.equal(patch.llm_provider, 'gemini')
    assert.equal(patch.gemini_api_key, 'gemini-test-key')
    assert.equal(patch.gemini_model, undefined)
  })

  it('does not send an untouched secret back, so a save cannot wipe it', () => {
    const patch = mergeLiveValues(
      { recipe_lang: 'hebrew' },
      { gemini_api_key: 'already-stored' },
      { gemini_api_key: 'already-stored' },
    )
    assert.deepEqual(patch, { recipe_lang: 'hebrew' })
  })

  it('sends an emptied field so the user can clear a key', () => {
    const patch = mergeLiveValues(
      {},
      { gemini_api_key: '' },
      { gemini_api_key: 'already-stored' },
    )
    assert.deepEqual(patch, { gemini_api_key: '' })
  })
})

describe('visibleFailureNotices', () => {
  it('keeps the live error and drops a repeated history row and a dismissal', () => {
    const notices = visibleFailureNotices(
      [{ key: 'job:1', message: 'Gemini rejected the key' }],
      [
        { key: 'history:9', message: 'Gemini rejected the key' },
        { key: 'history:8', message: '  ' },
        { key: 'history:7', message: 'model not found' },
      ],
      ['history:7'],
    )
    assert.deepEqual(notices, [
      { key: 'job:1', message: 'Gemini rejected the key' },
    ])
  })
})

import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import { pickLanguage } from './language.ts'

describe('pickLanguage', () => {
  it('keeps a saved choice', () => {
    assert.equal(pickLanguage('he', ['en-US']), 'he')
    assert.equal(pickLanguage('en', ['he-IL']), 'en')
  })

  it('defaults to English for a non-Hebrew browser', () => {
    assert.equal(pickLanguage(null, ['nl-NL', 'en']), 'en')
    assert.equal(pickLanguage(null, ['sw']), 'en')
  })

  it('defaults to Hebrew when the browser prefers it', () => {
    assert.equal(pickLanguage(null, ['he-IL', 'en']), 'he')
    assert.equal(pickLanguage(null, ['iw']), 'he')
  })

  it('falls back to English with no browser languages or a bad saved value', () => {
    assert.equal(pickLanguage(null, []), 'en')
    assert.equal(pickLanguage('fr', []), 'en')
  })
})

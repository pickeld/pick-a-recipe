/**
 * Kitchen timers that keep running while the user moves between pages.
 * Kept outside React so the recipe page and cook mode share them.
 */
import { useSyncExternalStore } from 'react'

export interface KitchenTimer {
  id: string
  label: string
  recipe: string
  endsAt: number
  duration: number
  pausedLeft: number | null
  done: boolean
}

let timers: KitchenTimer[] = []
const listeners = new Set<() => void>()
let ticker: ReturnType<typeof setInterval> | null = null
let onDone: ((t: KitchenTimer) => void) | null = null

function emit() {
  for (const l of listeners) l()
}

function tick() {
  const now = Date.now()
  let changed = false
  timers = timers.map((t) => {
    if (!t.done && t.pausedLeft === null && t.endsAt <= now) {
      changed = true
      const finished = { ...t, done: true }
      onDone?.(finished)
      return finished
    }
    return t
  })
  if (changed) emit()
  else if (timers.some((t) => !t.done && t.pausedLeft === null)) emit()
  if (!timers.some((t) => !t.done && t.pausedLeft === null) && ticker) {
    clearInterval(ticker)
    ticker = null
  }
}

function ensureTicker() {
  if (!ticker) ticker = setInterval(tick, 500)
}

export function setTimerDoneHandler(handler: (t: KitchenTimer) => void) {
  onDone = handler
}

export function startTimer(seconds: number, label: string, recipe = ''): string {
  const id = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
  timers = [...timers, { id, label, recipe, endsAt: Date.now() + seconds * 1000, duration: seconds, pausedLeft: null, done: false }]
  ensureTicker()
  emit()
  return id
}

export function togglePause(id: string) {
  timers = timers.map((t) => {
    if (t.id !== id || t.done) return t
    if (t.pausedLeft === null) return { ...t, pausedLeft: Math.max(0, t.endsAt - Date.now()) }
    return { ...t, endsAt: Date.now() + t.pausedLeft, pausedLeft: null }
  })
  ensureTicker()
  emit()
}

export function addTime(id: string, seconds: number) {
  timers = timers.map((t) => {
    if (t.id !== id) return t
    if (t.done) return { ...t, done: false, endsAt: Date.now() + seconds * 1000, pausedLeft: null }
    if (t.pausedLeft !== null) return { ...t, pausedLeft: t.pausedLeft + seconds * 1000 }
    return { ...t, endsAt: t.endsAt + seconds * 1000 }
  })
  ensureTicker()
  emit()
}

export function removeTimer(id: string) {
  timers = timers.filter((t) => t.id !== id)
  emit()
}

export function remaining(t: KitchenTimer): number {
  if (t.done) return 0
  return Math.max(0, (t.pausedLeft ?? t.endsAt - Date.now()) / 1000)
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

// Bumped on every tick so countdowns re-render even when the list is unchanged.
let version = 0
listeners.add(() => {
  version += 1
})

export function useTimers(): { timers: KitchenTimer[]; version: number } {
  const list = useSyncExternalStore(subscribe, () => timers)
  const v = useSyncExternalStore(subscribe, () => version)
  return { timers: list, version: v }
}

export function beep() {
  try {
    const AudioCtx = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
    const ctx = new AudioCtx()
    for (let i = 0; i < 3; i++) {
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.frequency.value = 880
      gain.gain.setValueAtTime(0.2, ctx.currentTime + i * 0.4)
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + i * 0.4 + 0.3)
      osc.connect(gain).connect(ctx.destination)
      osc.start(ctx.currentTime + i * 0.4)
      osc.stop(ctx.currentTime + i * 0.4 + 0.3)
    }
    setTimeout(() => void ctx.close(), 2000)
  } catch {
    // No audio: the toast and vibration still fire.
  }
  navigator.vibrate?.([300, 150, 300])
}

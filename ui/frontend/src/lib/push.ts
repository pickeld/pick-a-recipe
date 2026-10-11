import { api } from '@/lib/api'

/**
 * Browser-side Web Push subscription management.
 *
 * The service worker (public/push-sw.js) renders whatever arrives; this only
 * deals with getting a subscription registered and torn down.
 */

/** Whether this browser can do Web Push at all. */
export function isPushSupported(): boolean {
  return (
    typeof navigator !== 'undefined' &&
    'serviceWorker' in navigator &&
    typeof window !== 'undefined' &&
    'PushManager' in window &&
    'Notification' in window
  )
}

/** Current browser-level permission, or 'unsupported'. */
export function permissionState(): NotificationPermission | 'unsupported' {
  if (!isPushSupported()) return 'unsupported'
  return Notification.permission
}

/**
 * PushManager wants the application server key as raw bytes, but the API
 * serves it base64url-encoded and unpadded, the way the Web Push spec writes it.
 */
function decodeServerKey(base64url: string): Uint8Array {
  const padding = '='.repeat((4 - (base64url.length % 4)) % 4)
  const base64 = (base64url + padding).replace(/-/g, '+').replace(/_/g, '/')
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i)
  }
  return bytes
}

async function registration(): Promise<ServiceWorkerRegistration> {
  // Deliberately not `navigator.serviceWorker.ready`, which never settles when
  // no worker has been registered — that would hang the settings toggle
  // forever instead of explaining itself.
  const existing = await navigator.serviceWorker.getRegistration()
  if (!existing) {
    throw new Error(
      'The app has not finished installing its service worker. Reload the page and try again.',
    )
  }
  return existing
}

/** The active subscription for this browser, if it has one. */
export async function currentSubscription(): Promise<PushSubscription | null> {
  if (!isPushSupported()) return null
  const reg = await navigator.serviceWorker.getRegistration()
  if (!reg) return null
  return reg.pushManager.getSubscription()
}

/** Ask permission, subscribe, and register the endpoint with the server. */
export async function enablePush(): Promise<void> {
  const permission = await Notification.requestPermission()
  if (permission !== 'granted') {
    throw new Error(
      permission === 'denied'
        ? 'Notifications are blocked for this site. Re-allow them in your browser settings first.'
        : 'Notification permission was dismissed.',
    )
  }

  const { key } = await api.pushVapidKey()
  if (!key) {
    throw new Error(
      'This server cannot send notifications — it has no signing key. Check the server logs.',
    )
  }

  const reg = await registration()

  const existing = await reg.pushManager.getSubscription()
  if (existing) {
    // A subscription minted against a different server key can never be
    // delivered to, and re-subscribing over it throws. Drop it first.
    await existing.unsubscribe()
  }

  const subscription = await reg.pushManager.subscribe({
    // Required by Chrome: every push must result in a visible notification.
    userVisibleOnly: true,
    applicationServerKey: decodeServerKey(key) as BufferSource,
  })

  await api.pushSubscribe(subscription.toJSON())
}

/** Unregister this browser, server-side first. */
export async function disablePush(): Promise<void> {
  const subscription = await currentSubscription()
  if (!subscription) return

  // Server first: if the browser unsubscribes and the API call then fails, the
  // row survives and the server keeps pushing to a dead endpoint until it
  // happens to get a 410 back.
  await api.pushUnsubscribe(subscription.endpoint)
  await subscription.unsubscribe()
}

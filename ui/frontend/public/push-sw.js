/* Web Push handlers for Pick-a-Recipe.
 *
 * Imported into the Workbox-generated service worker via `workbox.importScripts`
 * in vite.config.ts. This lives in public/ as a plain script, rather than being
 * folded into the bundle, so the precaching setup stays exactly what Workbox
 * generates: switching the plugin to injectManifest purely to register two
 * listeners would mean hand-maintaining the whole caching configuration.
 */

/* global self */

const FALLBACK_TITLE = 'Pick-a-Recipe'
const DEFAULT_TAG = 'pick-a-recipe'

/**
 * Only ever navigate to a path on this origin.
 *
 * The URL comes from the push payload. Today the server is the only thing that
 * writes it, but a notification click is a navigation, so treat it as untrusted
 * and reject anything that could leave the origin -- including the
 * protocol-relative "//evil.example" form.
 */
function safePath(value) {
  if (typeof value !== 'string') return '/'
  if (!value.startsWith('/') || value.startsWith('//')) return '/'
  return value
}

self.addEventListener('push', (event) => {
  let payload = {}
  if (event.data) {
    try {
      payload = event.data.json()
    } catch {
      // A malformed or non-JSON payload should still surface something.
      payload = { body: event.data.text() }
    }
  }

  const title = payload.title || FALLBACK_TITLE
  const options = {
    body: payload.body || '',
    icon: '/icons/icon-192x192.png',
    badge: '/icons/icon-96x96.png',
    // One notification per job, replaced as it progresses: someone who was
    // away wants the job's current state, not a stack of its history.
    tag: payload.tag || DEFAULT_TAG,
    // ...but replacing silently would hide the outcome they were waiting for,
    // so still alert when a superseding notification arrives.
    renotify: true,
    data: { url: safePath(payload.url) },
  }

  event.waitUntil(self.registration.showNotification(title, options))
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()

  const target = safePath(event.notification.data && event.notification.data.url)

  event.waitUntil(
    self.clients
      .matchAll({ type: 'window', includeUncontrolled: true })
      .then((windows) => {
        // Reuse a tab that is already on the target, then any open tab.
        // Opening a third copy of the app to read one notification is a
        // worse outcome than reusing whatever is already there.
        for (const client of windows) {
          if (client.url.includes(target) && 'focus' in client) {
            return client.focus()
          }
        }
        for (const client of windows) {
          if ('navigate' in client && 'focus' in client) {
            return client.navigate(target).then((c) => (c ? c.focus() : undefined))
          }
        }
        return self.clients.openWindow(target)
      }),
  )
})

import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChefHat, Download, TriangleAlert } from 'lucide-react'
import { api } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'

type BeforeInstallPromptEvent = Event & {
  prompt: () => Promise<{ outcome: 'accepted' | 'dismissed' }>
}

function useInstallPrompt() {
  const [deferredPrompt, setDeferredPrompt] =
    useState<BeforeInstallPromptEvent | null>(null)
  const [installed, setInstalled] = useState(false)

  useEffect(() => {
    const onPrompt = (e: Event) => {
      setDeferredPrompt(e as BeforeInstallPromptEvent)
    }
    const onInstalled = () => {
      setInstalled(true)
      setDeferredPrompt(null)
    }
    window.addEventListener('beforeinstallprompt', onPrompt)
    window.addEventListener('appinstalled', onInstalled)
    return () => {
      window.removeEventListener('beforeinstallprompt', onPrompt)
      window.removeEventListener('appinstalled', onInstalled)
    }
  }, [])

  const install = async () => {
    if (!deferredPrompt) return
    const { outcome } = await deferredPrompt.prompt()
    if (outcome === 'accepted') {
      setDeferredPrompt(null)
    }
  }

  const isIosSafari = /iphone|ipad|ipod/i.test(navigator.userAgent) &&
    /safari/i.test(navigator.userAgent) &&
    !/(chrome|crios|fxios)/i.test(navigator.userAgent)

  const showInstall = !installed && !isIosSafari && deferredPrompt !== null

  return { showInstall, install }
}

export function LoginPage() {
  const { data, isLoading } = useQuery({
    queryKey: ['authStatus'],
    queryFn: api.authStatus,
    retry: false,
  })

  const { showInstall, install } = useInstallPrompt()

  const ssoEnabled = data?.sso_enabled ?? true

  return (
    <div className="flex min-h-svh items-center justify-center bg-background p-4">
      <div className="flex w-full max-w-sm flex-col items-center gap-6">
        <div className="flex flex-col items-center gap-3">
          <div className="flex size-16 items-center justify-center rounded-2xl bg-primary text-primary-foreground">
            <ChefHat className="size-8" />
          </div>
          <div className="text-center">
            <h1 className="text-xl font-semibold tracking-tight text-foreground">
              Pick a Recipe
            </h1>
            <p className="text-sm text-muted-foreground">
              Extract recipes from social media videos
            </p>
          </div>
        </div>

        <Card className="w-full">
          <CardContent className="flex flex-col gap-3 pt-4">
            {isLoading ? (
              <div className="h-9 w-full animate-pulse rounded-lg bg-muted" />
            ) : ssoEnabled ? (
              <Button asChild size="lg" className="w-full">
                <a href="/auth/login">Sign in with Authentik</a>
              </Button>
            ) : (
              <div
                role="alert"
                className="flex gap-2 rounded-lg border border-destructive/40 bg-destructive/10 px-3 py-2.5 text-sm text-destructive dark:border-destructive/30 dark:bg-destructive/20"
              >
                <TriangleAlert className="mt-0.5 size-4 shrink-0" />
                <span>
                  Single sign-on is not configured on this server. Set{' '}
                  <code className="rounded bg-destructive/15 px-1 font-mono text-xs">
                    AUTHENTIK_CLIENT_ID
                  </code>{' '}
                  and{' '}
                  <code className="rounded bg-destructive/15 px-1 font-mono text-xs">
                    AUTHENTIK_CLIENT_SECRET
                  </code>
                  , or set{' '}
                  <code className="rounded bg-destructive/15 px-1 font-mono text-xs">
                    AUTH_MODE=none
                  </code>{' '}
                  to run without authentication.
                </span>
              </div>
            )}

            {showInstall && (
              <Button
                variant="secondary"
                size="lg"
                className="w-full"
                onClick={() => void install()}
              >
                <Download />
                Install App
              </Button>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

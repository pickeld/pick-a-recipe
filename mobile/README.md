# Pick-a-Recipe — Android app

Flutter client for the Pick-a-Recipe server. Asks which instance to talk to,
then signs in whichever way that instance supports, and talks to the
`/api/mobile/*` endpoints with JWT bearer tokens.

## Requirements

- Flutter 3.44 or newer (stable channel)
- A running Pick-a-Recipe server with `JWT_SECRET_KEY` set — without it the
  mobile endpoints return 503 and sign-in cannot complete

## Which server, and which sign-in

Neither is compiled in. Everyone running Pick-a-Recipe runs their own server
with their own authentication, so a published APK cannot know either.

On first launch the app asks for a server address. It types short — `https://`
is assumed, a trailing slash is dropped, a port or subpath is kept — and nothing
is saved until the address answers, so a typo leaves the form on screen instead
of stranding the app somewhere unreachable. **Change** on the sign-in screen
returns to it, clearing the stored tokens along with the address, since tokens
mean nothing to a different instance.

The app then reads `GET /api/auth/status` and offers only what that server has:

| Server state | What the app shows |
|--------------|--------------------|
| `AUTH_MODE=local` | Username and password, posted to `/api/mobile/auth/login` |
| `AUTH_MODE=authentik` | A button that opens Authentik in the system browser |
| No account yet | A prompt to finish setup in a browser, and a re-check button |
| `JWT_SECRET_KEY` unset | An explanation that app sign-in is switched off |

A plain-`http://` address is allowed — plenty of self-hosted instances are HTTP
on a home network, and refusing would leave those users with no app — but the
address is shown with an open padlock and the password form says plainly that the
password will travel unencrypted.

That required permitting cleartext in `res/xml/network_security_config.xml`,
which is a deliberate exception to the HTTPS-only default: Android's policy takes
exact hosts rather than CIDR blocks, so "cleartext only on a private network"
cannot be expressed. Certificate validation is *not* relaxed — a self-signed
HTTPS certificate is still rejected, and the app says so rather than failing
silently.

## Running

```bash
flutter run                                        # prefills http://10.0.2.2:5006
flutter run --dart-define=FLAVOR=prod              # no prefill, as a release build
flutter run --dart-define=API_BASE_URL=http://192.168.1.10:5006   # prefill a host
```

`10.0.2.2` is the Android emulator's alias for the host machine; on a physical
device use your machine's LAN address. Both only *prefill* the address field —
the value the app uses is the one entered and stored on the device.

```bash
flutter test
flutter analyze
```

## How password sign-in works

Under `AUTH_MODE=local` there is no identity provider to visit, so the app posts
the credentials itself:

`POST /api/mobile/auth/login` with `{username, password}` returns the same token
pair the browser flow ends with. The server verifies the Argon2id hash and
applies the *same* throttle ladder as the web form, keyed on (IP, username) — so
the app endpoint is not a way around the form's rate limit, or the reverse.
Wrong password and unknown account are indistinguishable in the response.

The endpoint is refused under `AUTH_MODE=authentik`: there, group membership
governs access rather than any password an account happens to carry, so
honouring one would be a way around single sign-on.

## How Authentik sign-in works

The app never handles the OIDC client secret, and no credentials are entered
inside the app.

1. The app asks the server for an authorization URL:
   `GET /api/mobile/auth/login-url?redirect=par://auth/callback`. The server
   mints a single-use nonce, uses it as the OAuth `state`, and returns the
   Authentik URL.
2. The URL opens in the **system browser** — not a webview — so an existing
   Authentik session cookie is reused and credential entry stays outside the
   app.
3. Authentik redirects to the server's own callback,
   `https<server>/auth/callback`. The server recognises the nonce as a mobile
   sign-in, exchanges the code for tokens using its confidential client
   credentials, and checks the user's Authentik group.
4. The server redirects to `par://auth/callback#access_token=…&refresh_token=…`.
   Tokens ride in the URL **fragment**, which browsers never send to servers,
   keeping them out of access logs and `Referer` headers.
5. The app receives the deep link, stores the pair in the platform keystore,
   and confirms it with `GET /api/mobile/me` before showing a signed-in UI.

Because the redirect URI registered with Authentik is the server's own
callback, **no Authentik configuration change is needed** for the app — the
`par://` scheme is only ever seen by the device.

### Tokens

Access tokens last 15 minutes, refresh tokens 30 days. `AuthInterceptor`
attaches the access token to every request and, on a 401, refreshes once and
replays the original request, so expiry is invisible. Concurrent 401s share a
single refresh so the rotating refresh token is not spent more than once. A
rejected refresh clears the pair and returns the user to the login screen.

Tokens live in `flutter_secure_storage`, which on Android encrypts values with
AES-GCM under a key wrapped by RSA-OAEP in the hardware keystore. They are
never written to shared preferences and never logged.

### Deep link registration

The `par://auth/callback` intent filter is declared in
`android/app/src/main/AndroidManifest.xml`, and the server will only redirect
to schemes listed in its `MOBILE_DEEP_LINK_SCHEMES`. Changing the scheme means
changing both.

> Custom schemes are not exclusive on Android: another installed app can
> register `par://` and receive the redirect. Moving to verified Android App
> Links (an `https://` callback plus a hosted `assetlinks.json`) would close
> that gap and is tracked as follow-up work.

## Layout

```
lib/src/
  config/      build flavor and the address prefill
  core/        secure token + server-address storage, Dio client + auth
               interceptor, deep links
  features/
    auth/      server address and sign-in controllers, callback parsing,
               login screen
    home/      signed-in landing screen
  router.dart  go_router with auth-driven redirects
```

`Dio`'s base URL is derived from the stored address, so it is rebuilt when the
server changes and no client is left pointing at the old one.

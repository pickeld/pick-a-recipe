# Pick-a-Recipe — Android app

Flutter client for the Pick-a-Recipe server. Signs in against the same
Authentik tenant as the web UI and talks to the `/api/mobile/*` endpoints with
JWT bearer tokens.

## Requirements

- Flutter 3.44 or newer (stable channel)
- A running Pick-a-Recipe server with `JWT_SECRET_KEY` set — without it the
  mobile endpoints return 503 and sign-in cannot complete

## Running

The API host comes from the build flavor, so there is nothing to edit in the
source:

```bash
flutter run                                        # dev: http://10.0.2.2:5006
flutter run --dart-define=FLAVOR=prod              # https://recipes.pickel.me
flutter run --dart-define=API_BASE_URL=http://192.168.1.10:5006   # explicit host
```

`10.0.2.2` is the Android emulator's alias for the host machine. On a physical
device, pass `API_BASE_URL` with your machine's LAN address. Cleartext HTTP is
permitted only for those loopback/dev hosts; see
`android/app/src/main/res/xml/network_security_config.xml`.

```bash
flutter test
flutter analyze
```

## How sign-in works

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
  config/      build-flavor and API host resolution
  core/        secure token storage, Dio client + auth interceptor, deep links
  features/
    auth/      sign-in controller, callback parsing, login screen
    home/      signed-in landing screen
  router.dart  go_router with auth-driven redirects
```

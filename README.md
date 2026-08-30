<p align="center">
  <img src="ui/static/icons/icon-192x192.png" alt="Pick-a-Recipe" width="120" height="120">
</p>

# Pick-a-Recipe

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![Docker Hub](https://img.shields.io/docker/v/pickeld/pick-a-recipe?label=Docker%20Hub&logo=docker)](https://hub.docker.com/r/pickeld/pick-a-recipe)
[![Docker Pulls](https://img.shields.io/docker/pulls/pickeld/pick-a-recipe?logo=docker)](https://hub.docker.com/r/pickeld/pick-a-recipe)
[![Flask](https://img.shields.io/badge/Flask-Web_UI-green.svg)](https://flask.palletsprojects.com/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Extract recipes from social media videos (TikTok, YouTube, Instagram, etc.) and automatically import them into your self-hosted recipe manager.

## Overview

Pick-a-Recipe is a Python application that:

1. **Downloads videos** from TikTok, YouTube, Instagram, and other platforms using `yt-dlp`
2. **Transcribes audio** using Whisper AI (via `faster-whisper`)
3. **Extracts on-screen text** (ingredients, instructions) using vision-capable LLMs
4. **Generates structured recipes** using AI (OpenAI GPT or Google Gemini)
5. **Uploads to recipe managers** - supports [Tandoor](https://tandoor.dev/) and [Mealie](https://mealie.io/)

### Features

- 🎥 Multi-platform video support (TikTok, YouTube, Instagram, etc.)
- 🎙️ Audio transcription with language detection
- 👁️ Visual text extraction from video frames
- 🤖 AI-powered recipe generation with structured ingredients
- 🍽️ Automatic nutrition and serving size estimation
- 🖼️ Dish image extraction with manual selection option
- 🌐 Web UI with real-time progress updates
- 🔐 User authentication and settings management
- 🐳 Docker support for easy deployment
- 📱 **PWA Support** - Install as app on mobile, share URLs directly from Android/iOS

## Requirements

- Python 3.11+
- FFmpeg (for video/audio processing)
- API key for OpenAI or Google Gemini
- Self-hosted Tandoor or Mealie instance (optional)

## Installation

### Using Docker (Recommended)

**Option 1: Pull from Docker Hub (Easiest)**

```bash
docker run -d \
  --name pick-a-recipe \
  -p 5006:5006 \
  -e FLASK_SECRET_KEY="your-secure-secret-key" \
  -v pick-a-recipe-data:/app/data \
  pickeld/pick-a-recipe:latest
```

Open `http://localhost:5006` and pick a username and password. That first visit
is the only time the setup page is available; once an account exists it closes
for good.

No identity provider is needed. If you would rather sign in through Authentik,
see [Authentication](#authentication).

**Option 2: Using Docker Compose**

Create a `docker-compose.yml` file:

```yaml
version: "3.8"

services:
  pick-a-recipe:
    image: pickeld/pick-a-recipe:latest
    container_name: pick-a-recipe
    restart: unless-stopped
    ports:
      - "5006:5006"
    environment:
      - FLASK_SECRET_KEY=your-secure-secret-key
    volumes:
      - pick-a-recipe-data:/app/data

volumes:
  pick-a-recipe-data:
```

Then run:

```bash
docker-compose up -d
```

Open `http://localhost:5006` and create your account.

### Manual Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/pickeld/pick-a-recipe.git
   cd pick-a-recipe
   ```

2. Install system dependencies:
   ```bash
   # macOS
   brew install ffmpeg

   # Ubuntu/Debian
   sudo apt-get install ffmpeg
   ```

3. Create a virtual environment and install Python dependencies:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

4. Run the application:
   ```bash
   python ui/app.py
   ```

5. Open `http://localhost:5006` and create your account on the setup page.

## Configuration

All configuration is managed through the web UI settings page (`/settings`).

### Authentication

Sign-in is always required. Settings holds your LLM, Mealie and Tandoor API
keys, so an instance anyone can reach is an instance that hands those out.

Two modes, selected with `AUTH_MODE`:

| `AUTH_MODE` | Behaviour |
|-------------|-----------|
| `local` (default) | Username and password accounts stored by this app. No identity provider needed. |
| `authentik` | Authentik single sign-on (OIDC). Accounts and groups come from Authentik. |

**`local` — accounts stored by this app (default)**

Start the app and open it in a browser. With no account yet, every page redirects
to `/setup`, where you choose a username and password:

```bash
docker run -d --name pick-a-recipe -p 5006:5006 \
  -v pick-a-recipe-data:/app/data \
  pickeld/pick-a-recipe:latest
```

The setup page is reachable only while the instance has no account, and closes
permanently once one exists — so nobody can use it to add a second admin or
reset your password. If your instance is already reachable from the internet
when you first start it, create the account promptly: until you do, whoever
loads that page first gets it. `AUTH_LOCAL_USERNAME` prefills the username
field, nothing more.

Passwords must be at least 10 characters, with no composition rules: length is
what makes a password hard to guess, while forced symbols mostly produce
predictable substitutions. They are stored as Argon2id hashes with a per-account
salt, and rehashed automatically if the cost parameters are ever raised.

Repeated failures are slowed progressively rather than locking the account,
which would let anyone who knows your username lock you out at will. Wrong
password and no-such-user return the same message and take comparable time, so
the login form cannot be used to discover which accounts exist.

**Upgrading from `AUTH_MODE=none`**

That mode is gone; authentication can no longer be switched off. An instance
still setting it boots with a warning and is treated as `local`, landing on the
setup page. Ownership of recipes and history is recorded against the username
that mode used, so the setup page offers that name and reuses the account rather
than creating a second one — accept it, pick a password, and your history
carries over. Typing a different name starts with an empty history. Update your
configuration to `AUTH_MODE=local`, or drop the variable entirely.

**`authentik` — single sign-on**

Requires an [Authentik](https://goauthentik.io/) instance. Create an OAuth2/OIDC
provider for the app and set:

```bash
AUTH_MODE=authentik
AUTHENTIK_ISSUER_URL=https://auth.example.com/application/o/pick-a-recipe
AUTHENTIK_CLIENT_ID=...
AUTHENTIK_CLIENT_SECRET=...
```

Access is gated on group membership: users need `AUTHENTIK_USER_GROUP`
(default `pick-a-recipe-users`), and admins additionally need
`AUTHENTIK_ADMIN_GROUP` (default `admins`). Add the *authentik read groups*
scope mapping to the provider so the `groups` claim is present in tokens.

Behind a reverse proxy, set `PUBLIC_URL` (or `AUTHENTIK_REDIRECT_URI`) so the
OIDC callback URL matches what you registered in Authentik, and set
`SESSION_COOKIE_SECURE=true` when serving over HTTPS.

Password sign-in is refused outright in this mode, so an account that happens to
carry a password cannot be used to go around single sign-on.

With `AUTH_MODE=authentik` set but no client credentials configured, nobody can
sign in and the login page says so. It fails closed on purpose; drop `AUTH_MODE`
to fall back to local accounts.

**Android app sign-in**

The Android app in [`mobile/`](mobile/) authenticates with bearer tokens. Set a
signing key to enable it:

```bash
JWT_SECRET_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
```

With `JWT_SECRET_KEY` unset the whole mobile surface stays disabled and those
endpoints return 503. The app cannot hold the OIDC client secret, so it opens the
system browser against Authentik, the server completes the code exchange, and the
resulting token pair is handed back over a custom-scheme deep link
(`par://auth/callback` by default, configurable with
`MOBILE_DEEP_LINK_SCHEMES`). Tokens travel in the URL fragment, which browsers do
not send to servers or leak via `Referer`.

Access tokens last 15 minutes and refresh tokens 30 days; `POST
/api/mobile/auth/refresh` re-reads the account on every refresh, so removing
someone from the Authentik group takes effect within the access-token lifetime
rather than lasting for the life of the refresh token. Group membership is
enforced by the same check as the web flow.

Bearer tokens are accepted on the existing API endpoints alongside cookie
sessions; cookies take precedence when both are present.

### Settings

| Setting | Description |
|---------|-------------|
| **LLM Provider** | Choose between OpenAI or Google Gemini |
| **OpenAI API Key** | Your OpenAI API key (required if using OpenAI) |
| **OpenAI Model** | Model to use (default: `gpt-5-mini-2025-08-07`) |
| **Gemini API Key** | Your Google Gemini API key (required if using Gemini) |
| **Gemini Model** | Model to use (default: `gemini-2.5-flash`) |
| **Recipe Language** | Target language for recipe output (e.g., `hebrew`, `english`) |
| **Target Language Code** | ISO language code for transcription (e.g., `he`, `en`) |
| **Whisper Model** | Whisper model size (`tiny`, `small`, `medium`, `large`) |
| **Output Target** | Recipe manager: `tandoor` or `mealie` |
| **Tandoor Host** | URL of your Tandoor instance |
| **Tandoor API Key** | API token from Tandoor |
| **Mealie Host** | URL of your Mealie instance |
| **Mealie API Key** | API token from Mealie |
| **Confirm Before Upload** | Show recipe preview before uploading |

## Usage

### Web UI

1. Navigate to `http://localhost:5006`
2. Log in with your credentials
3. Paste a video URL (TikTok, YouTube, Instagram, etc.)
4. Click "Extract Recipe"
5. Watch the real-time progress as the video is processed
6. If "Confirm Before Upload" is enabled, review and optionally edit the recipe
7. The recipe is automatically uploaded to your configured recipe manager

### PWA / Mobile App (Share Links Directly)

Pick-a-Recipe supports PWA (Progressive Web App) installation, allowing you to share video links directly from your phone:

#### Android
1. Open `https://your-server:5006` in Chrome
2. Tap the menu (⋮) → "Add to Home screen"
3. Now when sharing any video link, choose "Pick-a-Recipe" from the share sheet

#### iPhone / iPad
1. Open `https://your-server:5006` in Safari
2. Tap the Share button → "Add to Home Screen"
3. Open the app from your home screen
4. Share video links from TikTok/Instagram/YouTube using the Share button → "Pick-a-Recipe"

> **Note:** PWA features require HTTPS in production. For local testing, `localhost` works without HTTPS.

### Command Line

For testing or batch processing, you can use the CLI:

```bash
# Basic usage
python main.py "https://www.tiktok.com/@user/video/1234567890"

# Skip upload (just generate recipe JSON)
python main.py --no-upload "https://www.youtube.com/watch?v=VIDEO_ID"
```

## Project Structure

```
pick-a-recipe/
├── main.py              # CLI entry point
├── chef.py              # AI recipe generation
├── config.py            # Configuration management
├── video_downloader.py  # Video downloading (yt-dlp)
├── transcriber.py       # Audio transcription (Whisper)
├── image_extractor.py   # Dish image extraction
├── mealie.py            # Mealie API integration
├── tandoor.py           # Tandoor API integration
├── recipe_exporter.py   # Recipe export utilities
├── helpers.py           # Utility functions and prompts
├── llm_providers/       # LLM provider implementations
│   ├── base.py
│   ├── openai.py
│   └── gemini.py
├── ui/                  # Flask web UI
│   ├── app.py           # Flask application
│   ├── database.py      # SQLite database management
│   ├── templates/       # HTML templates
│   └── static/          # CSS and JavaScript
├── Dockerfile
├── docker-compose.yml
├── docker-compose.srv2.yml   # srv2 reference (see portainer/ for production)
├── build-and-push.sh         # Publish pickeld/pick-a-recipe to Docker Hub
├── portainer/
│   ├── stack.yml             # Portainer / srv2 production stack
│   └── stack.env             # Stack env template (copy to stack.env.local)
├── scripts/
│   ├── portainer-migrate.sh  # srv2 deploy: pull image + restart stack
│   └── portainer-deploy.sh   # Deploy via Portainer API
└── requirements.txt
```

## Docker Deployment

### Docker Hub Image

The official image is available on Docker Hub: [`pickeld/pick-a-recipe`](https://hub.docker.com/r/pickeld/pick-a-recipe)

```bash
# Pull the latest image
docker pull pickeld/pick-a-recipe:latest

# Or pull a specific version
docker pull pickeld/pick-a-recipe:v1.0.0
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `HOST` | Host to bind to | `0.0.0.0` |
| `PORT` | Port to listen on | `5006` |
| `FLASK_SECRET_KEY` | Secret key for session cookies | Auto-generated |
| `FLASK_DEBUG` | Enable debug mode | `false` |
| `MAX_CONCURRENT_JOBS` | Parallel extraction workers (1–16) | `3` (or Settings value) |
| `AUTH_MODE` | `local` for accounts stored by this app, or `authentik` for SSO ([details](#authentication)) | `local` |
| `AUTH_LOCAL_USERNAME` | Prefills the username field on the setup page | `admin` |
| `AUTHENTIK_ISSUER_URL` | Authentik OIDC issuer URL | `https://auth.pickel.me/application/o/pick-a-recipe` |
| `AUTHENTIK_CLIENT_ID` | Authentik OAuth2 client ID (required when `AUTH_MODE=authentik`) | — |
| `AUTHENTIK_CLIENT_SECRET` | Authentik OAuth2 client secret | — |
| `AUTHENTIK_USER_GROUP` | Authentik group required for access | `pick-a-recipe-users` |
| `AUTHENTIK_ADMIN_GROUP` | Authentik group granting admin rights | `admins` |
| `JWT_SECRET_KEY` | Signing key for Android app tokens; unset disables mobile auth | — |
| `MOBILE_DEEP_LINK_SCHEMES` | Comma-separated URL schemes the app may receive tokens on | `par` |
| `SESSION_COOKIE_SECURE` | Set secure cookie flag (use with HTTPS) | `false` |

### Docker Compose (Using Docker Hub)

```yaml
version: "3.8"

services:
  pick-a-recipe:
    image: pickeld/pick-a-recipe:latest
    container_name: pick-a-recipe
    restart: unless-stopped
    ports:
      - "5006:5006"
    environment:
      - HOST=0.0.0.0
      - PORT=5006
      - FLASK_SECRET_KEY=your-secure-secret-key
      # Local accounts by default; create yours on first visit at /setup
    volumes:
      - pick-a-recipe-data:/app/data

volumes:
  pick-a-recipe-data:
```

### Building and Publishing to Docker Hub

Multi-arch image (`linux/amd64`, `linux/arm64`):

```bash
docker logout && docker login -u pickeld   # or pickeld@gmail.com
./build-and-push.sh latest
```

Published as [`pickeld/pick-a-recipe`](https://hub.docker.com/r/pickeld/pick-a-recipe) on Docker Hub.

### srv2 / Portainer deployment

Production on srv2 is managed by **Portainer**. Add credentials to `portainer/stack.env.local`, then deploy:

```bash
cd /opt/pick-a-recipe
cp portainer/stack.env portainer/stack.env.local
# Edit stack.env.local: FLASK_SECRET_KEY, PORTAINER_URL, PORTAINER_USER, PORTAINER_PASSWORD

./scripts/portainer-migrate.sh
```

Or deploy/update directly:

```bash
./scripts/portainer-deploy.sh --pull --force-recreate
```

> **Important:** Do not use `docker compose up` directly — Portainer will show *"created outside of Portainer"* and limit control. Always deploy via the scripts above or the Portainer UI.

> **Note:** Existing srv2 installs may still use the legacy Docker volume `social_recipe_social-recipes` for data; the stack preserves it automatically.

### Building from Source

If you prefer to build the image yourself:

```bash
git clone https://github.com/pickeld/pick-a-recipe.git
cd pick-a-recipe
docker build -t pick-a-recipe .
docker run -p 5006:5006 -e FLASK_SECRET_KEY="your-secret" pick-a-recipe
```

## Supported Platforms

Pick-a-Recipe uses `yt-dlp` for video downloading, which supports:

- TikTok
- YouTube
- Instagram Reels
- Facebook Videos
- Twitter/X Videos
- And [many more](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)

### Instagram troubleshooting

Instagram frequently blocks automated downloads. Pick-a-Recipe installs `yt-dlp[curl-cffi]` for browser impersonation, which is required for most public reels.

If you still see **"Instagram sent an empty media response"**:

1. **Update** to the latest Docker image or reinstall: `pip install "yt-dlp[curl-cffi]"`
2. **Confirm the reel opens** in a private/incognito browser window (not logged in). If it does not, the post is private — upload a `cookies.txt` in Settings while logged into Instagram.
3. **Upload cookies** in Settings → Video Downloads (export from your browser while logged into `instagram.com`).

This is an upstream Instagram/yt-dlp limitation, not a bug in the recipe extraction itself. See [yt-dlp issue #17074](https://github.com/yt-dlp/yt-dlp/issues/17074) for background.

## License

MIT License - see LICENSE file for details.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

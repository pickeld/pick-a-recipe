# Pick-a-Recipe Web UI

A modern web interface for the Pick-a-Recipe video recipe extractor.

## Features

- 🔐 **Authentik SSO (OIDC)** — no local passwords, or `AUTH_MODE=none` to run without sign-in
- 📹 **URL Input** - Paste video URLs from TikTok, YouTube, Instagram, etc.
- 📊 **Real-time Progress** - Watch the extraction process with live updates
- ⚙️ **Configuration Management** - Save all settings through the web interface
- 🎨 **shadcn/ui design system** — dark-first theming with light mode, React + Tailwind v4
- 📱 **PWA** — installable, Android share-target, web push notifications

## Architecture

The frontend is a React SPA (`frontend/`, Vite + TypeScript + Tailwind CSS v4 +
shadcn/ui components) talking to the Flask backend over its JSON API and
Socket.IO. Flask serves the compiled SPA from `frontend/dist/` and falls back
to the legacy Jinja templates in `templates/` when no build exists, so older
deployments keep working without Node.

Design tokens follow the shadcn convention (CSS custom properties on `:root`
and `.dark`), which keeps the palette portable for future native apps.

## Frontend development

```bash
cd ui/frontend
npm install
npm run dev        # dev server on :5173, proxies API/socket/auth to :5006
npm run build      # production build into dist/
```

Run the Flask server alongside it during development:

```bash
cd ui && python app.py   # serves API on :5006
```

Conventions live in `frontend/FRONTEND_SPEC.md`.

## Installation

1. Install the required dependencies:

```bash
pip install -r requirements.txt
```

2. Run the UI server:

```bash
cd ui
python app.py
```

3. Open your browser and navigate to: `http://localhost:5006`

Or run via Docker:

```bash
docker run -d -p 5006:5006 -v pick-a-recipe-data:/app/data pickeld/pick-a-recipe:latest
```

## Authentication

Set by `AUTH_MODE`:

- `authentik` (default) — sign-in required via Authentik single sign-on (OIDC).
  Needs `AUTHENTIK_CLIENT_ID` and `AUTHENTIK_CLIENT_SECRET`.
- `none` — no sign-in; every request runs as one local admin (`local`, or
  `AUTH_LOCAL_USERNAME`). Convenient for local development, but there is no
  access control whatsoever, so keep it off any untrusted network.

See the [root README](../README.md#authentication) for the full setup.

## Configuration

All settings can be configured through the web interface by clicking the gear icon (⚙️) in the sidebar:

### LLM Provider
- Choose between OpenAI and Google Gemini
- Configure API keys and model names

### Recipe Output
- Select output target (Tandoor or Mealie)
- Set recipe language preferences

### Mealie Settings
- Mealie server URL
- API key

### Tandoor Settings
- Tandoor server URL
- API key

### Whisper Transcription
- Choose transcription model size (tiny, base, small, medium, large-v3)

## File Structure

```
ui/
├── app.py                 # Flask app: routes, OIDC auth, REST API, Socket.IO, SPA serving
├── database.py            # SQLite persistence (jobs, history, config, push subs)
├── job_manager.py         # Job queue, concurrency, approval flow
├── frontend/              # React SPA (Vite + TS + Tailwind v4 + shadcn/ui)
│   ├── FRONTEND_SPEC.md   # Frontend conventions & API contract notes
│   ├── src/pages/         # login / home / job / tasks / settings
│   ├── src/components/    # shared UI (JobCard, RecipeView, ImagePicker) + ui/* primitives
│   ├── src/lib/           # typed API client + Socket.IO hooks
│   └── dist/              # production build served by Flask (generated)
├── templates/             # Legacy Jinja fallback (used only when dist/ is absent)
└── static/                # Legacy CSS/JS/icons (same fallback)
```

## Database

The UI uses SQLite for data storage. A single database file is created in the project root:

- `data/pick-a-recipe.db` - SQLite database containing:
  - `users` table - User credentials (hashed passwords)
  - `config` table - Configuration key-value pairs

## WebSocket Progress Events

The UI uses Socket.IO for real-time progress updates. The stages are:

1. `info` - Getting video information
2. `download` - Downloading video
3. `transcribe` - Transcribing audio
4. `visual` - Extracting on-screen text
5. `image` - Extracting dish image
6. `evaluate` - Creating recipe with AI
7. `upload` - Uploading to recipe manager
8. `complete` / `error` - Final status

## Security Notes

- Authentication is delegated to Authentik single sign-on (OIDC) — no local passwords
- Access requires membership in the configured Authentik user group; admin features require the admin group
- `AUTH_MODE=none` disables authentication entirely and must only be used on a trusted network
- Session management uses Flask's secure sessions
- API keys are stored in the local configuration file

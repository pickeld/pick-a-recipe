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

Access the web UI at `http://localhost:5006`

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

Access the web UI at `http://localhost:5006`

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

5. Access the web UI at `http://localhost:5006`

## Configuration

All configuration is managed through the web UI settings page (`/settings`). On first run, use the default credentials:

- **Username:** `admin`
- **Password:** `admin123`

> ⚠️ **Important:** Change the default password immediately after first login!

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

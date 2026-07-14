# Pick-a-Recipe - Codebase Analysis

## Overview

Pick-a-Recipe is a Python-based application that extracts recipes from social media videos (TikTok, YouTube, Instagram, etc.) and uploads them to self-hosted recipe managers like Tandoor and Mealie. The application uses AI for transcription, visual text extraction, and recipe generation.

## Tech Stack

### Core Technologies
- **Python 3.11+** - Primary language
- **Flask** - Web framework for the UI
- **SQLite** - Database for configuration and history
- **Docker** - Containerization support

### Key Dependencies
- **yt-dlp** - Video downloading from multiple platforms
- **faster-whisper** - Audio transcription using Whisper AI
- **openai** - OpenAI GPT API integration
- **google-genai** - Google Gemini API integration
- **flask-socketio** - Real-time web communication
- **eventlet** - Async server support

## Project Structure

```
pick-a-recipe/
├── main.py              # CLI entry point
├── chef.py              # AI recipe generation core
├── config.py            # Configuration management (SQLite-based)
├── video_downloader.py  # Video downloading (yt-dlp wrapper)
├── transcriber.py       # Audio transcription + visual text extraction
├── image_extractor.py   # Dish image extraction from video
├── mealie.py            # Mealie recipe manager integration
├── tandoor.py           # Tandoor recipe manager integration
├── recipe_exporter.py   # Recipe export utilities
├── helpers.py           # Utility functions and AI prompts
├── llm_providers/       # Modular LLM provider implementations
│   ├── base.py          # Abstract base class
│   ├── openai.py        # OpenAI GPT integration
│   └── gemini.py        # Google Gemini integration
├── ui/                  # Flask web application
│   ├── app.py           # Main Flask app with authentication
│   ├── database.py      # SQLite database operations
│   ├── job_manager.py   # Background job processing
│   ├── templates/       # HTML templates
│   └── static/          # CSS, JS, and PWA assets
├── Dockerfile           # Container configuration
├── docker-compose.yml   # Docker Compose setup
└── requirements.txt     # Python dependencies
```

## Architecture

### Processing Pipeline
1. **Video Download** (`video_downloader.py`) - Uses yt-dlp to download videos from social platforms
2. **Audio Transcription** (`transcriber.py`) - Extracts and transcribes audio using Whisper
3. **Visual Text Extraction** (`transcriber.py`) - Uses vision-capable LLMs to extract on-screen text
4. **Image Extraction** (`image_extractor.py`) - Extracts best dish image from video frames
5. **Recipe Generation** (`chef.py`) - Uses LLMs to create structured recipe from combined data
6. **Recipe Upload** (`mealie.py`/`tandoor.py`) - Uploads to recipe manager APIs

### Web Application
- **Flask Backend** with SocketIO for real-time updates
- **SQLite Database** for user authentication, configuration, and job history
- **Background Job Processing** with progress tracking
- **PWA Support** for mobile app installation and sharing integration

### LLM Provider Pattern
- **Abstract Base Class** (`llm_providers/base.py`) for consistent interface
- **OpenAI Provider** (`llm_providers/openai.py`) - GPT models
- **Gemini Provider** (`llm_providers/gemini.py`) - Google Gemini models
- **Configurable Selection** via web UI settings

## Key Features

### Core Functionality
- 🎥 **Multi-platform Video Support** - TikTok, YouTube, Instagram, Facebook, Twitter/X
- 🎙️ **Audio Transcription** - Automatic language detection with Whisper AI
- 👁️ **Visual Text Extraction** - OCR-like capabilities using vision LLMs
- 🤖 **AI Recipe Generation** - Structured recipe creation with ingredients and instructions
- 🍽️ **Nutrition Estimation** - Automatic serving size and nutrition calculation
- 🖼️ **Image Extraction** - Automatic dish image extraction with manual selection

### User Experience
- 🌐 **Web UI** - Flask-based interface with real-time progress
- 🔐 **Authentication** - User login with configurable credentials
- ⚙️ **Settings Management** - Web-based configuration for all options
- 📱 **PWA Support** - Install as mobile app, direct sharing from social apps
- 🐳 **Docker Support** - Easy deployment via Docker Hub

### Integration
- 🔗 **Recipe Manager Support** - Tandoor and Mealie integration
- 📊 **Job History** - Track processing jobs with status and results
- 🔄 **Background Processing** - Non-blocking job execution
- 💾 **Caching** - Transcription and visual text caching to avoid re-processing

## Local Setup Instructions

### Using Docker (Recommended)

**Quick Start:**
```bash
docker run -d \
  --name pick-a-recipe \
  -p 5006:5006 \
  -e FLASK_SECRET_KEY="your-secure-secret-key" \
  -v pick-a-recipe-data:/app/data \
  pickeld/pick-a-recipe:latest
```

**Docker Compose:**
```bash
# Use the provided docker-compose.yml
docker-compose up -d
```

### Manual Installation

**Prerequisites:**
```bash
# Install system dependencies
sudo apt-get install ffmpeg  # Ubuntu/Debian
brew install ffmpeg          # macOS
```

**Setup:**
```bash
# Clone and setup
git clone https://github.com/pickeld/pick-a-recipe.git
cd pick-a-recipe
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run web UI
python ui/app.py

# Or run CLI
python main.py "https://www.tiktok.com/@user/video/123"
```

**Access:**
- Web UI: `http://localhost:5006`
- Default credentials: `admin` / `admin123`

## Configuration

### Database-Driven Config
- All settings stored in SQLite database (`data/pick-a-recipe.db`)
- Web UI provides settings page for configuration
- Fallback to sensible defaults if database unavailable

### Key Settings
- **LLM Provider**: OpenAI or Google Gemini
- **API Keys**: OpenAI or Gemini authentication
- **Language Settings**: Recipe output language and transcription target
- **Recipe Manager**: Tandoor or Mealie integration
- **Processing Options**: Whisper model size, confirmation workflows

## Notable Patterns and Conventions

### Code Organization
- **Single Responsibility**: Each module has a clear, focused purpose
- **Configuration Abstraction**: Database-backed config with property-based access
- **Provider Pattern**: Pluggable LLM providers with consistent interface
- **Caching Strategy**: File-based caching for expensive operations (transcription, visual text)

### Error Handling
- **Graceful Degradation**: Visual text extraction failures don't break the pipeline
- **Logging**: Structured logging throughout with step-based progress
- **Validation**: Input validation and API error handling

### Performance Optimizations
- **Caching**: Transcription and visual text cached by language and video ID
- **Background Jobs**: Non-blocking processing via job manager
- **Progressive Loading**: Real-time updates via WebSockets

### Security Considerations
- **Session Management**: Secure Flask sessions with persistent secret keys
- **File Permissions**: Restricted permissions on secret key files
- **Input Validation**: URL validation and sanitization
- **API Key Storage**: Encrypted storage in SQLite database

## Development Considerations

### Extensibility
- New LLM providers can be added by extending `llm_providers/base.py`
- Recipe manager integrations follow consistent pattern (`mealie.py`, `tandoor.py`)
- Configuration system easily supports new settings

### Testing
- CLI supports `--no-upload` flag for testing without recipe manager
- Default test URL provided in argument parser
- Caching reduces costs during development iterations

### Deployment
- Docker Hub images available as `pickeld/pick-a-recipe`
- Volume mounts preserve data and configuration
- Environment variable overrides for deployment flexibility

This codebase represents a well-structured, production-ready application with clear separation of concerns, comprehensive error handling, and thoughtful user experience design.
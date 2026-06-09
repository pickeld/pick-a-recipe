FROM python:3.11-slim

# Install system dependencies for faster-whisper and video processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install deno (required by yt-dlp for YouTube extraction)
RUN curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose the web UI port
EXPOSE 5006

# Container health probe: hits the unauthenticated /api/health endpoint which
# verifies yt-dlp + the configured LLM. Marks the container unhealthy when the
# top outage classes (model drift / yt-dlp drift) are present.
HEALTHCHECK --interval=5m --timeout=20s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:5006/api/health || exit 1

# Default environment variables
ENV FLASK_DEBUG=false
ENV PYTHONPATH=/app

# Upgrade yt-dlp to latest version on startup and run the Flask application
CMD ["sh", "-c", "pip install --upgrade yt-dlp && python ui/app.py"]

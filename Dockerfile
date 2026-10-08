# ── Stage 1: build the React SPA ─────────────────────────────────────────────
FROM node:22-alpine AS node-build

WORKDIR /build

# Install dependencies (leverages layer cache when package files unchanged)
COPY ui/frontend/package*.json ./
RUN npm ci

# Copy source and build
COPY ui/frontend/ ./
RUN npm run build

# ── Stage 2: Python application ───────────────────────────────────────────────
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

# Overlay the freshly-built SPA from the node-build stage
COPY --from=node-build /build/dist /app/ui/frontend/dist

# Expose the web UI port
EXPOSE 5006

# Liveness only. /api/health returns 503 until an AI provider key is saved,
# which is the normal state of a new container. A NAS that treats an unhealthy
# container as down would never let that first visit through.
HEALTHCHECK --interval=5m --timeout=20s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:5006/healthz || exit 1

# Default environment variables
ENV FLASK_DEBUG=false
ENV PYTHONPATH=/app

# Refresh yt-dlp on startup so extractors keep up with the sites, but do not
# wait on the network. A NAS often has no DNS yet when the container is first
# started, and a failed upgrade used to keep the server from listening at all.
# The image already contains a working yt-dlp.
CMD ["sh", "-c", "pip install --disable-pip-version-check --retries 0 --timeout 15 --upgrade \"yt-dlp[curl-cffi]\" || echo '[startup] yt-dlp upgrade skipped'; exec python ui/app.py"]

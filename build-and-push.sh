#!/bin/bash
#
# Build and push multi-architecture Docker image to Docker Hub
# Supports: linux/amd64 (Intel/AMD) and linux/arm64 (Apple Silicon, Raspberry Pi 4, etc.)
#
# Usage: ./build-and-push.sh [tag]
#   tag: 'latest' (default), 'dev', or any custom tag
#
# Examples:
#   ./build-and-push.sh           # Builds and pushes with 'latest' tag
#   ./build-and-push.sh dev       # Builds and pushes with 'dev' tag
#   ./build-and-push.sh v1.2.0    # Builds and pushes with 'v1.2.0' tag
#
# Requirements:
#   - Docker with buildx support (Docker Desktop includes this)
#   - Logged in to Docker Hub as pickeld (username or pickeld@gmail.com)
#     Run: docker logout && docker login -u pickeld
#

set -e

# Configuration
DOCKER_REPO="pickeld/pick-a-recipe"
DEFAULT_TAG="latest"
PLATFORMS="linux/amd64,linux/arm64"
BUILDER_NAME="pick-a-recipe-builder"

# Get tag from argument or use default
TAG="${1:-$DEFAULT_TAG}"

# Validate tag
if [[ ! "$TAG" =~ ^[a-zA-Z0-9._-]+$ ]]; then
    echo "Error: Invalid tag format. Tags can only contain letters, numbers, dots, underscores, and hyphens."
    exit 1
fi

IMAGE_NAME="${DOCKER_REPO}:${TAG}"

echo "============================================"
echo "Building Multi-Arch Docker Image"
echo "============================================"
echo "Image:      ${IMAGE_NAME}"
echo "Platforms:  ${PLATFORMS}"
echo "============================================"

# Check if Docker is available
if ! command -v docker &> /dev/null; then
    echo "Error: Docker is not installed or not in PATH"
    exit 1
fi

# Check if buildx is available
if ! docker buildx version &> /dev/null; then
    echo "Error: Docker buildx is not available."
    echo "Please install Docker Desktop or enable buildx manually."
    exit 1
fi

# Check if logged in to Docker Hub (pickeld account — may show as email)
echo ""
echo "Checking Docker Hub login..."
DOCKER_USER="$(docker info 2>/dev/null | sed -n 's/.*Username: //p' | head -1)"
DOCKER_NAMESPACE="${DOCKER_REPO%%/*}"
if [[ -z "${DOCKER_USER}" ]]; then
    echo ""
    echo "Error: Not logged in to Docker Hub."
    echo "Run: docker logout && docker login -u pickeld   # accepts pickeld@gmail.com too"
    exit 1
fi
if [[ "${DOCKER_USER}" != "${DOCKER_NAMESPACE}" && "${DOCKER_USER}" != "pickeld@gmail.com" ]]; then
    echo ""
    echo "Error: Logged in as '${DOCKER_USER}', but pushes require the '${DOCKER_NAMESPACE}' account."
    echo "Run: docker logout && docker login -u pickeld   # accepts pickeld@gmail.com too"
    exit 1
fi
echo "Logged in as: ${DOCKER_USER}"

# Create or use buildx builder
echo ""
echo "Setting up multi-platform builder..."

# Check if our builder exists
if docker buildx inspect "${BUILDER_NAME}" &> /dev/null; then
    echo "Using existing builder: ${BUILDER_NAME}"
    docker buildx use "${BUILDER_NAME}"
else
    echo "Creating new builder: ${BUILDER_NAME}"
    docker buildx create --name "${BUILDER_NAME}" --use --bootstrap
fi

# Build and push the image for multiple platforms
echo ""
echo "Building and pushing multi-arch image..."
echo "This may take several minutes on first build..."
echo ""

docker buildx build \
    --platform "${PLATFORMS}" \
    --tag "${IMAGE_NAME}" \
    --push \
    .

if [ $? -ne 0 ]; then
    echo ""
    echo "Error: Build failed"
    exit 1
fi

echo ""
echo "============================================"
echo "✓ Successfully built and pushed!"
echo "============================================"
echo "Image: ${IMAGE_NAME}"
echo "Platforms: ${PLATFORMS}"
echo ""
echo "Pull with: docker pull ${IMAGE_NAME}"
echo "============================================"

# Show manifest to confirm multi-arch
echo ""
echo "Verifying multi-arch manifest..."
docker buildx imagetools inspect "${IMAGE_NAME}" --raw 2>/dev/null | head -20 || echo "(Manifest inspection skipped)"

# Tips
if [ "$TAG" = "latest" ]; then
    echo ""
    echo "Tip: To also push a dev tag, run: ./build-and-push.sh dev"
elif [ "$TAG" = "dev" ]; then
    echo ""
    echo "Tip: To push as latest, run: ./build-and-push.sh latest"
fi

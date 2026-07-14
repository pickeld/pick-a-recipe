#!/usr/bin/env bash
# Deploy pick-a-recipe on srv2 through Portainer (full stack control).
# Pulls pickeld/pick-a-recipe:latest; preserves legacy volume social_recipe_social-recipes.
#
# Requires Portainer credentials in portainer/stack.env.local:
#   PORTAINER_URL=https://your-portainer-host:9443
#   PORTAINER_API_KEY=ptr_...
#   FLASK_SECRET_KEY=your-secret
#
# Usage: ./scripts/portainer-migrate.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

ENV_LOCAL="portainer/stack.env.local"
if [[ ! -f "${ENV_LOCAL}" ]]; then
  echo "[migrate] ERROR: Missing ${ENV_LOCAL}" >&2
  echo "Copy portainer/stack.env and add Portainer credentials." >&2
  exit 1
fi

# Load credentials (and FLASK_SECRET_KEY) without exporting secrets to child env unnecessarily
while IFS= read -r line || [[ -n "${line}" ]]; do
  [[ -z "${line}" || "${line}" =~ ^[[:space:]]*# ]] && continue
  [[ "${line}" != *"="* ]] && continue
  key="${line%%=*}"
  value="${line#*=}"
  value="${value%%#*}"
  value="${value%"${value##*[![:space:]]}"}"
  case "${key}" in
    PORTAINER_URL|PORTAINER_API_KEY|PORTAINER_TLS_HOST|PORTAINER_ENDPOINT_ID|PORTAINER_USER|PORTAINER_PASSWORD|FLASK_SECRET_KEY)
      export "${key}=${value}"
      ;;
  esac
done < "${ENV_LOCAL}"

if [[ -z "${PORTAINER_URL:-}" ]]; then
  echo "[migrate] PORTAINER_URL not set — deploying via /data/compose stack path..."
  DEPLOY_MODE="compose"
elif [[ -z "${PORTAINER_API_KEY:-}" && ( -z "${PORTAINER_USER:-}" || -z "${PORTAINER_PASSWORD:-}" ) ]]; then
  echo "[migrate] ERROR: Set PORTAINER_API_KEY (or PORTAINER_USER/PORTAINER_PASSWORD) in ${ENV_LOCAL}" >&2
  exit 1
else
  DEPLOY_MODE="api"
fi

echo "[migrate] Migrating legacy DB and env from social_recipes stack..."
"${REPO_ROOT}/scripts/migrate-legacy-data.sh"

echo "[migrate] Pulling latest registry image..."
if ! docker pull pickeld/pick-a-recipe:latest 2>/dev/null; then
  echo "[migrate] Registry pull failed — building locally..."
  docker build -t pickeld/pick-a-recipe:latest "${REPO_ROOT}"
fi

if [[ "${DEPLOY_MODE}" == "api" ]]; then
  echo "[migrate] Deploying via Portainer API (removes external/orphan stack first)..."
  exec "${REPO_ROOT}/scripts/portainer-deploy.sh" --pull --force-recreate
fi

STACK_ID="${PORTAINER_STACK_ID:-521}"
COMPOSE_DST="/data/compose/${STACK_ID}"
PROJECT_NAME="${STACK_NAME:-pick-a-recipe}"

echo "[migrate] Syncing stack to ${COMPOSE_DST}..."
docker run --rm \
  -v /data/compose:/data/compose \
  -v "${REPO_ROOT}/portainer/stack.yml:/stack.yml:ro" \
  alpine sh -c "mkdir -p '${COMPOSE_DST}' && cp /stack.yml '${COMPOSE_DST}/docker-compose.yml'"

grep '^FLASK_SECRET_KEY=' "${ENV_LOCAL}" | docker run --rm -i \
  -v /data/compose:/data/compose \
  alpine sh -c "cat > '${COMPOSE_DST}/stack.env'"

echo "[migrate] Starting stack from Portainer compose path..."
docker compose -f "${REPO_ROOT}/portainer/stack.yml" -p "${PROJECT_NAME}" down 2>/dev/null || true
docker rm -f social-recipes 2>/dev/null || true
docker compose \
  --env-file "${ENV_LOCAL}" \
  -f "${COMPOSE_DST}/docker-compose.yml" \
  -p "${PROJECT_NAME}" \
  up -d --pull always --force-recreate

sleep 5
if curl -fsS -o /dev/null "http://127.0.0.1:5006/healthz"; then
  echo "[migrate] Done — pick-a-recipe healthy at http://$(hostname -I | awk '{print $1}'):5006"
else
  echo "[migrate] WARN: container started but health check failed — check: docker logs pick-a-recipe" >&2
  exit 1
fi

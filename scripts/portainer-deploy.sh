#!/usr/bin/env bash
# Deploy (or update) the Pick-a-Recipe stack on Portainer via REST API.
# Image: pickeld/pick-a-recipe:latest
#
# Usage:
#   # Credentials in portainer/stack.env.local (recommended):
#   ./scripts/portainer-deploy.sh --pull
#
#   # Or pass explicitly:
#   PORTAINER_URL=https://your-portainer:9443 \
#   PORTAINER_USER=admin \
#   PORTAINER_PASSWORD=secret \
#   ./scripts/portainer-deploy.sh --pull
#
# Options:
#   --pull            Pull latest image when updating
#   --force-recreate  Delete existing stack (incl. external) and recreate via Portainer
#   --dry-run         Print config and exit
#   --help            Show help

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# Bundled jq fallback
if ! command -v jq &>/dev/null; then
  JQ_BIN="${SCRIPT_DIR}/jq"
  if [[ ! -x "${JQ_BIN}" ]]; then
    echo "[portainer] Downloading jq..."
    curl -fsSL -o "${JQ_BIN}" https://github.com/jqlang/jq/releases/download/jq-1.7.1/jq-linux-amd64
    chmod +x "${JQ_BIN}"
  fi
  export PATH="${SCRIPT_DIR}:${PATH}"
fi

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
RESET='\033[0m'

info()    { echo -e "${CYAN}[portainer]${RESET} $*"; }
success() { echo -e "${GREEN}[portainer]${RESET} $*"; }
warn()    { echo -e "${YELLOW}[portainer]${RESET} $*"; }
error()   { echo -e "${RED}[portainer] ERROR:${RESET} $*" >&2; }
die()     { error "$*"; exit 1; }

PORTAINER_URL="${PORTAINER_URL:-}"
PORTAINER_USER="${PORTAINER_USER:-}"
PORTAINER_PASSWORD="${PORTAINER_PASSWORD:-}"
PORTAINER_API_KEY="${PORTAINER_API_KEY:-}"
PORTAINER_TLS_HOST="${PORTAINER_TLS_HOST:-}"
PORTAINER_ENDPOINT_ID="${PORTAINER_ENDPOINT_ID:-}"
STACK_NAME="${STACK_NAME:-pick-a-recipe}"
COMPOSE_FILE="${COMPOSE_FILE:-portainer/stack.yml}"
PULL_IMAGE=false
FORCE_RECREATE=false
DRY_RUN=false

if [[ -f "portainer/stack.env.local" ]]; then
  ENV_FILE="portainer/stack.env.local"
elif [[ -f "portainer/stack.env" ]]; then
  ENV_FILE="portainer/stack.env"
else
  ENV_FILE=""
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url)          PORTAINER_URL="$2"; shift 2 ;;
    --user)         PORTAINER_USER="$2"; shift 2 ;;
    --password)     PORTAINER_PASSWORD="$2"; shift 2 ;;
    --api-key)      PORTAINER_API_KEY="$2"; shift 2 ;;
    --tls-host)     PORTAINER_TLS_HOST="$2"; shift 2 ;;
    --endpoint-id)  PORTAINER_ENDPOINT_ID="$2"; shift 2 ;;
    --stack-name)   STACK_NAME="$2"; shift 2 ;;
    --env-file)     ENV_FILE="$2"; shift 2 ;;
    --compose)      COMPOSE_FILE="$2"; shift 2 ;;
    --pull)         PULL_IMAGE=true; shift ;;
    --force-recreate) FORCE_RECREATE=true; shift ;;
    --dry-run)      DRY_RUN=true; shift ;;
    --help|-h)
      sed -n '2,22p' "$0" | sed 's/^# \?//'
      exit 0
      ;;
    *) warn "Unknown argument: $1"; shift ;;
  esac
done

# Load credentials from stack.env.local when not passed on CLI
if [[ -n "${ENV_FILE}" && -f "${ENV_FILE}" ]]; then
  while IFS= read -r line || [[ -n "${line}" ]]; do
    [[ -z "${line}" || "${line}" =~ ^[[:space:]]*# ]] && continue
    [[ "${line}" != *"="* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    value="${value%%#*}"
    value="${value%"${value##*[![:space:]]}"}"
    case "${key}" in
      PORTAINER_URL)     [[ -z "${PORTAINER_URL}" ]] && PORTAINER_URL="${value}" ;;
      PORTAINER_USER)    [[ -z "${PORTAINER_USER}" ]] && PORTAINER_USER="${value}" ;;
      PORTAINER_PASSWORD) [[ -z "${PORTAINER_PASSWORD}" ]] && PORTAINER_PASSWORD="${value}" ;;
      PORTAINER_API_KEY) [[ -z "${PORTAINER_API_KEY}" ]] && PORTAINER_API_KEY="${value}" ;;
      PORTAINER_TLS_HOST) [[ -z "${PORTAINER_TLS_HOST}" ]] && PORTAINER_TLS_HOST="${value}" ;;
      PORTAINER_ENDPOINT_ID) [[ -z "${PORTAINER_ENDPOINT_ID}" ]] && PORTAINER_ENDPOINT_ID="${value}" ;;
    esac
  done < "${ENV_FILE}"
fi

command -v curl &>/dev/null || die "curl is required."
command -v jq   &>/dev/null || die "jq is required."
[[ -f "${COMPOSE_FILE}" ]] || die "Compose file not found: ${COMPOSE_FILE}"
[[ -n "${PORTAINER_URL}" ]] || die "PORTAINER_URL is required (set in portainer/stack.env.local)."
if [[ -z "${PORTAINER_API_KEY}" ]]; then
  [[ -n "${PORTAINER_USER}" ]] || die "PORTAINER_USER is required (or set PORTAINER_API_KEY)."
  [[ -n "${PORTAINER_PASSWORD}" ]] || die "PORTAINER_PASSWORD is required (or set PORTAINER_API_KEY)."
fi

PORTAINER_URL="${PORTAINER_URL%/}"
PORTAINER_AUTH_MODE="jwt"
[[ -n "${PORTAINER_API_KEY}" ]] && PORTAINER_AUTH_MODE="apikey"
COMPOSE_CONTENT="$(cat "${COMPOSE_FILE}")"

build_env_json() {
  local env_file="$1"
  local json="["
  local first=true
  while IFS= read -r line || [[ -n "${line}" ]]; do
    [[ -z "${line}" || "${line}" =~ ^[[:space:]]*# ]] && continue
    [[ "${line}" != *"="* ]] && continue
    local key="${line%%=*}"
    local value="${line#*=}"
    value="${value%%#*}"
    value="${value%"${value##*[![:space:]]}"}"
    [[ "${key}" == PORTAINER_* ]] && continue
    [[ "${first}" == "false" ]] && json+=","
    json+=$(printf '{"name":%s,"value":%s}' \
      "$(echo -n "${key}" | jq -Rs .)" \
      "$(echo -n "${value}" | jq -Rs .)")
    first=false
  done < "${env_file}"
  json+="]"
  echo "${json}"
}

ENV_JSON="[]"
if [[ -n "${ENV_FILE}" && -f "${ENV_FILE}" ]]; then
  ENV_JSON="$(build_env_json "${ENV_FILE}")"
fi

stop_external_containers() {
  info "Stopping containers deployed outside Portainer..."
  docker compose -f "${REPO_ROOT}/docker-compose.srv2.yml" -p social_recipes down 2>/dev/null || true
  docker compose -f "${COMPOSE_FILE}" -p "${STACK_NAME}" down 2>/dev/null || true
  docker compose -f "/data/compose/521/docker-compose.yml" -p "${STACK_NAME}" down 2>/dev/null || true
  docker rm -f social-recipes pick-a-recipe 2>/dev/null || true
}

portainer_curl_args() {
  local method="$1"
  local path="$2"
  local -n out_ref=$3
  local url_host port
  url_host="$(python3 -c 'import sys; from urllib.parse import urlparse; print(urlparse(sys.argv[1]).hostname or "")' "${PORTAINER_URL}")"
  port="$(python3 -c 'import sys; from urllib.parse import urlparse; p=urlparse(sys.argv[1]); print(p.port or (443 if p.scheme=="https" else 80))' "${PORTAINER_URL}")"
  out_ref=(-sk -X "${method}" "${PORTAINER_URL}/api${path}" -H "Content-Type: application/json")
  if [[ "${PORTAINER_AUTH_MODE}" == "apikey" ]]; then
    out_ref+=(-H "X-API-Key: ${PORTAINER_API_KEY}")
  else
    out_ref+=(-H "Authorization: Bearer ${PORTAINER_TOKEN}")
  fi
  if [[ -n "${PORTAINER_TLS_HOST}" && -n "${url_host}" ]]; then
    out_ref+=(--resolve "${PORTAINER_TLS_HOST}:${port}:${url_host}")
  fi
}

portainer_api() {
  local method="$1"
  local path="$2"
  local data="${3:-}"
  local curl_args=()
  portainer_curl_args "${method}" "${path}" curl_args
  [[ -n "${data}" ]] && curl_args+=(-d "${data}")
  curl "${curl_args[@]}"
}

info "Portainer URL : ${PORTAINER_URL}"
info "Stack name    : ${STACK_NAME}"
info "Compose file  : ${COMPOSE_FILE}"
info "Pull image    : ${PULL_IMAGE}"
info "Force recreate: ${FORCE_RECREATE}"
[[ "${DRY_RUN}" == "true" ]] && { success "Dry run — exiting."; exit 0; }

stop_external_containers

if [[ "${PORTAINER_AUTH_MODE}" == "apikey" ]]; then
  info "Authenticating with Portainer API key..."
  status_response="$(portainer_api GET "/status")"
  echo "${status_response}" | jq -e '.Version' &>/dev/null \
    || die "API key authentication failed. Check PORTAINER_URL and PORTAINER_API_KEY."
else
  info "Authenticating..."
  auth_response="$(curl -sk -X POST "${PORTAINER_URL}/api/auth" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"${PORTAINER_USER}\",\"password\":\"${PORTAINER_PASSWORD}\"}")"
  PORTAINER_TOKEN="$(echo "${auth_response}" | jq -r '.jwt // empty')"
  [[ -n "${PORTAINER_TOKEN}" ]] || die "Authentication failed. Check PORTAINER_URL/USER/PASSWORD."
fi

if [[ -z "${PORTAINER_ENDPOINT_ID}" ]]; then
  endpoints="$(portainer_api GET "/endpoints")"
  PORTAINER_ENDPOINT_ID="$(echo "${endpoints}" | jq -r '.[] | select(.Name=="srv2" or .Name=="local") | .Id' | head -1)"
  if [[ -z "${PORTAINER_ENDPOINT_ID}" ]]; then
    PORTAINER_ENDPOINT_ID="$(echo "${endpoints}" | jq -r '.[0].Id // empty')"
  fi
  [[ -n "${PORTAINER_ENDPOINT_ID}" ]] || die "No Portainer endpoints found."
  endpoint_name="$(echo "${endpoints}" | jq -r --arg id "${PORTAINER_ENDPOINT_ID}" '.[] | select(.Id==($id|tonumber)) | .Name')"
  info "Using endpoint: ${endpoint_name:-unknown} (ID ${PORTAINER_ENDPOINT_ID})"
fi

stacks="$(portainer_api GET "/stacks")"
existing_id="$(echo "${stacks}" | jq -r --arg name "${STACK_NAME}" '.[] | select(.Name == $name) | .Id' | head -1)"
existing_external="$(echo "${stacks}" | jq -r --arg name "${STACK_NAME}" '.[] | select(.Name == $name) | .Status' | head -1)"

if [[ -n "${existing_id}" && ( "${FORCE_RECREATE}" == "true" || "${existing_external}" == "2" ) ]]; then
  warn "Removing existing stack '${STACK_NAME}' (ID ${existing_id}, external=${existing_external})..."
  del_response="$(portainer_api DELETE "/stacks/${existing_id}?endpointId=${PORTAINER_ENDPOINT_ID}")"
  if echo "${del_response}" | jq -e '.message' &>/dev/null; then
    warn "Delete response: $(echo "${del_response}" | jq -r '.message')"
  fi
  existing_id=""
fi

if [[ -z "${existing_id}" ]]; then
  info "Creating stack '${STACK_NAME}' via Portainer..."
  payload="$(jq -n \
    --arg name "${STACK_NAME}" \
    --arg compose "${COMPOSE_CONTENT}" \
    --argjson env "${ENV_JSON}" \
    '{name: $name, stackFileContent: $compose, env: $env}')"
  response="$(portainer_api POST \
    "/stacks/create/standalone/string?endpointId=${PORTAINER_ENDPOINT_ID}" \
    "${payload}")"
else
  info "Updating stack '${STACK_NAME}' (ID ${existing_id})..."
  payload="$(jq -n \
    --arg compose "${COMPOSE_CONTENT}" \
    --argjson env "${ENV_JSON}" \
    --argjson pull "${PULL_IMAGE}" \
    '{stackFileContent: $compose, env: $env, pullImage: $pull, prune: false}')"
  response="$(portainer_api PUT \
    "/stacks/${existing_id}?endpointId=${PORTAINER_ENDPOINT_ID}" \
    "${payload}")"
fi

stack_id="$(echo "${response}" | jq -r '.Id // empty')"
if [[ -z "${stack_id}" ]]; then
  error "Deploy failed:"
  echo "${response}" | jq . >&2 || echo "${response}" >&2
  exit 1
fi

success "Stack '${STACK_NAME}' deployed under Portainer control (ID: ${stack_id})"
info "App URL: http://192.168.127.252:5006"

#!/usr/bin/env bash
# One-time migration: legacy SQLite + env on srv2.
# - Copies pick_a_recipe.db / social_recipes.db into pick-a-recipe.db when richer
# - Preserves FLASK_SECRET_KEY in portainer/stack.env.local from running container
#
# Usage:
#   ./scripts/migrate-legacy-data.sh
#   VOLUME=social_recipe_social-recipes ./scripts/migrate-legacy-data.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VOLUME="${VOLUME:-social_recipe_social-recipes}"
ENV_LOCAL="${REPO_ROOT}/portainer/stack.env.local"

echo "[migrate-data] Volume: ${VOLUME}"

docker run --rm -i -v "${VOLUME}:/data" python:3.11-slim python3 - <<'PY'
import os
import shutil
import sqlite3

DATA_DIR = '/data'
DB_FILE = os.path.join(DATA_DIR, 'pick-a-recipe.db')
LEGACY = ('pick_a_recipe.db', 'social_recipes.db')

def score(path):
    if not os.path.exists(path):
        return 0
    s = 0
    conn = sqlite3.connect(path)
    for table, w in (('recipe_history', 10), ('recipe_jobs', 1), ('pending_uploads', 5), ('config', 1)):
        try:
            s += conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] * w
        except sqlite3.OperationalError:
            pass
    conn.close()
    return s

canonical = score(DB_FILE)
best, best_score = None, canonical
for name in LEGACY:
    p = os.path.join(DATA_DIR, name)
    sc = score(p)
    print(f'  {name}: score={sc}')
    if sc > best_score:
        best, best_score = p, sc

print(f'  pick-a-recipe.db: score={canonical}')

if not best:
    print('[migrate-data] Canonical DB already has the data — nothing to do.')
    raise SystemExit(0)

bak = f'{DB_FILE}.pre-migration.bak'
if os.path.exists(DB_FILE) and not os.path.exists(bak):
    shutil.copy2(DB_FILE, bak)
    print(f'[migrate-data] Backed up empty canonical DB to {os.path.basename(bak)}')

shutil.copy2(best, DB_FILE)
print(f'[migrate-data] Copied {os.path.basename(best)} -> pick-a-recipe.db')

conn = sqlite3.connect(DB_FILE)
for table in ('users', 'config', 'recipe_history', 'recipe_jobs'):
    try:
        n = conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
        print(f'  {table}: {n}')
    except sqlite3.OperationalError:
        pass
conn.close()
PY

# Sync FLASK_SECRET_KEY from running container if stack.env.local is missing or default
if docker ps --format '{{.Names}}' | grep -qx 'pick-a-recipe'; then
  secret="$(docker inspect pick-a-recipe --format '{{range .Config.Env}}{{println .}}{{end}}' \
    | sed -n 's/^FLASK_SECRET_KEY=//p' | head -1)"
  if [[ -n "${secret}" ]]; then
    mkdir -p "$(dirname "${ENV_LOCAL}")"
    if [[ ! -f "${ENV_LOCAL}" ]] || grep -q '^FLASK_SECRET_KEY=change-me' "${ENV_LOCAL}" 2>/dev/null; then
      {
        echo "FLASK_SECRET_KEY=${secret}"
        if [[ -f "${ENV_LOCAL}" ]]; then
          grep -v '^FLASK_SECRET_KEY=' "${ENV_LOCAL}" || true
        fi
      } > "${ENV_LOCAL}.tmp"
      mv "${ENV_LOCAL}.tmp" "${ENV_LOCAL}"
      chmod 600 "${ENV_LOCAL}"
      echo "[migrate-data] Updated FLASK_SECRET_KEY in portainer/stack.env.local"
    fi
  fi
fi

if docker ps --format '{{.Names}}' | grep -qx 'pick-a-recipe'; then
  echo "[migrate-data] Restarting pick-a-recipe to load migrated DB..."
  docker restart pick-a-recipe >/dev/null
  echo "[migrate-data] Done. Check http://$(hostname -I | awk '{print $1}'):5006"
else
  echo "[migrate-data] Done (container not running — start stack to apply)."
fi

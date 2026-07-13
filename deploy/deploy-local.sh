#!/usr/bin/env bash
set -euo pipefail

# Local deploy — for the colocated box where the devbox IS prod (everything in
# one /opt/praive runtime + one Caddy, no remote SSH hop). Same colocated-deploy
# contract as praive/kompetansegapet: skytilsynet is a static site served by Caddy
# from /opt/praive/skytilsynet-dist (/srv/skytilsynet) — no build step. Plus a small
# FOI intake backend (compose service skytilsynet-foi, #54). Swap the static files,
# sync + restart the backend, reload Caddy.
#
#   DRY_RUN=1 deploy/deploy-local.sh   # print actions, change nothing

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$SCRIPT_DIR")"
RUNTIME=/opt/praive
DRY="${DRY_RUN:-}"

run()  { if [ -n "$DRY" ]; then echo "DRY: $*"; else eval "$@"; fi; }
step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

[ -f "$RUNTIME/docker-compose.yml" ] || {
  echo "No $RUNTIME/docker-compose.yml — this is not the prod box." >&2; exit 1; }
[ -f "$ROOT/web/index.html" ] || {
  echo "web/index.html missing — nothing to deploy." >&2; exit 1; }

step "Swap skytilsynet static site into $RUNTIME/skytilsynet-dist"
run "sudo mkdir -p '$RUNTIME/skytilsynet-dist'"
run "sudo rsync -a --delete '$ROOT/web/' '$RUNTIME/skytilsynet-dist/'"

# FOI intake backend (#54): a small container (compose service skytilsynet-foi)
# serves POST /api/foi and stores answers for manual review. Sync the app code +
# the domain whitelist (data/*.latest.json, read at startup) into the mounted app
# dir, then restart the service so it picks up new code + a fresh whitelist. The
# SQLite DB lives in a docker volume, NOT here, so it survives this sync untouched.
if [ -d "$ROOT/server" ]; then
  step "Sync FOI intake backend + restart the service"
  run "sudo mkdir -p '$RUNTIME/skytilsynet-app'"
  run "sudo rsync -a --delete '$ROOT/server/' '$RUNTIME/skytilsynet-app/server/'"
  # The backend adds the repo root to sys.path and imports the top-level shared
  # package (server/foi_intake.py: `from shared.csv_safe import csv_safe`), so it
  # must ship alongside server/ or the container crashes at import on restart (#110).
  run "sudo rsync -a --delete '$ROOT/shared/' '$RUNTIME/skytilsynet-app/shared/'"
  run "sudo rsync -a '$ROOT/data/' '$RUNTIME/skytilsynet-app/data/'"
  run "sudo rsync -a '$ROOT/scripts/' '$RUNTIME/skytilsynet-app/scripts/'"
  run "sudo rm -f '$RUNTIME/skytilsynet-app/server/data/foi_submissions.db'"
  run "sudo docker compose -f '$RUNTIME/docker-compose.yml' restart skytilsynet-foi"
fi

step "Reload Caddy"
run "sudo docker compose -f '$RUNTIME/docker-compose.yml' exec -T caddy caddy reload --config /etc/caddy/Caddyfile"

printf '\n\033[1;32m==> deploy-local: DONE\033[0m  → https://skytilsynet.no\n'

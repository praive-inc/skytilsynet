#!/usr/bin/env bash
set -euo pipefail

# Local deploy — for the colocated box where the devbox IS prod (everything in
# one /opt/praive runtime + one Caddy, no remote SSH hop). Same colocated-deploy
# contract as praive/kompetansegapet, simplest variant: skytilsynet is a static
# site served by Caddy from /opt/praive/skytilsynet-dist (/srv/skytilsynet) — no
# build step, no backend container. Just swap the static files + reload Caddy.
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

step "Reload Caddy"
run "sudo docker compose -f '$RUNTIME/docker-compose.yml' exec -T caddy caddy reload --config /etc/caddy/Caddyfile"

printf '\n\033[1;32m==> deploy-local: DONE\033[0m  → https://skytilsynet.no\n'

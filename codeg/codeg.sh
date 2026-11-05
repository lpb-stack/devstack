#!/usr/bin/env bash
# codeg.sh — control the codeg container + goose-as-ACP wiring (strixite).
#
#   ./codeg.sh start              create-or-start the container (env from codeg.env)
#   ./codeg.sh stop | restart    plain docker lifecycle
#   ./codeg.sh patch             apply the vision-capable catalog entry (idempotent)
#   ./codeg.sh reload            patch + kill goose-acp children so they respawn
#                                with the current catalog (no container restart)
#   ./codeg.sh recreate          rm + run again with the CURRENT codeg.env (needed
#                                after any env change), then patch
#   ./codeg.sh verify            what the running goose can actually see (catalog entry)
#   ./codeg.sh status            container state, goose processes, catalog summary
#   ./codeg.sh logs              tail codeg server log, goose-relevant lines
#
# Why reload matters: goose-acp loads the models.dev catalog when the agent
# process starts and caches it in memory; and any upstream etag change
# re-downloads the catalog, wiping local patches. `reload` re-patches and
# respawns the agent. After CONTAINER recreation, /root/.local/share is empty
# again (only /data is a volume) — `patch` then waits for goose to fetch once
# and re-applies from the host cache in codeg/.catalog-cache/.
#
# Root-cause history: README.md and devstack#18.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
NAME=${CODEG_NAME:-codeg}
IMAGE="${CODEG_IMAGE:-ghcr.io/spacering-net/codeg:latest}"
PORT="${CODEG_PORT:-3080}"
VOL="${CODEG_VOLUME:-codeg-data}"
ENV_FILE="${CODEG_GOOSE_ENV:-$HERE/codeg.env}"
# Host dir exposed at the SAME path inside the container (goose validates that
# the session/new cwd exists IN ITS OWN filesystem — host paths must be mounted
# verbatim or every new session dies with Invalid params: "invalid directory
# path"; the codeg UI's "turn off MCP support" hint is a false lead).
# Override with CODEG_MOUNT=/some/path, disable with CODEG_MOUNT="".
MOUNT="${CODEG_MOUNT:-$HOME/workspace}"
MODEL_ID=${MODEL_ID:-qwen3.8-flash-next}
CTX=${CTX:-524288}
MAXOUT=${MAXOUT:-16384}
CAT=/root/.local/share/goose/model_catalog/models_dev_api.json
CACHE_DIR="$HERE/.catalog-cache"
CACHE="$CACHE_DIR/models_dev_api.patched.json"

TMP_FILES=()
cleanup() { [[ ${#TMP_FILES[@]} -gt 0 ]] && rm -f "${TMP_FILES[@]}"; true; }
trap cleanup EXIT

mktmp() { local t; t=$(mktemp); TMP_FILES+=("$t"); echo "$t"; }

is_running() { docker ps --format '{{.Names}}' | grep -qx -- "$NAME"; }
is_present()  { docker ps -a --format '{{.Names}}' | grep -qx -- "$NAME"; }
c() { docker exec "$NAME" "$@"; }
die() { echo ">> $*" >&2; exit 1; }

# ---------- create / lifecycle -------------------------------------------------

cmd_run() {  # fresh create with goose env wired in
  local envsrc; envsrc="$(mktmp)"
  if [[ -f "$ENV_FILE" ]]; then
    cp "$ENV_FILE" "$envsrc"
    echo ">> goose env: $ENV_FILE"
  else
    echo ">> goose env: built-in defaults (create $ENV_FILE to override)"
    cat >"$envsrc" <<DEFAULTS
GOOSE_PROVIDER=openai
GOOSE_MODEL=$MODEL_ID
OPENAI_HOST=http://192.168.0.13:8000
OPENAI_BASE_PATH=v1/chat/completions
GOOSE_CONTEXT_LIMIT=$CTX
GOOSE_MAX_TOKENS=$MAXOUT
GOOSE_AUTO_COMPACT_TOKEN_LIMIT=200000
GOOSE_CONTEXT_STRATEGY=summarize
GOOSE_MODE=auto
GOOSE_TELEMETRY_ENABLED=false
DEFAULTS
  fi
  docker rm -f awesome_agnesi >/dev/null 2>&1 || true   # legacy unnamed first run
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  local mounts=( -v "${VOL}:/data" )
  if [[ -n "$MOUNT" ]]; then
    if [[ -d "$MOUNT" ]]; then
      mounts+=( -v "$MOUNT:$MOUNT" )
      echo ">> workspace mount: $MOUNT (same path in container)"
    else
      echo ">> WARN: CODEG_MOUNT=$MOUNT does not exist — skipping mount"
    fi
  fi
  docker run -d --name "$NAME" -p "${PORT}:3080" "${mounts[@]}" \
    --env-file "$envsrc" "$IMAGE"
  echo ">> waiting for UI on :${PORT}..."
  # Host-mounted repos carry the host uid; the root-running agent inside trips
  # git's ownership check. Silence it per create (container fs outside /data is
  # ephemeral, so this lives in the lifecycle, not in a one-off exec).
  docker exec "$NAME" git config --global --add safe.directory '*' 2>/dev/null || true
  local i
  for i in $(seq 1 20); do
    sleep 1
    if curl -fs -m 2 "http://localhost:${PORT}/" -o /dev/null 2>/dev/null; then
      echo ">> up: http://localhost:${PORT}"; return 0
    fi
  done
  echo ">> WARN: UI not answering yet — check: $0 logs"
}

cmd_start() {
  if is_running; then echo ">> already running"; return 0; fi
  if is_present; then docker start "$NAME" >/dev/null; echo ">> started existing container";
  else cmd_run; fi
}

cmd_stop()    { docker stop "$NAME" >/dev/null && echo ">> stopped"; }
cmd_restart() { docker restart "$NAME" >/dev/null && echo ">> restarted (goose children respawn, catalog re-read)"; }

# ---------- catalog patch ------------------------------------------------------

# ---- catalog plumbing (all host-side; goose's own fetch is only one source) ----

CATALOG_URL=${CATALOG_URL:-https://models.dev/api.json}

push_catalog() {  # $1 = local patched file -> container
  c mkdir -p "${CAT%/*}"
  docker cp "$1" "$NAME:/tmp/catalog.new" && c sh -c "mv /tmp/catalog.new '$CAT'"
}

seed_catalog() {  # break the bootstrap deadlock: no session has run yet, so the
                  # container has no catalog and we fetch the upstream one host-side
  local raw; raw="$(mktmp)"
  echo ">> seeding catalog from $CATALOG_URL"
  curl -fsSL -m 30 "$CATALOG_URL" -o "$raw" || die "cannot fetch catalog on host"
  patch_file "$raw"
  push_catalog "$CACHE"
}

patch_file() {  # $1 = local catalog file, patched in place + mirrored to $CACHE
  python3 - "$1" "$MODEL_ID" "$CTX" "$MAXOUT" <<'EOF'
import json, sys, os
p, mid, ctx, mo = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
d = json.load(open(p))
models = d.setdefault("openai", {}).setdefault("models", {})
entry = {"id": mid, "name": "Qwen3.8 Flash Next (strixite)",
  "description": "Local strixite model, vision-capable",
  "family": "qwen", "attachment": True, "reasoning": False,
  "tool_call": True, "structured_output": True, "temperature": True,
  "modalities": {"input": ["text", "image"], "output": ["text"]},
  "open_weights": True, "limit": {"context": ctx, "output": mo},
  "cost": {"input": 0.0, "output": 0.0}}
if models.get(mid) == entry:
    print(">> already patched"); sys.exit(0)
models[mid] = entry
tmp = p + ".new"; json.dump(d, open(tmp, "w"), separators=(",", ":")); os.replace(tmp, p)
print(">> patched catalog with vision-capable entry")
EOF
  mkdir -p "$CACHE_DIR"; cp "$1" "$CACHE"     # host copy survives recreation
}

patch_catalog() {  # patch the catalog already present in the container
  local tmp; tmp="$(mktmp)"
  if ! docker cp "$NAME:$CAT" "$tmp" 2>/dev/null; then return 1; fi
  patch_file "$tmp"
  push_catalog "$tmp"
}

cmd_patch() {
  is_running || die "container '$NAME' not running — $0 start first"
  if c test -s "$CAT"; then
    patch_catalog
  elif [[ -s "$CACHE" ]]; then
    echo ">> pushing host-cached catalog"
    push_catalog "$CACHE"
  else
    seed_catalog
  fi
  echo ">> run '$0 reload' if the agent is already running (it caches the catalog)"
}

cmd_reload() {
  is_running || die "not running"
  if [[ -s "$CACHE" ]] && ! c test -s "$CAT"; then
    echo ">> restoring host-cached catalog"
    docker cp "$CACHE" "$NAME:/tmp/catalog.new" && c sh -c "mv /tmp/catalog.new '$CAT'"
  fi
  cmd_patch
  echo ">> respawning goose agent(s)..."
  c sh -c 'for d in /proc/[0-9]*; do
             exe=$(readlink "$d/exe" 2>/dev/null) || continue
             case "$exe" in *goose*) kill "${d#/proc/}" 2>/dev/null && echo "   killed ${d#/proc/}";; esac
           done; true'
  cmd_verify || true
  echo ">> done — reconnect the agent in codeg if it shows disconnected"
}

cmd_recreate() { cmd_run; cmd_patch || true; }

# ---------- inspection ----------------------------------------------------------

cmd_verify() {
  is_running || die "not running"
  echo -n ">> catalog entry for $MODEL_ID: "
  c node -e "
    const fs=require('fs');
    try{
      const d=JSON.parse(fs.readFileSync('$CAT','utf8'));
      const e=((d.openai||{}).models||{})['$MODEL_ID'];
      if(!e){ console.log('MISSING — goose treats it as text-only/non-thinking'); process.exit(1); }
      console.log('present | attachment='+e.attachment+' | reasoning='+e.reasoning+
                  ' | input='+JSON.stringify((e.modalities||{}).input));
    }catch(err){ console.log('unreadable: '+err.message); process.exit(1); }
  "
}

cmd_status() {
  docker ps -a --filter "name=^${NAME}$" --format 'container: {{.Status}} | image: {{.Image}}'
  if is_running; then
    echo -n "goose children: "
    c sh -c 'found=0; for d in /proc/[0-9]*; do
               exe=$(readlink "$d/exe" 2>/dev/null) || continue
               case "$exe" in *goose*) echo -n "${d#/proc/} "; found=1;; esac
             done; [[ $found == 0 ]] && echo -n "(none)"; echo'
    cmd_verify || true
  fi
  if [[ -s "$CACHE" ]]; then echo "host catalog cache: present ($CACHE)"; else echo "host catalog cache: none"; fi
}

cmd_logs() {
  c sh -c "tail -n 200 /data/logs/codeg-server.\$(date +%F).log 2>/dev/null | grep -Ei 'goose|acp|provider|error' | tail -30"
}

# 'invalid directory path' forensics: show which cwd recent sessions asked for
# and whether it actually exists inside the container.
cmd_cwdcheck() {
  is_running || die "not running"
  local raw; raw="$(mktmp)"
  c sh -c "cat /data/logs/codeg-server.\$(date +%F).log 2>/dev/null" > "$raw" || true
  grep -E 'working_dir|invalid directory path' "$raw" | tail -15
  echo "--- distinct working_dir values and their existence in the container ---"
  python3 - "$raw" | sort -u | while read -r d; do
    if c test -d "$d"; then echo "  OK       $d"; else echo "  MISSING  $d"; fi
  done <<PYEOF
import json, sys
seen = set()
for line in open(sys.argv[1] if len(sys.argv) > 1 else 0, errors="replace"):
    if '"working_dir"' not in line: continue
    try: m = json.loads(line)
    except Exception: continue
    w = (m.get("span") or {}).get("working_dir") or \
        (m.get("spans") or [{}])[0].get("working_dir", "")
    # value is the Rust Debug format: Some("/root/test")
    if w.startswith('Some("') and w.endswith('")'):
        w = w[6:-2]
    else:
        continue
    if w.startswith("/") and w not in seen:
        seen.add(w); print(w)
PYEOF
}

case "${1:-}" in
  start)    cmd_start;;
  stop)     cmd_stop;;
  restart)  cmd_restart;;
  patch)    cmd_patch;;
  reload)   cmd_reload;;
  recreate) cmd_recreate;;
  verify)   cmd_verify;;
  status)   cmd_status;;
  logs)     cmd_logs;;
  cwdcheck) cmd_cwdcheck;;
  run)      cmd_run;;
  *) cat <<EOF
usage: $0 {start|stop|restart|patch|reload|recreate|verify|status|logs|cwdcheck}
  reload    — after catalog changes (patches + respawns goose agent, no restart)
  recreate  — after codeg.env changes (container is rebuilt, env is baked at create)
              NOTE: recreates now bind-mount \$CODEG_MOUNT (default \$HOME/workspace)
              at the same path — goose requires the session cwd to exist in-container
  cwdcheck  — diagnose 'invalid directory path': which cwd sessions asked for, does it exist
EOF
  ;;
esac

#!/usr/bin/env bash
# goose-as-ACP diagnostics for the codeg container — run on slaptop host
# (devstack repo: codeg/goose-codeg-diag.sh)
# Usage: bash goose-codeg-diag.sh   (or: chmod +x and ./goose-codeg-diag.sh)
# Read-only: inspects, never changes anything.

NAME=${1:-$(docker ps --filter ancestor=ghcr.io/spacering-net/codeg:latest --format "{{.Names}}" | head -1)}

echo '================ 1) containers (is codeg up? did it spawn siblings?) ================'
docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}'

echo
echo '================ 2) codeg container: runtime mode, user, HOME ================'
docker exec "$NAME" sh -c 'echo HOME=$HOME; id; printenv | grep -Ei "codeg_runtime|codeg_|goose|openai" | sort'

echo
echo '================ 3) goose binary inside the codeg container? ================'
docker exec "$NAME" sh -c 'which goose goose-acp; goose --version 2>&1 | head -1' || echo '>> goose NOT present in codeg container'

echo
echo '================ 4) model server reachable FROM the container? ================'
docker exec "$NAME" sh -c 'curl -s -m5 http://192.168.0.13:8000/health | head -c 200 || wget -qO- -T5 http://192.168.0.13:8000/health | head -c 200' || echo '>> NOT reachable from container'
echo

echo
echo '================ 5) docker socket visible inside codeg? (CODEG_RUNTIME=docker needs it) ================'
docker exec "$NAME" sh -c 'ls -la /var/run/docker.sock 2>&1; docker ps 2>&1 | head -3' || true

echo
echo '================ 6) codeg persisted state: agent definitions mentioning goose ================'
docker exec "$NAME" sh -c 'ls -la /data; echo ---; find /data -maxdepth 3 -type f 2>/dev/null | head -40'
echo ---
docker exec "$NAME" sh -c 'grep -ril goose /data 2>/dev/null | head -20' || true

echo
echo '================ 7) any codeg-spawned agent/sandbox images on the host ================'
docker images --format '{{.Repository}}:{{.Tag}}\t{{.Size}}' | grep -Ei 'codeg|goose|agent|acp' || echo '>> no obvious agent sandbox images'

echo
echo '================ 8) host-side goose state (for reference / path B bind-mounts) ================'
which goose goose-acp 2>/dev/null; goose --version 2>/dev/null | head -1
echo '--- host ~/.config/goose/config.yaml:'
cat ~/.config/goose/config.yaml 2>/dev/null || echo '>> no host config file'

echo
echo 'Done. Paste the whole output back to the Pi session.'

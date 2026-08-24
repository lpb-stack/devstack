#!/usr/bin/env bash
# Installer for lpb (LocalPibox Devstack launcher)
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/lpb-stack/devstack/main/scripts/install.sh | bash
#
# Best practice: download first, inspect, then run
#   curl -fsSL https://raw.githubusercontent.com/lpb-stack/devstack/main/scripts/install.sh -o /tmp/install.sh
#   cat /tmp/install.sh
#   bash /tmp/install.sh

set -euo pipefail

INSTALL_DIR="${HOME}/.local/bin"
CONFIG_DIR="${HOME}/.lpb-stack/devstack"
CONFIG_FILE="${CONFIG_DIR}/config"
# Source branch: main by default. Set LPB_BRANCH=dev to install the dev
# pipeline code (e.g. the unified setup wizard) so the launcher matches
# *-dev images:  LPB_BRANCH=dev bash install.sh
OWNER_REPO="lpb-stack/devstack"
BRANCH="${LPB_BRANCH:-main}"
SCRIPT_URL="https://raw.githubusercontent.com/${OWNER_REPO}/${BRANCH}/scripts/lpb"

# 1. Validate prerequisites
for dep in curl; do
    command -v "$dep" >/dev/null 2>&1 || { echo "ERROR: $dep is required"; exit 1; }
done
command -v podman 2>/dev/null || command -v docker 2>/dev/null || \
    { echo "WARNING: Neither podman nor docker found — lpb will need one to run"; }

# 2. Create directories
mkdir -p "${INSTALL_DIR}" "${CONFIG_DIR}"

# 3. Download and install script + engine
echo "Installing lpb..."
curl -fsSL "${SCRIPT_URL}" -o "${INSTALL_DIR}/lpb"
chmod +x "${INSTALL_DIR}/lpb"
# The shell wrapper execs lpb.py from the same directory.
curl -fsSL "https://raw.githubusercontent.com/${OWNER_REPO}/${BRANCH}/scripts/lpb.py" -o "${INSTALL_DIR}/lpb.py"
chmod +x "${INSTALL_DIR}/lpb.py"
# Install the canonical env defaults alongside so a plain install picks up
# stack/repo defaults (a fork's lpb.py loads these via CONFIG_DIR fallback).
curl -fsSL "https://raw.githubusercontent.com/${OWNER_REPO}/${BRANCH}/lpb.stack.env" -o "${CONFIG_DIR}/lpb.stack.env"
curl -fsSL "https://raw.githubusercontent.com/${OWNER_REPO}/${BRANCH}/lpb.conf.env" -o "${CONFIG_DIR}/lpb.conf.env"
# Installed VERSION file (source for `lpb --version`; refreshed by self-update)
curl -fsSL "https://raw.githubusercontent.com/${OWNER_REPO}/${BRANCH}/VERSION" -o "${CONFIG_DIR}/VERSION"

# 3b. Stack tools (config repo manager + devstack DevOps tool) and the shared
#     localpibox package they import (resolved via ~/.lpb-stack/devstack/).
echo "Installing lpb-config + lpb-devstack..."
for tool in lpb-config lpb-devstack; do
    curl -fsSL "https://raw.githubusercontent.com/${OWNER_REPO}/${BRANCH}/scripts/${tool}" -o "${INSTALL_DIR}/${tool}"
    chmod +x "${INSTALL_DIR}/${tool}"
done
mkdir -p "${CONFIG_DIR}/localpibox/stack"
for f in __init__.py cli.py env.py log.py run.py setup.py; do
    curl -fsSL "https://raw.githubusercontent.com/${OWNER_REPO}/${BRANCH}/scripts/localpibox/${f}" -o "${CONFIG_DIR}/localpibox/${f}"
done
for f in __init__.py gitutil.py repos.py version.py workspace.py validate.py release.py; do
    curl -fsSL "https://raw.githubusercontent.com/${OWNER_REPO}/${BRANCH}/scripts/localpibox/stack/${f}" -o "${CONFIG_DIR}/localpibox/stack/${f}"
done

# 4. Add to PATH if needed (warn only, don't modify shell configs)
case ":${PATH}:" in
    *:"${INSTALL_DIR}":*)
        echo "  ${INSTALL_DIR} is already in PATH" ;;
    *)
        echo "  ${INSTALL_DIR} is NOT in PATH — add it:"
        echo "    export PATH=\"${INSTALL_DIR}:\${PATH}\""
        echo "  Or add to ~/.bashrc / ~/.zshrc" ;;
esac

# 5. Create default config
if [ ! -f "${CONFIG_FILE}" ]; then
    cat > "${CONFIG_FILE}" <<'EOF'
# ─── Devstack global config ───────────────────────────────────────────────
# Edit this file to override defaults. Values are in the form:
#   export VAR_NAME="value"
#
# Stack/build identity (image names, forks, versions) and runtime defaults
# are defined in lpb.stack.env / lpb.conf.env (installed next to this file).
# Override any of them here — these take priority.
#
# Project-specific overrides go in:
#   ~/.lpb-stack/devstack/projects/<project-name>

# ─── Container ──────────────────────────────────────────────────────────────
export LPB_CONTAINER_NAME="lpb-stack"

# ─── VSCodium ───────────────────────────────────────────────────────────────
export LPB_EDITOR_HOST="localhost"
# Connection token — leave empty to auto-generate per session (recommended).
export LPB_CONNECTION_TOKEN=""

# ─── Directories ────────────────────────────────────────────────────────────
export LPB_STATE_DIR="${HOME}/.lpb-stack/state"
export LPB_BROWSER_DIR="${HOME}/.lpb-stack/agent-browser"
EOF
    echo "  Created config: ${CONFIG_FILE}"
fi

echo ""
echo "✓ lpb installed to ${INSTALL_DIR}/lpb"
echo "✓ lpb-config + lpb-devstack installed to ${INSTALL_DIR}/"
echo ""
echo "Usage examples:"
echo "  lpb                              — Start VSCodium at ~ (pick project)"
echo "  lpb /path/to/project              — Start VSCodium at project"
echo "  lpb /path/to/project --port 8080  — Custom port"
echo "  lpb --without-token               — No auth (localhost only!)"
echo "  lpb --stop                        — Stop container"
echo "  lpb --logs                        — View container logs"
echo "  lpb --remove                      — Remove everything"
echo ""
echo "Stack tools:"
echo "  lpb setup          — Initial setup wizard (server, key, model, memory)"
echo "  lpb doctor         — Validate the installation"
echo "  lpb-config status | update | reset | memory setup   — config repo (container/host)"
echo "  lpb-devstack bump | tag-repos | workspace | validate | release — DevOps"
echo ""
echo "Config: ${CONFIG_FILE}"
echo "Docs:   lpb --help"

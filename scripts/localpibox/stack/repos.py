"""LocalPibox stack constants and repository definitions.

Single source of truth for the workspace layout and the 6-repo stack map.
Everything else (tagging list, extension list, release repo list, expected
branches) is derived from the tables below. Also the one-time legacy
~/.pi layout migration.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from ..log import Console

# ─── Path constants ─────────────────────────────────────────────────────────

# Devstack repo root (this file lives in <devstack>/scripts/localpibox/stack/).
# In the Docker image this resolves to /opt — harmless, the version-file
# candidates fall through to /opt/devstack and the workspace.
_DEVSTACK_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_AGENT_DIR = os.environ.get("AGENT_DIR", "/home/lpb/.pi/agent")
DEFAULT_REMOTE = os.environ.get("CONFIG_REMOTE", "https://github.com/lpb-stack/config.git")
DEFAULT_REF = os.environ.get("CONFIG_REF", "main")

WORKSPACE_ROOT = Path(os.environ.get("LPB_WORKSPACE_ROOT", "/home/lpb/workspace"))
AGENT_GIT = Path(os.environ.get(
    "LPB_AGENT_GIT", f"{DEFAULT_AGENT_DIR}/git/github.com/lpb-stack"))

MIGRATE_KEEP = {".initialized", "ssh-host-keys", "gh-config", "agent"}

# Canonical stack VERSION format (0.x.y-lpb[-dev]) — must match the
# devstack pre-commit hook.
VERSION_RE = re.compile(r"^0\.[0-9]+\.[0-9]+-lpb(-dev)?$")

# ─── Workspace repo definitions ───────────────────────────────────────────
# Each repo: (name, is_symlink, is_extension, dev_branch, main_branch)
#   is_symlink: workspace repo is a symlink → .pi/agent/git/...
#   is_extension: repo is installed as a Pi extension
#   dev_branch / main_branch: expected branch for each pipeline

WORKSPACE_REPOS = [
    # (name, is_symlink, is_extension, dev_branch, main_branch)
    ("devstack",          False, False, "dev",    "main"),
    ("lemonade-pi-plugin", True,  True,  "lpb-dev", "lpb"),
    ("lpb-memory",        True,  True,  "dev",    "main"),
    ("pi-subagents",      True,  True,  "lpb-dev", "lpb"),
    # pi is no longer a stack repo (de-forked 2026-08-31 — the lpb-stack/pi
    # fork is retired; the image installs mainstream pi from the npm
    # registry at LPB_PI_VERSION). A local workspace/pi clone may remain as
    # a reference only (retired state: tag pre-defork-0.0.71).
]

# The config repo lives in the agent dir (DEFAULT_AGENT_DIR) instead of the
# workspace root and is installed by lpb-config (not a plain git clone).
# (name, dev_branch, main_branch)
CONFIG_REPO = ("config", "dev", "main")

# Stack repos that fork an upstream project, mapped to its canonical git URL.
# `lpb-devstack workspace sync` ensures an `upstream` remote exists for these
# (added on missing, repaired on drift) and fetches it, so upstream updates
# are visible without manual remote setup.
UPSTREAM_REMOTES = {
    "pi-subagents": "https://github.com/tintinweb/pi-subagents.git",
    "lemonade-pi-plugin": "https://github.com/lemonade-sdk/lemonade-pi-plugin.git",
}


def stack_repos() -> list[tuple[str, str, str]]:
    """All 6 stack repos: (name, dev_branch, main_branch) — single source of truth."""
    return [(n, d, m) for (n, _sym, _ext, d, m) in WORKSPACE_REPOS] + [CONFIG_REPO]


# Repos tagged per release — the 5 stack repos excluding devstack (devstack
# is tracked by its VERSION file, never tagged). Mirrors the CI tag-repos job.
TAG_REPOS = [
    (n, d, m) for (n, _sym, _ext, d, m) in WORKSPACE_REPOS if n != "devstack"
] + [CONFIG_REPO]

# Pi extension repos (settings.json pin targets).
LPB_EXTENSION_REPOS = [n for (n, _sym, is_ext, _d, _m) in WORKSPACE_REPOS if is_ext]

# Extensions installed from an upstream npm package instead of the fork
# clone (fork retired). Pin versions follow upstream releases, NOT the
# stack VERSION — pin sync leaves them alone and validate treats them as
# a presence-only check.
NPM_EXTENSION_PACKAGES = {
    "pi-subagents": "@tintinweb/pi-subagents",
}


def repo_path(name: str) -> Path:
    """Local path where stack repo *name* lives (workspace root or agent dir)."""
    if name == CONFIG_REPO[0]:
        return Path(DEFAULT_AGENT_DIR)
    return WORKSPACE_ROOT / name

MEMORY_CONFIG_PATH = Path(DEFAULT_AGENT_DIR) / "lpb-memory-config.json"
MEMORY_CONFIG_TEMPLATE = Path(DEFAULT_AGENT_DIR) / "lpb-memory-config.json.template"


def _repo_remote(name: str) -> str:
    """GitHub remote URL for a stack repo (LPB_STACK_REMOTE_BASE override for tests/offline)."""
    base = os.environ.get("LPB_STACK_REMOTE_BASE", "https://github.com/lpb-stack").rstrip("/")
    return f"{base}/{name}.git"


def migrate_legacy_layout(pi_root: str | Path, agent_dir: str | Path, cons: Console) -> None:
    """Move legacy ``~/.pi`` root layout contents into ``~/.pi/agent/`` (one-time)."""
    pi_root, agent_dir = Path(pi_root), Path(agent_dir)
    if (pi_root / ".git").is_dir() and not (agent_dir / ".git").is_dir():
        cons.info(f"Migrating legacy config layout from {pi_root} to {agent_dir} ...")
        agent_dir.mkdir(parents=True, exist_ok=True)
        for item in pi_root.iterdir():
            if item.name in MIGRATE_KEEP:
                continue
            try:
                shutil.move(str(item), str(agent_dir / item.name))
            except OSError:
                pass
        cons.info(f"Legacy config layout migrated to {agent_dir}.")

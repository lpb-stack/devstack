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
import sys
from pathlib import Path
from typing import TextIO

from ..cli import select_one
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

# ─── Devstack checkout discovery ──────────────────────────────────────────
# The devstack repo is identified by what it CONTAINS, never by the name of
# its directory: a checkout (or git worktree) may live anywhere under the
# workspace root — worktree managers routinely give it a random name, e.g.
# ~/workspace/molly. Matching on the name only made `workspace sync` treat the
# running checkout as "missing" and try to clone a second devstack beside it.
DEVSTACK_REPO = "devstack"
_DEVSTACK_SIGNATURE = Path("scripts/localpibox/stack/workspace.py")

_devstack_override: Path | None = None  # set by --devstack-dir / LPB_DEVSTACK_DIR


def set_devstack_dir(path: str | Path | None) -> None:
    """Pin the devstack checkout (CLI --devstack-dir); None clears the pin."""
    global _devstack_override
    _devstack_override = Path(path).expanduser() if path else None


def is_devstack_checkout(path: Path) -> bool:
    """True when *path* holds a devstack checkout (content, not directory name)."""
    return ((path / "VERSION").is_file()
            and (path / _DEVSTACK_SIGNATURE).is_file())


def devstack_dirs(workspace_root: Path | None = None) -> list[Path]:
    """Every devstack checkout visible, best candidate first (deduped).

    Order: explicit pin (--devstack-dir / LPB_DEVSTACK_DIR), the checkout this
    code runs from (when it is the workspace one), the conventional
    $WORKSPACE_ROOT/devstack, then any other directory under the workspace
    root. With nothing in the workspace at all, the checkout this code runs
    from is still the answer — that is the host install (~/.lpb-stack/devstack).
    """
    root = Path(workspace_root) if workspace_root is not None else WORKSPACE_ROOT
    pinned = _devstack_override or os.environ.get("LPB_DEVSTACK_DIR")
    found: list[Path] = []

    def add(candidate: Path | None) -> None:
        if candidate is None or not is_devstack_checkout(Path(candidate)):
            return
        here = Path(candidate)
        if any(_same_dir(here, seen) for seen in found):
            return
        found.append(here)

    add(Path(pinned).expanduser() if pinned else None)
    if _inside(_DEVSTACK_ROOT, root):
        add(_DEVSTACK_ROOT)
    add(root / DEVSTACK_REPO)
    try:
        children = sorted(root.iterdir()) if root.is_dir() else []
    except OSError:
        children = []
    for child in children:
        add(child)
    if not found:
        add(_DEVSTACK_ROOT)
    return found


def _same_dir(a: Path, b: Path) -> bool:
    """Same directory, following symlinks; falls back to the literal path."""
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return a == b


def _inside(path: Path, root: Path) -> bool:
    """True when *path* is *root* or below it (compared resolved)."""
    try:
        return path.resolve().is_relative_to(root.resolve())
    except (OSError, AttributeError):
        return False


def in_stack_container() -> bool:
    """True inside a LocalPibox container.

    The image marks itself with /opt/pi-support (the installed CLIs) and
    LPB_CONTAINER_NAME. Outside it — a host checkout, a CI runner — the
    container state (config repo in ~/.pi/agent, extension clones, rendered
    pins, published model server) simply does not exist, so checks over it are
    skipped there rather than failed. Override the detection with
    LPB_STACK_IN_CONTAINER=0/1 (tests, unusual layouts).
    """
    env = os.environ.get("LPB_STACK_IN_CONTAINER")
    if env is not None:
        return env not in ("", "0", "false", "False")
    return Path("/opt/pi-support").is_dir() or bool(os.environ.get("LPB_CONTAINER_NAME"))


def resolve_devstack_dir(
    workspace_root: Path | None = None,
    *,
    cons: Console | None = None,
    inp: TextIO | None = None,
    out: TextIO | None = None,
) -> Path | None:
    """The devstack checkout to operate on, or None when there is none (clone).

    Ambiguity is the operator's call, not a guess: with more than one checkout
    the tool asks, and non-interactively it takes the first candidate
    (see devstack_dirs) and says so — --devstack-dir pins it explicitly.
    """
    dirs = devstack_dirs(workspace_root)
    pinned = _devstack_override or os.environ.get("LPB_DEVSTACK_DIR")
    if pinned and not any(_same_dir(Path(pinned).expanduser(), d) for d in dirs):
        if cons is not None:
            cons.error(f"  devstack: {pinned} is not a devstack checkout "
                       "(needs VERSION + scripts/localpibox/stack/)")
        return None
    if not dirs:
        return None
    if len(dirs) == 1:
        return dirs[0]
    if _devstack_override or os.environ.get("LPB_DEVSTACK_DIR"):
        return dirs[0]                     # the pin is first in the list
    if cons is not None:
        if inp is None and not sys.stdin.isatty():
            cons.warn(f"  devstack: {len(dirs)} checkouts, using {dirs[0]} — "
                      "pass --devstack-dir to pick another")
            return dirs[0]
        choice = Path(select_one(
            "Multiple devstack checkouts found — which one?",
            [str(d) for d in dirs], default_index=0, inp=inp, out=out))
        if choice != dirs[0]:
            cons.info(f"  devstack: using {choice}")
        return choice
    return dirs[0]


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
    # pi is no longer a stack repo (de-forked 2026-08-31 — the lpb-stack/pi
    # fork is retired; the image installs mainstream pi from the npm
    # registry at LPB_PI_VERSION). A local workspace/pi clone may remain as
    # a reference only (retired state: tag pre-defork-0.0.71).
    # pi-subagents is no longer a git repo either (de-forked 2026-09-10 —
    # the lpb-stack/pi-subagents fork is retired; it installs from the
    # upstream npm package, pinned in settings.json without a version).
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
    "lemonade-pi-plugin": "https://github.com/lemonade-sdk/lemonade-pi-plugin.git",
}


def stack_repos() -> list[tuple[str, str, str]]:
    """All stack git repos: (name, dev_branch, main_branch) — single source of truth.

    This is the set of repos the stack *git-manages* (workspace + config).
    npm-installed extensions (any 'npm:' or bare package in settings.json)
    are NOT here — they are not git repos the stack clones or tags.
    """
    return [(n, d, m) for (n, _sym, _ext, d, m) in WORKSPACE_REPOS] + [CONFIG_REPO]


# Repos tagged per release — every stack git repo excluding devstack (devstack
# is tracked by its VERSION file, never tagged). Mirrors the CI tag-repos job.
TAG_REPOS = [
    (n, d, m) for (n, _sym, _ext, d, m) in WORKSPACE_REPOS if n != "devstack"
] + [CONFIG_REPO]

# Pi extension repos (settings.json pin targets) that are git-managed and
# version-tracked by the stack: the extension repos in WORKSPACE_REPOS.
# Any other package in settings.json (an 'npm:' or bare specifier) is NOT here
# — pi manages its version via `pi update --extensions`, so pin sync and
# validate treat it as a presence-only check.
LPB_EXTENSION_REPOS = [n for (n, _sym, is_ext, _d, _m) in WORKSPACE_REPOS if is_ext]

# A version-managed pin is 'git:github.com/lpb-stack/<name>@<version>' where
# <name> is in LPB_EXTENSION_REPOS. Every other package specifier (npm: or
# bare) is presence-only — not tracked by the stack VERSION.
GIT_EXTENSION_PREFIX = "git:github.com/lpb-stack/"


def git_extension_pin(pkg: object) -> str | None:
    """Return the extension repo name if *pkg* is a version-managed git pin
    for one of LPB_EXTENSION_REPOS, else None (i.e. an npm/bare package)."""
    if not isinstance(pkg, str) or not pkg.startswith(GIT_EXTENSION_PREFIX):
        return None
    name = pkg[len(GIT_EXTENSION_PREFIX):].split("@", 1)[0]
    return name if name in LPB_EXTENSION_REPOS else None


def repo_path(name: str) -> Path:
    """Local path where stack repo *name* lives (workspace root or agent dir).

    The devstack entry is found by content, not by directory name — the
    checkout may be called anything (see devstack_dirs).
    """
    if name == CONFIG_REPO[0]:
        return Path(DEFAULT_AGENT_DIR)
    if name == DEVSTACK_REPO:
        return resolve_devstack_dir() or (WORKSPACE_ROOT / DEVSTACK_REPO)
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

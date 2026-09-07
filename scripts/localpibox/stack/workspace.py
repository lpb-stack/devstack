"""Workspace operations: repo sync/clone/ensure and settings.json pin helpers.

The workspace mirrors the 6-repo stack under $LPB_WORKSPACE_ROOT (extension
repos live in the agent git area and are symlinked). These operations align
the workspace to a pipeline (dev/main): clone what's missing, create
symlinks, switch branches, fast-forward to origin.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..cli import confirm
from ..log import Console
from ..run import run_cmd
from .gitutil import _git_authenticated, git, git_auth
from .repos import (
    AGENT_GIT,
    CONFIG_REPO,
    DEFAULT_AGENT_DIR,
    LPB_EXTENSION_REPOS,
    WORKSPACE_REPOS,
    WORKSPACE_ROOT,
    _repo_remote,
)
from .version import expected_branch, expected_pin_version, get_version


# ─── Repo helpers ─────────────────────────────────────────────────────────

def _resolve_repo_path(repo_name: str) -> Path | None:
    """Resolve a stack repo's actual path (follows symlinks); None when missing.

    Workspace repos live under WORKSPACE_ROOT (extension repos are symlinks
    into the agent git area); the config repo lives in the agent dir itself.
    """
    if repo_name == CONFIG_REPO[0]:
        path = Path(DEFAULT_AGENT_DIR)
        return path if (path / ".git").is_dir() else None
    ws_path = WORKSPACE_ROOT / repo_name
    if ws_path.is_symlink():
        target = ws_path.resolve()
        if target.exists():
            return target
    elif (ws_path / ".git").exists() or ws_path.is_dir():
        return ws_path
    return None


def _repo_branch(repo_path: Path) -> str:
    """Get the current branch of a repo."""
    out, _, code = git(repo_path, "branch", "--show-current")
    if code == 0:
        return out.strip()
    return ""


def _repo_head(repo_path: Path) -> str:
    """Get the HEAD commit of a repo."""
    out, _, code = git(repo_path, "rev-parse", "HEAD")
    if code == 0:
        return out.strip()[:8]
    return "?"


def _ensure_branch_tracked(repo_path: Path, branch: str) -> bool:
    """Ensure a remote branch has a local tracking branch. Returns True on success."""
    # Local branch already exists
    if git(repo_path, "rev-parse", "--verify", f"refs/heads/{branch}")[2] == 0:
        return True

    # Fetch the remote branch (also proves it exists); capture the commit so
    # follow-up fetches can't clobber the ref we check out from.
    out, err, code = git_auth(repo_path, "fetch", "origin", branch, timeout=120)
    if code != 0:
        return False
    fetched, _, fcode = git(repo_path, "rev-parse", "--verify", "FETCH_HEAD^{commit}")
    fetched = fetched.strip()
    if fcode != 0 or not fetched:
        return False

    # Clones with a restricted fetch refspec (e.g. lpb-config's shallow
    # `--branch main`) never get refs/remotes/origin/<branch> — repair the
    # refspec so future fetches see all branches.
    fetch_spec, _, _ = git(repo_path, "config", "--get", "remote.origin.fetch")
    if fetch_spec.strip() and "refs/heads/*" not in fetch_spec:
        git(repo_path, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
        git_auth(repo_path, "fetch", "origin", timeout=180)

    # Create the local branch from the fetched commit
    out, err, code = git(repo_path, "checkout", "-q", "-b", branch, fetched)
    if code != 0:
        return False

    # Set upstream tracking once the remote-tracking ref exists
    if git(repo_path, "rev-parse", "--verify", f"refs/remotes/origin/{branch}")[2] == 0:
        git(repo_path, "branch", "--set-upstream-to", f"origin/{branch}", branch)
    return True


def _is_dirty(path: Path) -> bool:
    """True when the worktree has uncommitted changes (tracked or untracked)."""
    out, _, _ = git(path, "status", "--porcelain")
    return bool(out.strip())


def _dirty_files(path: Path) -> list[str]:
    """Uncommitted change lines (porcelain), minus lockfile tool-noise.

    Lockfile rewrites are discarded automatically by workspace sync, so
    they are not reported as drift.
    """
    st, _, _ = git(path, "status", "--porcelain")
    return [line for line in st.splitlines()
            if line.strip() and line[3:] not in LOCKFILE_NAMES]


# Dependency lockfiles a package manager rewrites during `npm install`.
# pi's extension manager runs npm install inside the extension clones, and a
# newer npm than the one that generated the committed lockfile rewrites it
# (e.g. npm 12 dropped the `hasShrinkwrap` field). Such drift is tool noise,
# not user work — discard it so it can't block a sync.
LOCKFILE_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb"}


def _discard_lockfile_drift(name: str, path: Path, cons: Console) -> None:
    """Restore modified dependency lockfiles (package-manager rewrites)."""
    st, _, _ = git(path, "status", "--porcelain")
    drift = [line[3:] for line in st.splitlines()
             if len(line) >= 4 and "M" in line[:2] and line[3:] in LOCKFILE_NAMES]
    if not drift:
        return
    for f in drift:
        git(path, "checkout", "--", f)
    cons.info(f"  {name}: discarded lockfile rewrite(s): {', '.join(sorted(drift))}")


def _detached_ref(path: Path) -> str:
    """Readable description of a detached HEAD ('detached @ 0.0.52-lpb-dev'); '' on a branch."""
    out, _, code = git(path, "rev-parse", "--abbrev-ref", "HEAD")
    if code != 0 or out.strip() != "HEAD":
        return ""
    tag, _, tcode = git(path, "describe", "--tags", "--exact-match")
    if tcode == 0 and tag.strip():
        return f"detached @ {tag.strip()}"
    return f"detached @ {_repo_head(path)}"


def _clone_repo(name: str, dest: Path, expected: str, cons: Console) -> bool:
    """Clone a stack repo at *expected* into *dest*. Returns True on success."""
    remote = _repo_remote(name)
    cons.info(f"  {name}: cloning {remote} (branch: {expected}) ...")
    dest.parent.mkdir(parents=True, exist_ok=True)
    out, err, code = run_cmd(
        _git_authenticated(["clone", "-q", "--branch", expected, remote, str(dest)]),
        timeout=600,
    )
    if code != 0:
        cons.error(f"  {name}: clone failed ({(err or out).strip()[:100]})")
        shutil.rmtree(dest, ignore_errors=True)
        return False
    return True


def _ensure_counterpart_branch(
    name: str, path: Path, pipeline: str,
    dev_branch: str, main_branch: str, cons: Console,
) -> None:
    """Ensure the OTHER pipeline's branch also exists as a local branch.

    A `--branch <expected>` clone (or pi's pinned-tag checkout) only carries
    one local branch, but `lpb-devstack validate` needs local refs for BOTH
    (e.g. pi's `lpb` + `lpb-dev`) — so a freshly synced workspace used to
    fail validation until the second branch was created by hand. Creating it
    here keeps sync and validate consistent. The worktree is NOT switched:
    only a local ref is created (a `checkout -b` would leave the repo on
    the wrong branch). Idempotent; a branch that does not exist on origin
    (fresh fork) is reported, not an error.
    """
    counterpart = main_branch if pipeline == "dev" else dev_branch
    if git(path, "rev-parse", "--verify", f"refs/heads/{counterpart}")[2] == 0:
        return
    # Fetch the branch (proves it exists; updates the remote-tracking ref
    # when the fetch refspec is unrestricted).
    out, err, code = git_auth(path, "fetch", "origin", counterpart, timeout=120)
    if code != 0:
        cons.info(f"  {name}: origin/{counterpart} not found on remote — skipped")
        return
    if git(path, "rev-parse", "--verify", f"refs/remotes/origin/{counterpart}")[2] != 0:
        # Restricted refspec (shallow/partial clone) — repair so the
        # remote-tracking ref exists for --track.
        git(path, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
        git_auth(path, "fetch", "origin", timeout=180)
    out, err, code = git(path, "branch", "--track", counterpart, f"origin/{counterpart}")
    if code == 0:
        cons.info(f"  {name}: local branch '{counterpart}' created (tracking origin)")
    else:
        cons.info(f"  {name}: could not create local '{counterpart}' — skipped")


def _sync_repo(name: str, path: Path, expected: str, cons: Console) -> bool:
    """Fetch, align the branch, and fast-forward *path* to origin/*expected*.

    Returns True when the repo ends up on *expected* and up to date.
    Dirty worktrees are left untouched and reported.
    """
    out, err, code = git_auth(path, "fetch", "--prune", "origin", timeout=180)
    if code != 0:
        cons.error(f"  {name}: fetch failed ({(err or out).strip()[:80]})")
        return False

    if _is_dirty(path):
        _discard_lockfile_drift(name, path, cons)
        if _is_dirty(path):
            st, _, _ = git(path, "status", "--short")
            first = (st.strip().splitlines() or ["?"])[0][:60]
            cons.warn(f"  {name}: uncommitted changes ({first}) — skipped (commit or stash first)")
            return False

    branch = _repo_branch(path)
    if branch != expected:
        before = _detached_ref(path) or branch or "?"
        if not _ensure_branch_tracked(path, expected):
            cons.error(f"  {name}: branch '{expected}' not found on origin")
            return False
        out, err, code = git(path, "checkout", "-q", expected)
        if code != 0:
            cons.error(f"  {name}: checkout '{expected}' failed ({err.strip()[:80]})")
            return False
        cons.info(f"  {name}: {before} → {expected}")

    out, err, code = git(path, "merge", "--ff-only", "-q", f"origin/{expected}")
    if code != 0:
        cons.error(f"  {name}: fast-forward to origin/{expected} failed ({err.strip()[:80]})")
        return False

    cons.info(f"  {name}: {expected} @ {_repo_head(path)} ✅")
    return True


# ─── Config repo (lives in the agent dir, managed by lpb-config) ──────────

def _status_config(pipeline: str, cons: Console) -> bool:
    """Report the config repo; True when aligned with *pipeline*."""
    path = Path(DEFAULT_AGENT_DIR)
    if not (path / ".git").exists():
        cons.warn("  config: MISSING")
        return False
    expected = expected_branch("config", pipeline)
    branch = _repo_branch(path)
    head = _repo_head(path)
    if branch == expected:
        cons.info(f"  config: {branch} ({head}) ✅")
        return True
    cons.warn(f"  config: {branch} (expected: {expected}) ({head}) ❌")
    return False


def _sync_config(pipeline: str, cons: Console) -> bool:
    """Align the config repo to *pipeline*; True on success."""
    path = Path(DEFAULT_AGENT_DIR)
    if not (path / ".git").exists():
        cons.warn(f"  config: no git repo at {path} — run 'lpb-config update' to install it")
        return False
    return _sync_repo("config", path, expected_branch("config", pipeline), cons)


# ─── Workspace commands ───────────────────────────────────────────────────

def cmd_workspace_status(pipeline: str, cons: Console) -> int:
    """Show workspace repo branches and alignment."""
    cons.info(f"Pipeline: {pipeline} (VERSION: {get_version()})")
    cons.info("")

    all_aligned = True
    for name, is_sym, is_ext, dev_branch, main_branch in WORKSPACE_REPOS:
        expected = dev_branch if pipeline == "dev" else main_branch
        path = _resolve_repo_path(name)

        if path is None:
            cons.warn(f"  {name}: MISSING")
            all_aligned = False
            continue

        branch = _repo_branch(path)
        head = _repo_head(path)
        symlink = " (symlink)" if is_sym else ""
        where = branch if branch else (_detached_ref(path) or "(detached)")

        if branch == expected:
            cons.info(f"  {name}: {where}{symlink} ({head}) ✅")
        else:
            cons.warn(f"  {name}: {where} (expected: {expected}){symlink} ({head}) ❌")
            all_aligned = False

    # Config repo (agent dir)
    if not _status_config(pipeline, cons):
        all_aligned = False

    cons.info("")
    if all_aligned:
        cons.done("All repos aligned with pipeline.")
    else:
        cons.warn("Some repos are misaligned. Run 'lpb-devstack workspace sync' to fix.")

    return 0 if all_aligned else 1


def cmd_workspace_sync(pipeline: str, cons: Console) -> int:
    """Prepare the workspace for *pipeline*:

    - clone missing repos (extension clones + real workspace repos)
    - create/repair symlinks for extension repos
    - check out the pipeline's expected branch (fixes detached-HEAD checkouts
      left behind by pi's pinned-tag extension loads)
    - fast-forward to origin

    Returns 0 only when every repo is on the expected branch and up to date.
    """
    cons.info(f"Preparing workspace for pipeline: {pipeline}")
    cons.info("")

    prepared = True
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)

    for name, is_sym, _is_ext, dev_branch, main_branch in WORKSPACE_REPOS:
        expected = dev_branch if pipeline == "dev" else main_branch

        if is_sym:
            # Extension repos live in the agent git area; the workspace entry is a symlink
            ext_path = AGENT_GIT / name
            ws_path = WORKSPACE_ROOT / name

            if not (ext_path / ".git").exists():
                if not _clone_repo(name, ext_path, expected, cons):
                    prepared = False
                    continue

            if ws_path.is_symlink():
                if ws_path.resolve() != ext_path.resolve():
                    ws_path.unlink()
                    ws_path.symlink_to(ext_path)
                    cons.info(f"  {name}: symlink → {ext_path}")
            elif ws_path.is_dir():
                if (ws_path / ".git").exists():
                    rem, _, _ = git(ws_path, "remote", "get-url", "origin")
                    if rem.strip() == _repo_remote(name):
                        shutil.rmtree(ws_path)
                        ws_path.symlink_to(ext_path)
                        cons.info(f"  {name}: replaced real clone with symlink → {ext_path}")
                    else:
                        cons.error(f"  {name}: {ws_path} is a git repo with a different remote — left untouched")
                        prepared = False
                        continue
                else:
                    cons.error(f"  {name}: {ws_path} exists but is not a git repo — left untouched")
                    prepared = False
                    continue
            else:
                ws_path.symlink_to(ext_path)
                cons.info(f"  {name}: symlink → {ext_path}")

            if not _sync_repo(name, ext_path, expected, cons):
                prepared = False
                continue
            _ensure_counterpart_branch(
                name, ext_path, pipeline, dev_branch, main_branch, cons)
            continue

        # Real repos live in the workspace root
        path = _resolve_repo_path(name)
        if path is None:
            if not _clone_repo(name, WORKSPACE_ROOT / name, expected, cons):
                prepared = False
                continue
            path = WORKSPACE_ROOT / name

        if not _sync_repo(name, path, expected, cons):
            prepared = False
        else:
            _ensure_counterpart_branch(
                name, path, pipeline, dev_branch, main_branch, cons)

    # Config repo (the agent dir itself)
    if not _sync_config(pipeline, cons):
        prepared = False

    cons.info("")
    if prepared:
        cons.done("Workspace prepared.")
        return 0
    cons.warn("Workspace not fully prepared — see messages above.")
    return 1



# ─── Settings.json helpers ──────────────────────────────────────────────

def _read_settings(agent_dir: str | Path) -> dict | None:
    """Read settings.json from agent dir."""
    path = Path(agent_dir) / "settings.json"
    if not path.is_file():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _write_settings(agent_dir: str | Path, settings: dict) -> None:
    """Write settings.json to agent dir."""
    path = Path(agent_dir) / "settings.json"
    with open(path, "w") as f:
        json.dump(settings, f, indent=2)
        f.write("\n")


def _get_pinned_versions(settings: dict) -> dict[str, str]:
    """Extract version pins for LPB extension repos from settings."""
    pins: dict[str, str] = {}
    for pkg in settings.get("packages", []):
        if not isinstance(pkg, str):
            continue
        for name in LPB_EXTENSION_REPOS:
            marker = f"lpb-stack/{name}@"
            if marker in pkg:
                pins[name] = pkg.split("@")[-1]
    return pins


def _update_pinned_versions(settings: dict, target_version: str) -> list[tuple[str, str, str]]:
    """Update LPB extension pins to target_version. Returns list of changes."""
    packages = settings.get("packages", [])
    changes: list[tuple[str, str, str]] = []
    for name in LPB_EXTENSION_REPOS:
        marker = f"git:github.com/lpb-stack/{name}@"
        for i, pkg in enumerate(packages):
            if isinstance(pkg, str) and pkg.startswith(marker):
                old_tag = pkg.split("@")[-1]
                if old_tag != target_version:
                    new_pkg = f"{marker}{target_version}"
                    packages[i] = new_pkg
                    changes.append((name, old_tag, target_version))
    settings["packages"] = packages
    return changes


# ─── Workspace: sync extension pins ───────────────────────────────────────

def cmd_workspace_sync_pins(pipeline: str, cons: Console) -> int:
    """Sync settings.json extension pins to the pipeline's stack version."""
    agent_dir = Path(DEFAULT_AGENT_DIR)
    settings = _read_settings(agent_dir)

    if settings is None:
        cons.error(f"settings.json not found: {agent_dir}")
        cons.info("  Run 'lpb-config render' to generate it from the template.")
        return 1

    target_version = expected_pin_version(pipeline)

    current_pins = _get_pinned_versions(settings)

    cons.info(f"Pipeline:     {pipeline}")
    cons.info(f"LPB_VERSION:  {get_version()}")
    cons.info(f"Target pins:  {target_version}")
    cons.info("")

    # Check mismatches
    mismatches = []
    for name in LPB_EXTENSION_REPOS:
        cur = current_pins.get(name, "(unpinned)")
        if cur != target_version:
            mismatches.append((name, cur, target_version))

    if not mismatches:
        cons.info("All extension pins already match LPB_VERSION.")
        return 0

    cons.warn(f"Version mismatch ({len(mismatches)} extension(s)):")
    for name, cur, target in mismatches:
        cons.warn(f"  {name}: {cur} → {target}")

    cons.info("")
    if confirm("Update settings.json extension pins?"):
        changes = _update_pinned_versions(settings, target_version)
        _write_settings(agent_dir, settings)
        for name, old, new in changes:
            cons.info(f"  {name}: {old} → {new}")
        cons.info("")
        cons.done("Extension pins updated. Run 'pi update --extensions' to apply.")
    else:
        cons.info("Skipped. Run 'lpb-config sync-pins' when ready.")

    return 0 if not mismatches else 1

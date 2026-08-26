#!/usr/bin/env python3
"""localpibox.stack workspace-sync tests:
detached-head checkout, clone-missing, dirty skip, lockfile-drift discard,
wrong branch, missing config, main pipeline."""
from __future__ import annotations

from testharness import run_lpbx_suite, _bare_remote, _push_branch, _quiet_console, log_mod

import os
import io
import subprocess
from unittest import mock

from localpibox.stack.workspace import cmd_workspace_sync
from localpibox.stack import workspace as ws_mod


def _workspace_patch(tmpdir, repos, config_branch="dev", config_repo=True):
    """Point lpb-config workspace constants at *tmpdir* (context manager).

    Installs a clean config repo on *config_branch* at the agent dir unless
    config_repo is False. Mirrors the real layout: the agent dir is a git
    repo whose worktree contains the extension clones under git/ (ignored).
    """
    agent = tmpdir / "agent"
    agent.mkdir(parents=True, exist_ok=True)
    if config_repo:
        cfg_remote, _ = _bare_remote(tmpdir, "config", config_branch)
        subprocess.run(["git", "-C", str(agent), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(agent), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(agent), "config", "user.name", "t"], check=True)
        subprocess.run(["git", "-C", str(agent), "remote", "add", "origin", str(cfg_remote)], check=True)
        subprocess.run(["git", "-C", str(agent), "fetch", "-q", "origin", config_branch], check=True)
        subprocess.run(["git", "-C", str(agent), "checkout", "-q", "-b", config_branch, "FETCH_HEAD"], check=True)
        (agent / ".gitignore").write_text("git/\n")
        subprocess.run(["git", "-C", str(agent), "add", ".gitignore"], check=True)
        subprocess.run(["git", "-C", str(agent), "commit", "-qm", "gitignore"], check=True)
        subprocess.run(["git", "-C", str(agent), "push", "-q", "origin", config_branch], check=True)
    return mock.patch.multiple(
        ws_mod,
        WORKSPACE_REPOS=repos,
        WORKSPACE_ROOT=tmpdir / "workspace",
        AGENT_GIT=agent / "git" / "github.com" / "lpb-stack",
        DEFAULT_AGENT_DIR=str(agent),
    )


def _branch(p):
    return subprocess.run(
        ["git", "-C", str(p), "branch", "--show-current"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def test_lpb_config_sync_detached_head(tmpdir):
    """Clone detached at a tag (pi's pinned-tag checkout) → ends on branch @ tip."""
    remote, src = _bare_remote(tmpdir, "repo-a", "dev")
    ag = tmpdir / "agent" / "git" / "github.com" / "lpb-stack"
    clone = ag / "repo-a"
    subprocess.run(["git", "clone", "-q", str(remote), str(clone)], check=True)
    subprocess.run(["git", "-C", str(clone), "checkout", "-q", "0.0.1"], check=True)
    _push_branch(src, "dev")
    with _workspace_patch(tmpdir, [("repo-a", True, True, "dev", "main")]):
        code = cmd_workspace_sync("dev", _quiet_console())
    ws = tmpdir / "workspace"
    assert code == 0
    assert (ws / "repo-a").is_symlink()
    assert (ws / "repo-a").resolve() == clone.resolve()
    assert _branch(clone) == "dev"
    assert (clone / "f").read_text() == "two"  # fast-forwarded


def test_lpb_config_sync_clones_missing(tmpdir):
    """Missing extension clone + missing real repo → both cloned and linked."""
    repos = [("repo-a", True, True, "dev", "main"), ("repo-b", False, False, "dev", "main")]
    _bare_remote(tmpdir, "repo-a", "dev")
    _bare_remote(tmpdir, "repo-b", "dev")
    with mock.patch.dict(os.environ, {"LPB_STACK_REMOTE_BASE": str(tmpdir / "remotes")}), \
         _workspace_patch(tmpdir, repos):
        code = cmd_workspace_sync("dev", _quiet_console())
    ws = tmpdir / "workspace"
    ag = tmpdir / "agent" / "git" / "github.com" / "lpb-stack"
    assert code == 0
    assert (ag / "repo-a" / ".git").exists()
    assert (ws / "repo-a").is_symlink()
    assert (ws / "repo-b" / ".git").exists()  # real clone in workspace
    assert _branch(ws / "repo-b") == "dev"


def test_lpb_config_sync_dirty_skipped(tmpdir):
    """Dirty worktree → left untouched, reported, non-zero exit."""
    remote, src = _bare_remote(tmpdir, "repo-a", "dev")
    clone = tmpdir / "agent" / "git" / "github.com" / "lpb-stack" / "repo-a"
    subprocess.run(["git", "clone", "-q", str(remote), str(clone)], check=True)
    (clone / "f").write_text("local edit")
    _push_branch(src, "dev")
    out, err = io.StringIO(), io.StringIO()
    with _workspace_patch(tmpdir, [("repo-a", True, True, "dev", "main")]):
        cons = log_mod.Console(color=False, out=out, err=err)
        code = cmd_workspace_sync("dev", cons)
    assert code == 1
    assert _branch(clone) == "dev"
    assert (clone / "f").read_text() == "local edit"  # untouched
    assert "uncommitted changes" in (out.getvalue() + err.getvalue())


def test_lpb_config_sync_lockfile_drift_discarded(tmpdir):
    """Only package-lock.json dirty (npm rewrite) → discarded, sync proceeds."""
    remote, src = _bare_remote(tmpdir, "repo-a", "dev")
    (src / "package-lock.json").write_text('{\n  "name": "repo-a"\n}\n')
    subprocess.run(["git", "-C", str(src), "add", "package-lock.json"], check=True)
    subprocess.run(["git", "-C", str(src), "commit", "-qm", "lockfile"], check=True)
    subprocess.run(["git", "-C", str(src), "push", "-q", "origin", "dev"], check=True)
    clone = tmpdir / "agent" / "git" / "github.com" / "lpb-stack" / "repo-a"
    subprocess.run(["git", "clone", "-q", str(remote), str(clone)], check=True)
    (clone / "package-lock.json").write_text('{\n  "name": "repo-a", "rewritten": true\n}\n')
    _push_branch(src, "dev")
    out, err = io.StringIO(), io.StringIO()
    with _workspace_patch(tmpdir, [("repo-a", True, True, "dev", "main")]):
        cons = log_mod.Console(color=False, out=out, err=err)
        code = cmd_workspace_sync("dev", cons)
    assert code == 0
    assert (clone / "package-lock.json").read_text() == '{\n  "name": "repo-a"\n}\n'  # restored
    assert (clone / "f").read_text() == "two"  # fast-forwarded
    assert "lockfile rewrite" in (out.getvalue() + err.getvalue())


def test_lpb_config_sync_mixed_dirt_still_skipped(tmpdir):
    """package-lock.json + a real file dirty → still skipped."""
    remote, src = _bare_remote(tmpdir, "repo-a", "dev")
    (src / "package-lock.json").write_text('{\n  "name": "repo-a"\n}\n')
    subprocess.run(["git", "-C", str(src), "add", "package-lock.json"], check=True)
    subprocess.run(["git", "-C", str(src), "commit", "-qm", "lockfile"], check=True)
    subprocess.run(["git", "-C", str(src), "push", "-q", "origin", "dev"], check=True)
    clone = tmpdir / "agent" / "git" / "github.com" / "lpb-stack" / "repo-a"
    subprocess.run(["git", "clone", "-q", str(remote), str(clone)], check=True)
    (clone / "package-lock.json").write_text('{\n  "rewritten": true\n}\n')
    (clone / "f").write_text("local edit")
    _push_branch(src, "dev")
    with _workspace_patch(tmpdir, [("repo-a", True, True, "dev", "main")]):
        code = cmd_workspace_sync("dev", _quiet_console())
    assert code == 1
    assert _branch(clone) == "dev"
    assert (clone / "f").read_text() == "local edit"  # untouched
    assert (clone / "package-lock.json").read_text() == '{\n  "name": "repo-a"\n}\n'  # drift discarded


def test_lpb_config_sync_wrong_branch(tmpdir):
    """Clean repo on a feature branch → switched to pipeline branch @ tip."""
    remote, src = _bare_remote(tmpdir, "repo-a", "dev")
    subprocess.run(["git", "-C", str(src), "checkout", "-q", "-b", "feature"], check=True)
    subprocess.run(["git", "-C", str(src), "push", "-q", "origin", "feature"], check=True)
    clone = tmpdir / "agent" / "git" / "github.com" / "lpb-stack" / "repo-a"
    subprocess.run(["git", "clone", "-q", str(remote), str(clone)], check=True)
    subprocess.run(["git", "-C", str(clone), "checkout", "-q", "feature"], check=True)
    _push_branch(src, "dev")
    with _workspace_patch(tmpdir, [("repo-a", True, True, "dev", "main")]):
        code = cmd_workspace_sync("dev", _quiet_console())
    assert code == 0
    assert _branch(clone) == "dev"
    assert (clone / "f").read_text() == "two"


def test_lpb_config_sync_missing_config(tmpdir):
    """No config repo at agent dir → warning + non-zero exit."""
    remote, src = _bare_remote(tmpdir, "repo-a", "dev")
    clone = tmpdir / "agent" / "git" / "github.com" / "lpb-stack" / "repo-a"
    subprocess.run(["git", "clone", "-q", str(remote), str(clone)], check=True)
    out, err = io.StringIO(), io.StringIO()
    with _workspace_patch(tmpdir, [("repo-a", True, True, "dev", "main")], config_repo=False):
        cons = log_mod.Console(color=False, out=out, err=err)
        code = cmd_workspace_sync("dev", cons)
    assert code == 1
    assert "config" in (out.getvalue() + err.getvalue())


def test_lpb_config_sync_main_pipeline(tmpdir):
    """main pipeline → main branches selected for repo + config."""
    remote, src = _bare_remote(tmpdir, "repo-a", "dev")
    subprocess.run(["git", "-C", str(src), "checkout", "-q", "-b", "main"], check=True)
    (src / "f").write_text("stable")
    subprocess.run(["git", "-C", str(src), "commit", "-qam", "stable"], check=True)
    subprocess.run(["git", "-C", str(src), "push", "-q", "origin", "main"], check=True)
    clone = tmpdir / "agent" / "git" / "github.com" / "lpb-stack" / "repo-a"
    subprocess.run(["git", "clone", "-q", "--branch", "main", str(remote), str(clone)], check=True)
    with _workspace_patch(tmpdir, [("repo-a", True, True, "dev", "main")], config_branch="main"):
        code = cmd_workspace_sync("main", _quiet_console())
    assert code == 0
    assert _branch(clone) == "main"
    assert (clone / "f").read_text() == "stable"


def _local_branches(p):
    return set(subprocess.run(
        ["git", "-C", str(p), "branch", "--format", "%(refname:short)"],
        check=True, capture_output=True, text=True,
    ).stdout.split())


def test_lpb_config_sync_ensures_counterpart_branch(tmpdir):
    """Fresh clone (--branch dev) → sync also creates the local 'main' branch.

    validate's fork-branch check needs BOTH local refs (pi: lpb + lpb-dev);
    a --branch clone only has one. Sync must leave the workspace in a state
    where validate can pass — no manual branch creation in between.
    """
    remote, src = _bare_remote(tmpdir, "repo-a", "dev")
    # A second branch on the remote (the other pipeline's branch)
    subprocess.run(["git", "-C", str(src), "checkout", "-q", "-b", "main"], check=True)
    (src / "f").write_text("stable")
    subprocess.run(["git", "-C", str(src), "commit", "-qam", "stable"], check=True)
    subprocess.run(["git", "-C", str(src), "push", "-q", "origin", "main"], check=True)
    with mock.patch.dict(os.environ, {"LPB_STACK_REMOTE_BASE": str(tmpdir / "remotes")}), \
         _workspace_patch(tmpdir, [("repo-a", False, False, "dev", "main")]):
        code = cmd_workspace_sync("dev", _quiet_console())
    clone = tmpdir / "workspace" / "repo-a"
    assert code == 0
    assert _branch(clone) == "dev"
    assert {"dev", "main"} <= _local_branches(clone)  # both pipelines tracked locally
    # Idempotent: a second sync changes nothing (config_repo=False: the agent
    # repo from the first patch is still in place)
    with mock.patch.dict(os.environ, {"LPB_STACK_REMOTE_BASE": str(tmpdir / "remotes")}), \
         _workspace_patch(tmpdir, [("repo-a", False, False, "dev", "main")], config_repo=False):
        code = cmd_workspace_sync("dev", _quiet_console())
    assert code == 0
    assert {"dev", "main"} <= _local_branches(clone)


def test_lpb_config_sync_missing_counterpart_is_not_an_error(tmpdir):
    """Remote without the counterpart branch (fresh fork) → sync still succeeds."""
    _bare_remote(tmpdir, "repo-a", "dev")  # only 'dev' exists on the remote
    with mock.patch.dict(os.environ, {"LPB_STACK_REMOTE_BASE": str(tmpdir / "remotes")}), \
         _workspace_patch(tmpdir, [("repo-a", False, False, "dev", "main")]):
        code = cmd_workspace_sync("dev", _quiet_console())
    clone = tmpdir / "workspace" / "repo-a"
    assert code == 0
    assert _branch(clone) == "dev"
    assert "main" not in _local_branches(clone)


def main() -> int:
    return run_lpbx_suite("lpb-config workspace sync tests", globals())


if __name__ == "__main__":
    raise SystemExit(main())

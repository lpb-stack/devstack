#!/usr/bin/env python3
"""scripts/lpb-devstack tests: VERSION bumping (patch/minor/set,
commit, missing VERSION) and repo tagging (tag-repos, missing clone)."""
from __future__ import annotations

from testharness import run_lpbx_suite, _bare_remote, _quiet_console, _load_script, SCRIPTS_DIR

import os
import subprocess
from unittest import mock

from localpibox.stack import version as ver_mod
from localpibox.stack import workspace as ws_mod
from localpibox.stack import release as rel_mod

ld = _load_script('lpb_devstack', SCRIPTS_DIR / 'lpb-devstack')

# ─── lpb-devstack: VERSION bumping ────────────────────────────────────────

def _devstack_root_patch(root, tmpdir):
    """Point stack version discovery at *root* (context manager)."""
    return mock.patch.multiple(
        ver_mod,
        _DEVSTACK_ROOT=root,
        WORKSPACE_ROOT=tmpdir / "nowhere",
        _VERSION_FILE=None,
    )


def test_devstack_bump_patch(tmpdir):
    root = tmpdir / "devstack"
    root.mkdir()
    (root / "VERSION").write_text("0.0.57-lpb-dev\n")
    with _devstack_root_patch(root, tmpdir):
        code = ld.cmd_bump(_quiet_console(), no_commit=True)
    assert code == 0
    assert (root / "VERSION").read_text().strip() == "0.0.58-lpb-dev"


def test_devstack_bump_minor_and_set(tmpdir):
    root = tmpdir / "devstack"
    root.mkdir()
    (root / "VERSION").write_text("0.0.9-lpb-dev\n")
    with _devstack_root_patch(root, tmpdir):
        assert ld.cmd_bump(_quiet_console(), minor=True, no_commit=True) == 0
        assert (root / "VERSION").read_text().strip() == "0.1.0-lpb-dev"
        # explicit --set (same value → no-op, still 0)
        assert ld.cmd_bump(_quiet_console(), set_version="0.1.0-lpb-dev", no_commit=True) == 0
        assert (root / "VERSION").read_text().strip() == "0.1.0-lpb-dev"
        # invalid --set rejected
        assert ld.cmd_bump(_quiet_console(), set_version="1.2.3", no_commit=True) == 1
        assert (root / "VERSION").read_text().strip() == "0.1.0-lpb-dev"


def test_devstack_bump_commits(tmpdir):
    root = tmpdir / "devstack"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    (root / "VERSION").write_text("0.0.57-lpb-dev\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "init"], check=True)
    with _devstack_root_patch(root, tmpdir):
        code = ld.cmd_bump(_quiet_console())  # commit (no push)
    assert code == 0
    r = subprocess.run(
        ["git", "-C", str(root), "show", "HEAD:VERSION"],
        capture_output=True, text=True)
    assert r.stdout.strip() == "0.0.58-lpb-dev"


def test_devstack_bump_missing_version(tmpdir):
    with mock.patch.object(ver_mod, "_VERSION_FILE", None), \
         mock.patch.object(ver_mod, "_DEVSTACK_ROOT", tmpdir / "nope"), \
         mock.patch.object(ver_mod, "WORKSPACE_ROOT", tmpdir / "nope2"):
        assert ld.cmd_bump(_quiet_console(), no_commit=True) == 1


# ─── lpb-devstack: dev release gate (bump requires other repos pushed) ────

def _gate_env(tmpdir, branch="dev"):
    """Fake stack repo (bare remote + clean clone) + patches so the gate sees
    only it. Returns (work, patches)."""
    _remote, work = _bare_remote(tmpdir, "fake-repo", branch)
    p1 = mock.patch.object(rel_mod, "TAG_REPOS", [("fake-repo", branch, "stable")])
    p2 = mock.patch.object(rel_mod, "repo_path", lambda name: work)
    p1.start()
    p2.start()
    return work, [p1, p2]


def test_dev_release_gate_clean(tmpdir):
    _work, patches = _gate_env(tmpdir)
    try:
        assert rel_mod.dev_release_gate(_quiet_console()) == 0
    finally:
        for p in patches:
            p.stop()


def test_dev_release_gate_blocks_unpushed(tmpdir):
    work, patches = _gate_env(tmpdir)
    try:
        (work / "g").write_text("local only")
        subprocess.run(["git", "-C", str(work), "add", "."], check=True)
        subprocess.run(["git", "-C", str(work), "commit", "-qm", "local"], check=True)
        cons = _quiet_console()
        assert rel_mod.dev_release_gate(cons) == 1
        assert "unpushed" in cons.err.getvalue()
    finally:
        for p in patches:
            p.stop()


def test_dev_release_gate_blocks_uncommitted(tmpdir):
    work, patches = _gate_env(tmpdir)
    try:
        (work / "f").write_text("dirty")
        cons = _quiet_console()
        assert rel_mod.dev_release_gate(cons) == 1
        assert "uncommitted" in cons.err.getvalue()
    finally:
        for p in patches:
            p.stop()


def test_dev_release_gate_blocks_wrong_branch(tmpdir):
    work, patches = _gate_env(tmpdir)
    try:
        subprocess.run(["git", "-C", str(work), "checkout", "-q", "-b", "feature"], check=True)
        cons = _quiet_console()
        assert rel_mod.dev_release_gate(cons) == 1
        assert "expected dev" in cons.err.getvalue()
    finally:
        for p in patches:
            p.stop()


def test_devstack_bump_gate_blocks_and_force_bypasses(tmpdir):
    root = tmpdir / "devstack"
    root.mkdir()
    (root / "VERSION").write_text("0.0.57-lpb-dev\n")
    work, patches = _gate_env(tmpdir)
    (work / "g").write_text("local only")
    subprocess.run(["git", "-C", str(work), "add", "."], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-qm", "local"], check=True)
    try:
        with _devstack_root_patch(root, tmpdir):
            # Gate blocks: VERSION must not be written
            assert ld.cmd_bump(_quiet_console(), no_commit=True) == 1
            assert (root / "VERSION").read_text().strip() == "0.0.57-lpb-dev"
            # --force bypasses the gate
            assert ld.cmd_bump(_quiet_console(), no_commit=True, force=True) == 0
            assert (root / "VERSION").read_text().strip() == "0.0.58-lpb-dev"
    finally:
        for p in patches:
            p.stop()


# ─── lpb-devstack: repo tagging ───────────────────────────────────────────

def test_devstack_tag_repos(tmpdir):
    remote, _src = _bare_remote(tmpdir, "pi", "lpb-dev")
    # Local workspace clone (cmd_tag_repos tags via the workspace repos)
    ws = tmpdir / "workspace" / "pi"
    ws.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", "--branch", "lpb-dev", str(remote), str(ws)], check=True)
    env = mock.patch.dict(os.environ, {"LPB_STACK_REMOTE_BASE": str(tmpdir / "remotes")})
    patch_repos = mock.patch.object(ld, "TAG_REPOS", [("pi", "lpb-dev", "lpb")])
    patch_ws = mock.patch.object(ws_mod, "WORKSPACE_ROOT", tmpdir / "workspace")
    for p in (env, patch_repos, patch_ws):
        p.start()
    try:
        code = ld.cmd_tag_repos(_quiet_console(), pipeline="dev", version="0.0.58-lpb-dev")
        assert code == 0
        r = subprocess.run(
            ["git", "ls-remote", str(remote), "refs/tags/0.0.58-lpb-dev"],
            capture_output=True, text=True)
        assert "0.0.58-lpb-dev" in r.stdout
        # idempotent: second run reports "already exists" and succeeds
        assert ld.cmd_tag_repos(_quiet_console(), pipeline="dev", version="0.0.58-lpb-dev") == 0
        # main pipeline targets the 'lpb' branch (absent on this remote → fail fast)
        assert ld.cmd_tag_repos(_quiet_console(), pipeline="main", version="0.0.58-lpb") == 1
    finally:
        for p in (env, patch_repos, patch_ws):
            p.stop()


def test_devstack_tag_repos_missing_clone(tmpdir):
    with mock.patch.object(ld, "TAG_REPOS", [("pi", "lpb-dev", "lpb")]), \
         mock.patch.object(ws_mod, "WORKSPACE_ROOT", tmpdir / "empty-ws"):
        cons = _quiet_console()
        assert ld.cmd_tag_repos(cons, pipeline="dev", version="0.0.58-lpb-dev") == 1
        assert "workspace sync" in cons.err.getvalue()


def test_devstack_release_cli_surface(tmpdir):
    """New release subcommands/flags parse: docs-ready + promote --force."""
    import contextlib
    import io
    for argv, needle in ((["release", "docs-ready", "--help"], "docs-ready"),
                         (["release", "promote", "--help"], "--force")):
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf):
                ld.main(argv)
            raise AssertionError(f"{argv} did not exit")
        except SystemExit as e:
            assert e.code == 0
            assert needle in buf.getvalue()


def main() -> int:
    return run_lpbx_suite("lpb-devstack tests", globals())


if __name__ == "__main__":
    raise SystemExit(main())

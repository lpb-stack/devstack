#!/usr/bin/env python3
"""Commit guard tests — devstack pre-commit: main is promote-only.

The guard lives in localpibox.stack.validate (checked FIRST in
cmd_validate, which .githooks/pre-commit runs). Development happens on
the dev branch or feature branches; the stable branch (main) only moves
via `lpb-devstack release promote`, which sets LPB_ALLOW_MAIN_COMMIT=1
around its devstack VERSION-strip commit (the only legitimate `git
commit` on main — promote's merges don't trigger hooks).
"""
from __future__ import annotations

from testharness import run_lpbx_suite, _quiet_console

import io
import os
from unittest import mock

from localpibox.stack import validate as vmod


def test_guard_blocks_main_without_exemption():
    with mock.patch.object(vmod, "_branch_is_main", return_value="main"), \
         mock.patch.dict(os.environ, {"LPB_ALLOW_MAIN_COMMIT": ""}):
        assert vmod._commit_guard_failed() is True


def test_guard_allows_main_with_exemption():
    with mock.patch.object(vmod, "_branch_is_main", return_value="main"), \
         mock.patch.dict(os.environ, {"LPB_ALLOW_MAIN_COMMIT": "1"}):
        assert vmod._commit_guard_failed() is False


def test_guard_allows_dev_branch():
    with mock.patch.object(vmod, "_branch_is_main", return_value="dev"), \
         mock.patch.dict(os.environ, {"LPB_ALLOW_MAIN_COMMIT": ""}):
        assert vmod._commit_guard_failed() is False


def test_guard_allows_feature_branches():
    """Not only 'dev' — any non-main branch (feature work) passes."""
    for branch in ("feature/payload-tuning", "hotfix/x", "docs/rewrite"):
        with mock.patch.object(vmod, "_branch_is_main", return_value=branch), \
             mock.patch.dict(os.environ, {"LPB_ALLOW_MAIN_COMMIT": ""}):
            assert vmod._commit_guard_failed() is False, branch


def test_validate_blocks_commit_on_main(tmpdir):
    """Inside a git hook (GIT_INDEX_FILE set), validating on the stable
    branch fails FIRST — the commit aborts with a clear, actionable error
    before any other check runs."""
    out, err = io.StringIO(), io.StringIO()
    cons = type(_quiet_console())(color=False, out=out, err=err)
    with mock.patch.object(vmod, "_branch_is_main", return_value="main"), \
         mock.patch.dict(os.environ, {"GIT_INDEX_FILE": str(tmpdir / "index"),
                                      "LPB_ALLOW_MAIN_COMMIT": ""}):
        code = vmod.cmd_validate("main", cons)
    text = out.getvalue() + err.getvalue()
    assert code == 1
    assert "❌ Commit target is not the stable branch" in text
    assert "LPB_ALLOW_MAIN_COMMIT" in text
    # Guard fires first: exactly 1 check ran (not the full suite).
    assert text.count("❌") == 1


def test_validate_on_main_outside_hook_is_informational(tmpdir):
    """Plain `lpb-devstack validate` on main (no hook) must NOT hard-fail on
    the guard — validating the stable environment stays possible; the guard
    only warns (commits would still be blocked by the hook)."""
    out, err = io.StringIO(), io.StringIO()
    cons = type(_quiet_console())(color=False, out=out, err=err)
    with mock.patch.object(vmod, "_branch_is_main", return_value="main"), \
         mock.patch.dict(os.environ, {"LPB_ALLOW_MAIN_COMMIT": ""}), \
         mock.patch.object(vmod, "run_server_checks", return_value=[]):
        vmod.cmd_validate("main", cons)
    text = out.getvalue() + err.getvalue()
    assert "on the stable branch: commits to main need the promote exemption" in text
    # The full suite ran (no short-circuit at check 1).
    assert "1/1 checks passed" not in text
    assert "checks passed" in text


def main() -> int:
    return run_lpbx_suite("commit guard (main is promote-only)", globals())


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""localpibox.stack tests: pipeline detection (tag override, env tag),
expected branch/pin, VERSION parse/bump math."""
from __future__ import annotations

from testharness import run_lpbx_suite

import os
from unittest import mock

from localpibox.stack import version as ver_mod
from localpibox.stack.version import (
    bump_version,
    detect_pipeline,
    expected_branch,
    expected_pin_version,
    parse_version,
)


def test_stack_detect_pipeline_tag_override():
    assert detect_pipeline("dev") == "dev"
    assert detect_pipeline("main") == "main"
    assert detect_pipeline(None) in ("dev", "main")


def test_stack_detect_pipeline_env_tag():
    with mock.patch.dict(os.environ, {"LPB_IMAGE_TAG": "main"}, clear=False):
        assert detect_pipeline(None) == "main"


def test_stack_expected_branch():
    # pi is no longer a stack repo (de-forked 2026-08-31 — mainstream pi
    # installs from the npm registry at LPB_PI_VERSION)
    assert expected_branch("pi", "dev") == ""
    assert expected_branch("pi", "main") == ""
    assert expected_branch("devstack", "dev") == "dev"
    assert expected_branch("devstack", "main") == "main"
    assert expected_branch("config", "dev") == "dev"
    assert expected_branch("config", "main") == "main"
    assert expected_branch("nope", "dev") == ""  # not a stack repo


def test_stack_expected_pin_version_main_fallback(tmpdir):
    """dev → local VERSION; main without a devstack clone → strip -dev."""
    root = tmpdir / "devstack"
    root.mkdir()
    (root / "VERSION").write_text("0.1.0-lpb-dev\n")
    with mock.patch.object(ver_mod, "_VERSION_FILE", None), \
         mock.patch.object(ver_mod, "_DEVSTACK_ROOT", root), \
         mock.patch.object(ver_mod, "WORKSPACE_ROOT", tmpdir / "nowhere"):
        assert expected_pin_version("dev") == "0.1.0-lpb-dev"
        assert expected_pin_version("main") == "0.1.0-lpb"


def test_stack_parse_version():
    assert parse_version("0.0.57-lpb-dev") == (0, 0, 57, "-lpb-dev")
    assert parse_version("1.2.3-lpb") == (1, 2, 3, "-lpb")
    assert parse_version("garbage") is None
    assert parse_version("0.0.57") is None  # suffix required


def test_stack_bump_version():
    assert bump_version("0.0.57-lpb-dev") == "0.0.58-lpb-dev"
    assert bump_version("0.0.57-lpb") == "0.0.58-lpb"          # suffix preserved
    assert bump_version("0.0.9-lpb-dev", "minor") == "0.1.0-lpb-dev"
    assert bump_version("0.9.9-lpb", "major") == "1.0.0-lpb"
    for bad, kind in (("nope", "patch"), ("0.0.57", "patch"), ("0.0.1-lpb-dev", "bogus")):
        try:
            bump_version(bad, kind)
            assert False, f"expected ValueError for {bad!r}"
        except ValueError:
            pass





# ─── serverhealth: probe parsing (no network) ─────────────────────────────

from localpibox.stack import serverhealth as sh_mod  # noqa: E402


def test_serverhealth_kv_cache_hit():
    r1 = {"usage": {"prompt_tokens": 63}}
    r2 = {"usage": {"prompt_tokens": 63, "prompt_tokens_details": {"cached_tokens": 59}}}
    with mock.patch.object(sh_mod, "_post", side_effect=[r1, r2]):
        res = sh_mod.check_kv_cache("http://x", "k")
    assert res["ok"] is True, res


def test_serverhealth_kv_cache_miss():
    r1 = {"usage": {"prompt_tokens": 63}}
    r2 = {"usage": {"prompt_tokens": 63}}
    with mock.patch.object(sh_mod, "_post", side_effect=[r1, r2]):
        res = sh_mod.check_kv_cache("http://x", "k")
    assert res["ok"] is False and "re-prefilled" in res["detail"], res


def test_serverhealth_budget_marker_found():
    r = {"choices": [{"message": {"reasoning_content": "", "content": "Budget exceeded. 4"}}]}
    with mock.patch.object(sh_mod, "_post", return_value=r):
        res = sh_mod.check_budget_message("http://x", "k")
    assert res["ok"] is True, res


def test_serverhealth_budget_marker_missing():
    r = {"choices": [{"message": {"reasoning_content": "hmm", "content": "4"}}]}
    with mock.patch.object(sh_mod, "_post", return_value=r):
        res = sh_mod.check_budget_message("http://x", "k")
    assert res["ok"] is False and "--reasoning-budget-message" in res["fix"], res


def test_serverhealth_probe_failure():
    import urllib.error
    with mock.patch.object(sh_mod, "_post", side_effect=urllib.error.URLError("down")):
        res = sh_mod.check_kv_cache("http://x", "k")
    assert res["ok"] is False and "probe failed" in res["detail"], res


def test_serverhealth_run_checks_labels():
    with mock.patch.object(sh_mod, "check_kv_cache", return_value={"ok": True, "detail": "", "fix": ""}), \
         mock.patch.object(sh_mod, "check_budget_message", return_value={"ok": False, "detail": "d", "fix": "f"}):
        checks = sh_mod.run_server_checks("http://x", "k")
    assert len(checks) == 2
    assert "KV/prompt cache" in checks[0]["label"]
    assert "reasoning-budget-full" in checks[1]["label"]

def main() -> int:
    return run_lpbx_suite("localpibox.stack tests", globals())


if __name__ == "__main__":
    raise SystemExit(main())

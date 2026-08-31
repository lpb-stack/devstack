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


def main() -> int:
    return run_lpbx_suite("localpibox.stack tests", globals())


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""support/build.py tests: env loading (success/missing file/missing
var/${HOME} expansion), build-arg mapping from git identity, docker
command shape."""
from __future__ import annotations

from testharness import run_lpbx_suite

import os
import time
import datetime
import build


def _fake_runner(responses):
    """Return a run_cmd-style fn that maps git subcommand -> canned output."""
    def runner(args, timeout=120, cwd=None):
        if args and args[0] == "git":
            sub = args[1]
            out, code = responses.get(sub, ("", 0))
            return out, "", code
        return "", "unexpected cmd", 1
    return runner


def test_build_load_env_success(tmpdir):
    (tmpdir / "lpb.stack.env").write_text(
        "LPB_PI_VERSION=0.84.4\n"
        "LPB_CONFIG_FORK=https://github.com/lpb-stack/config.git\n"
        "LPB_CONFIG_REF=main\n"
        "LPB_IMAGE_CLI=ghcr.io/lpb-stack/devstack:cli\n"
        "LPB_IMAGE_WEB=ghcr.io/lpb-stack/devstack:web\n"
        "LPB_NODE_VERSION=24\n"
        "LPB_VSCODIUM_VERSION=1.126.04524\n"
    )
    (tmpdir / "lpb.conf.env").write_text("LPB_MAX_TOKENS_CONTEXT_RATIO=0.06\n")
    env = build.load_build_env(tmpdir.path)
    assert env["LPB_IMAGE_CLI"] == "ghcr.io/lpb-stack/devstack:cli"
    assert env["LPB_MAX_TOKENS_CONTEXT_RATIO"] == "0.06"


def test_build_load_env_missing_file(tmpdir):
    (tmpdir / "lpb.stack.env").write_text("A=1\n")
    try:
        build.load_build_env(tmpdir.path)
        assert False, "should raise FileNotFoundError"
    except FileNotFoundError as e:
        assert "lpb.conf.env" in str(e)


def test_build_load_env_missing_var(tmpdir):
    for name in ("lpb.stack.env", "lpb.conf.env"):
        (tmpdir / name).write_text("LPB_MAX_TOKENS_CONTEXT_RATIO=0.06\n")
    try:
        build.load_build_env(tmpdir.path)
        assert False, "should raise RuntimeError"
    except RuntimeError as e:
        assert "LPB_IMAGE_CLI" in str(e)


def test_build_load_env_expands_home(tmpdir):
    (tmpdir / "lpb.stack.env").write_text(
        "LPB_IMAGE_CLI=c\nLPB_IMAGE_WEB=w\nLPB_PI_VERSION=v\n"
        "LPB_CONFIG_FORK=c\nLPB_CONFIG_REF=m\nLPB_NODE_VERSION=n\nLPB_VSCODIUM_VERSION=v\n"
    )
    (tmpdir / "lpb.conf.env").write_text(
        "LPB_MAX_TOKENS_CONTEXT_RATIO=0.06\nLPB_STATE_DIR=${HOME}/.lpb-stack/state\n"
    )
    env = build.load_build_env(tmpdir.path)
    assert env["LPB_STATE_DIR"] == os.path.expanduser("~") + "/.lpb-stack/state"


def test_build_build_args_with_fake_git(tmpdir):
    env = {
        "LPB_PI_VERSION": "0.84.4",
        "LPB_CONFIG_FORK": "cfg", "LPB_CONFIG_REF": "main",
        "LPB_NODE_VERSION": "24", "LPB_VSCODIUM_VERSION": "v",
        "LPB_MAX_TOKENS_CONTEXT_RATIO": "0.06",
        "LPB_IMAGE_CLI": "cli", "LPB_IMAGE_WEB": "web",
    }
    (tmpdir / "VERSION").write_text("0.9.9-test\n")
    runner = _fake_runner({
        "ls-remote": ("abc123HEAD...\trefs/tags/v0.84.4\n", 0),
        "rev-parse": ("beef123\n", 0),
    })
    now = datetime.datetime(2026, 8, 10, 12, 0, 0, tzinfo=datetime.timezone.utc)
    args = build.build_args(env, root=tmpdir.path, now=now, runner=runner)
    flat = " ".join(args)
    assert "PI_VERSION=0.84.4" in flat
    assert "PI_HEAD_SHA=abc123HEAD..." in flat
    assert "IMAGE_REVISION=beef123" in flat
    assert "IMAGE_BUILT=2026-08-10T12:00:00Z" in flat
    assert "LPB_VERSION=0.9.9-test" in flat
    assert "--build-arg" in flat


def test_build_build_args_git_fail(tmpdir):
    env = {
        "LPB_PI_VERSION": "0.84.4",
        "LPB_CONFIG_FORK": "cfg", "LPB_CONFIG_REF": "main",
        "LPB_NODE_VERSION": "24", "LPB_VSCODIUM_VERSION": "v",
        "LPB_MAX_TOKENS_CONTEXT_RATIO": "0.06",
        "LPB_IMAGE_CLI": "cli", "LPB_IMAGE_WEB": "web",
    }
    runner = _fake_runner({"ls-remote": ("", 128), "rev-parse": ("", 128)})
    now = datetime.datetime(2026, 8, 10, 12, 0, 0, tzinfo=datetime.timezone.utc)
    args = build.build_args(env, root=tmpdir.path, now=now, runner=runner)
    flat = " ".join(args)
    assert "PI_HEAD_SHA=unknown" in flat
    assert "IMAGE_REVISION=unknown" in flat
    assert "LPB_VERSION=unknown" in flat


def test_build_command_shape(tmpdir):
    cmd = build.build_command("cli", "img:cli", ["--build-arg", "X=1"], root=tmpdir)
    assert cmd[0] == "docker" and cmd[1] == "buildx"
    assert "--target" in cmd and cmd[cmd.index("--target") + 1] == "cli"
    assert "-t" in cmd and cmd[cmd.index("-t") + 1] == "img:cli"
    assert "--platform" in cmd and cmd[cmd.index("--platform") + 1] == "linux/amd64"
    assert cmd[-1] == "."
    assert "--push" not in cmd
    pushed = build.build_command("web", "img:web", [], push=True, root=tmpdir)
    assert pushed[-2:] == ["--push", "."]


def main() -> int:
    return run_lpbx_suite("support/build.py tests", globals())


if __name__ == "__main__":
    raise SystemExit(main())

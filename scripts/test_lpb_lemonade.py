#!/usr/bin/env python3
"""Lemonade first-boot URL/key tests: host passthrough (shell env, lpb.conf.env),
detached-server (--ssh/--web) first-boot prompt (probe → unreachable → user
enters the real host), key prompt with default, and no-prompt cases (URL
reachable, not first boot, non-TTY, foreground CLI mode).

Part of the lpb.py family (run via test_lpb.py or directly)."""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

from testharness import make_module, reset_mock

LEMONADE_ENV_KEYS = ("LPB_LEMONADE_BASE_URL", "LEMONADE_BASE_URL",
                     "LPB_LEMONADE_API_KEY", "LEMONADE_API_KEY")


class _TTYStdin(io.StringIO):
    """Fake TTY stdin feeding piped lines to input().

    If a prompt fires that wasn't expected, input() raises EOFError and the
    test fails loudly instead of hanging."""

    def isatty(self) -> bool:
        return True


def _clean_lemonade_env():
    for k in LEMONADE_ENV_KEYS:
        os.environ.pop(k, None)


def _env_joined(mod) -> str:
    return "\n".join(mod._build_run_env("/home/lpb/workspace"))


def _run(mod, args, stdin: str | None = None):
    """parse + overrides + cmd_run, optionally under a TTY stdin."""
    old = sys.stdin
    if stdin is not None:
        sys.stdin = _TTYStdin(stdin)
    try:
        mod.parse_cli(args)
        mod.apply_overrides()
        mod.cmd_run()
    finally:
        sys.stdin = old


def test_lemonade_first_boot_prompt_unreachable_new_host():
    print("TEST: --ssh first boot, default unreachable → user enters real host")
    reset_mock()
    _clean_lemonade_env()
    mod = make_module()
    mod._conf_cfg = {}  # layout-independent: no conf-provided URL
    mod._url_reachable = lambda url, timeout=3.0: "192.168.0.20" in url
    try:
        _run(mod, ["--ssh", "ssh-ed25519 AAAA k@h"],
             stdin="http://192.168.0.20:13305/v1\nmy-key\n")
    finally:
        _clean_lemonade_env()
    assert mod.cfg.lemonade_base_url == "http://192.168.0.20:13305"
    assert mod.cfg.lemonade_api_key == "my-key"
    env = _env_joined(mod)
    assert "LPB_LEMONADE_BASE_URL=http://192.168.0.20:13305" in env
    assert "LPB_LEMONADE_API_KEY=my-key" in env
    print("  PASS\n")


def test_lemonade_first_boot_prompt_keep_unreachable():
    print("TEST: --ssh first boot, unreachable URL → Enter keeps it, default key")
    reset_mock()
    _clean_lemonade_env()
    mod = make_module()
    mod._conf_cfg = {}
    mod._url_reachable = lambda url, timeout=3.0: False
    try:
        _run(mod, ["--ssh", "ssh-ed25519 AAAA k@h"], stdin="\n\n")
    finally:
        _clean_lemonade_env()
    assert mod.cfg.lemonade_base_url == "http://127.0.0.1:13305"
    assert mod.cfg.lemonade_api_key == "lemonade"  # Enter → default
    print("  PASS\n")


def test_lemonade_first_boot_prompt_reachable_default():
    print("TEST: --ssh first boot, URL reachable → key prompt only")
    reset_mock()
    _clean_lemonade_env()
    mod = make_module()
    mod._conf_cfg = {}
    mod._url_reachable = lambda url, timeout=3.0: True
    try:
        _run(mod, ["--ssh", "ssh-ed25519 AAAA k@h"], stdin="\n")
    finally:
        _clean_lemonade_env()
    assert mod.cfg.lemonade_base_url == "http://127.0.0.1:13305"
    assert mod.cfg.lemonade_api_key == "lemonade"
    print("  PASS\n")


def test_lemonade_env_passthrough_reachable():
    print("TEST: LPB_LEMONADE_* env (LPB_ beats bare), reachable → key prompt only")
    reset_mock()
    _clean_lemonade_env()
    mod = make_module()
    os.environ["LPB_LEMONADE_BASE_URL"] = "http://env-host:13305"
    os.environ["LEMONADE_BASE_URL"] = "http://bare-host:13305"
    os.environ["LPB_LEMONADE_API_KEY"] = "env-key"
    mod._url_reachable = lambda url, timeout=3.0: True
    try:
        _run(mod, ["--ssh", "ssh-ed25519 AAAA k@h"], stdin="\n")  # key prompt
    finally:
        _clean_lemonade_env()
    assert mod.cfg.lemonade_base_url == "http://env-host:13305"
    assert mod.cfg.lemonade_api_key == "env-key"
    env = _env_joined(mod)
    assert "LPB_LEMONADE_BASE_URL=http://env-host:13305" in env
    assert "LPB_LEMONADE_API_KEY=env-key" in env
    print("  PASS\n")


def test_lemonade_conf_env_passthrough_reachable():
    print("TEST: lpb.conf.env LPB_LEMONADE_* (reachable) → normalized passthrough")
    reset_mock()
    _clean_lemonade_env()
    mod = make_module()
    mod._conf_cfg = {"LPB_LEMONADE_BASE_URL": "http://conf-host:13305/v1",
                     "LPB_LEMONADE_API_KEY": "conf-key"}
    mod._url_reachable = lambda url, timeout=3.0: True
    _run(mod, ["--ssh", "ssh-ed25519 AAAA k@h"], stdin="\n")  # key prompt
    _clean_lemonade_env()
    assert mod.cfg.lemonade_base_url == "http://conf-host:13305"  # /v1 stripped
    assert mod.cfg.lemonade_api_key == "conf-key"
    env = _env_joined(mod)
    assert "LPB_LEMONADE_BASE_URL=http://conf-host:13305" in env
    assert "LPB_LEMONADE_API_KEY=conf-key" in env
    print("  PASS\n")


def test_lemonade_no_prompt_not_first_boot():
    print("TEST: .initialized present → no host prompt even on TTY")
    reset_mock()
    _clean_lemonade_env()
    mod = make_module()
    mod._conf_cfg = {}
    marker_dir = Path(os.environ["HOME"]) / ".lpb-stack" / "state" / ".pi"
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker = marker_dir / ".initialized"
    marker.write_text("")
    try:
        _run(mod, ["--ssh", "ssh-ed25519 AAAA k@h"], stdin="")  # EOF if prompt fires
        assert mod.cfg.lemonade_base_url == ""
        assert mod.cfg.lemonade_api_key == ""
        assert "LPB_LEMONADE_BASE_URL" not in _env_joined(mod)
    finally:
        marker.unlink(missing_ok=True)
    print("  PASS\n")


def test_lemonade_no_prompt_non_tty():
    print("TEST: --ssh first boot + non-TTY → no prompt (container log hints instead)")
    reset_mock()
    _clean_lemonade_env()
    mod = make_module()
    mod._conf_cfg = {}
    _run(mod, ["--ssh", "ssh-ed25519 AAAA k@h"])  # harness default: non-TTY stdin
    _clean_lemonade_env()
    assert mod.cfg.lemonade_base_url == ""
    assert mod.cfg.lemonade_api_key == ""
    assert "LPB_LEMONADE_BASE_URL" not in _env_joined(mod)
    print("  PASS\n")


def test_lemonade_no_prompt_cli_mode():
    print("TEST: foreground CLI mode → no host prompt (in-container wizard handles it)")
    reset_mock()
    _clean_lemonade_env()
    mod = make_module()
    mod._conf_cfg = {}
    _run(mod, ["/tmp"], stdin="")  # TTY stdin, EOF if the host prompt fires
    _clean_lemonade_env()
    assert mod.cfg.lemonade_base_url == ""
    print("  PASS\n")


def test_bare_lemonade_url_normalization():
    print("TEST: _bare_lemonade_url strips /v1, /api/v1, adds scheme")
    reset_mock()
    mod = make_module()
    assert mod._bare_lemonade_url("http://h:13305/v1") == "http://h:13305"
    assert mod._bare_lemonade_url("https://h:13305/api/v1/") == "https://h:13305"
    assert mod._bare_lemonade_url("h:13305") == "http://h:13305"
    assert mod._bare_lemonade_url("  ") == ""
    print("  PASS\n")


TESTS = [
    test_bare_lemonade_url_normalization,
    test_lemonade_first_boot_prompt_unreachable_new_host,
    test_lemonade_first_boot_prompt_keep_unreachable,
    test_lemonade_first_boot_prompt_reachable_default,
    test_lemonade_env_passthrough_reachable,
    test_lemonade_conf_env_passthrough_reachable,
    test_lemonade_no_prompt_not_first_boot,
    test_lemonade_no_prompt_non_tty,
    test_lemonade_no_prompt_cli_mode,
]


def main() -> int:
    from testharness import run_lpb_suite
    return run_lpb_suite("lpb.py lemonade first-boot tests", TESTS)


if __name__ == "__main__":
    raise SystemExit(main())

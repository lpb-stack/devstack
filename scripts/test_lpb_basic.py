#!/usr/bin/env python3
"""lpb.py basic tests: lifecycle commands (--help/--config/--stop/
--remove/--logs), lpb run modes (welcome/project/shell/port), URL building.

Part of the lpb.py test suite (entry point: test_lpb.py).
"""
from __future__ import annotations

import os
import sys
import tempfile

from testharness import (
    MOCK_STATE,
    _OutputCapture,
    make_module,
    reset_mock,
    run_lpb_suite,
)

def test_help():
    print("TEST: --help")
    reset_mock()
    mod = make_module()
    mod.parse_cli(["--help"])
    mod.apply_overrides()
    try:
        mod.cmd_help()
    except SystemExit as e:
        assert e.code == 0, f"expected exit 0, got {e.code}"
    print("  PASS\n")


def test_config():
    print("TEST: --config")
    reset_mock()
    mod = make_module()
    mod.parse_cli(["--config"])
    mod.apply_overrides()
    mod.cmd_config()
    print("  PASS\n")


def test_port_invalid():
    print("TEST: --port abc → error")
    reset_mock()
    mod = make_module()
    captured = []
    old_stderr = sys.stderr

    class Cap:
        def write(self, s): captured.append(s)
        def flush(self): pass

    sys.stderr = Cap()
    try:
        mod.parse_cli(["--port", "abc"])
        mod.apply_overrides()
        mod.cmd_run()
        assert False, "should have exited"
    except SystemExit:
        err = "".join(captured)
        assert "integer" in err or "error" in err.lower(), f"wrong error: {err}"
    finally:
        sys.stderr = old_stderr
    print("  PASS\n")


def test_remove():
    """Bare non-interactive --remove must refuse (no silent partial removals)."""
    print("TEST: --remove (non-interactive refusal)")
    reset_mock()
    MOCK_STATE["exists"] = True
    mod = make_module()
    mod.parse_cli(["--remove"])
    mod.apply_overrides()
    with _OutputCapture():
        try:
            mod.cmd_remove()
            assert False, "bare non-interactive --remove must refuse without sections/--yes"
        except mod.DevstackError:
            pass
    assert MOCK_STATE["exists"], "nothing may be removed on refusal"
    print("  PASS\n")


def test_remove_all_yes():
    """--remove all --yes is the scriptable full clean (no prompts)."""
    print("TEST: --remove all --yes (full clean)")
    reset_mock()
    MOCK_STATE["exists"] = True
    with tempfile.TemporaryDirectory() as td:
        state = os.path.join(td, "state")
        browser = os.path.join(td, "browser")
        os.makedirs(os.path.join(state, "agent"))
        os.makedirs(os.path.join(state, "gh-config"))
        os.makedirs(browser)
        mod = make_module()
        mod.parse_cli(["--remove", "all", "--yes"])
        mod.apply_overrides()
        mod.cfg.state_dir = state
        mod.cfg.browser_dir = browser
        with _OutputCapture():
            mod.cmd_remove()
    assert not MOCK_STATE["exists"], "container should be removed"
    assert not os.path.exists(state), "state dir should be removed"
    assert not os.path.exists(browser), "browser dir should be removed"
    print("  PASS\n")


def test_stop_running():
    print("TEST: --stop (running)")
    reset_mock()
    MOCK_STATE["exists"] = True
    MOCK_STATE["running"] = True
    mod = make_module()
    mod.parse_cli(["--stop"])
    mod.apply_overrides()
    mod.cmd_stop()
    assert not MOCK_STATE["running"], "container should be stopped"
    assert not MOCK_STATE["exists"], "container should be removed"
    print("  PASS\n")


def test_stop_not_running():
    print("TEST: --stop (not running)")
    reset_mock()
    mod = make_module()
    mod.parse_cli(["--stop"])
    mod.apply_overrides()
    mod.cmd_stop()
    print("  PASS\n")


def test_logs_missing():
    print("TEST: --logs (no container)")
    reset_mock()
    mod = make_module()
    mod.parse_cli(["--logs"])
    mod.apply_overrides()
    with _OutputCapture():
        try:
            mod.cmd_logs()
        except SystemExit:
            pass
    print("  PASS\n")


def test_unknown_flag():
    print("TEST: --foo-bar → error")
    reset_mock()
    mod = make_module()
    with _OutputCapture():
        try:
            mod.parse_cli(["--foo-bar"])
            mod.apply_overrides()
            mod.cmd_run()
            assert False, "should have raised an error"
        except (SystemExit, mod.DevstackError) as e:
            # DevstackError (new) or SystemExit(code=1) (legacy path)
            if isinstance(e, SystemExit):
                assert e.code == 1
    print("  PASS\n")


def test_run_welcome():
    print("TEST: lpb (no project → welcome)")
    reset_mock()
    mod = make_module()
    mod.parse_cli([])
    mod.apply_overrides()
    mod.cfg.state_dir = "/tmp/lpb-test-state"
    mod.cfg.browser_dir = "/tmp/lpb-test-browser"
    mod.cfg.open_home = True  # simulate no project
    mod.cmd_run()
    assert MOCK_STATE["running"]
    assert mod.cfg.open_home, "should be in home mode"
    print("  PASS\n")


def test_run_project():
    print("TEST: lpb /tmp (project)")
    reset_mock()
    mod = make_module()
    mod.parse_cli(["/tmp"])
    mod.apply_overrides()
    mod.cfg.state_dir = "/tmp/lpb-test-state"
    mod.cfg.browser_dir = "/tmp/lpb-test-browser"
    mod.cmd_run()
    assert MOCK_STATE["running"], "container should be running after start"
    assert mod.cfg.project_name == "tmp", f"expected 'tmp', got '{mod.cfg.project_name}'"
    print("  PASS\n")


def test_run_shell():
    print("TEST: lpb --shell /tmp")
    reset_mock()
    mod = make_module()
    mod.parse_cli(["--shell", "/tmp"])
    mod.apply_overrides()
    mod.cfg.state_dir = "/tmp/lpb-test-state"
    mod.cfg.browser_dir = "/tmp/lpb-test-browser"
    mod.cmd_run()
    assert mod.cfg.shell_mode, "shell_mode should be True"
    # Shell mode must resolve a CLI image (never the web image). The exact
    # tag depends on pin/remote state, so assert the tag family, not equality
    # with the CLI_IMAGE fallback constant.
    assert mod.cfg.image_name.endswith(("-cli", ":dev-cli", ":main-cli", ":latest-cli")), \
        f"expected a CLI image, got {mod.cfg.image_name}"
    assert not mod.cfg.image_name.endswith(("-web", ":web")), \
        f"shell mode must not use the web image: {mod.cfg.image_name}"
    assert mod.cfg.project_name == "tmp", f"expected 'tmp', got '{mod.cfg.project_name}'"
    print("  PASS\n")


def test_run_port_flag():
    print("TEST: lpb --port 9999 /tmp")
    reset_mock()
    mod = make_module()
    mod.parse_cli(["--port", "9999", "/tmp"])
    mod.apply_overrides()
    mod.cfg.state_dir = "/tmp/lpb-test-state"
    mod.cfg.browser_dir = "/tmp/lpb-test-browser"
    mod.cmd_run()
    assert mod.cfg.port == 9999, f"expected 9999, got {mod.cfg.port}"
    assert MOCK_STATE["running"], "container should be running after start"
    print("  PASS\n")


def test_url_from_resolved_config():
    """Critical: _build_urls() must reflect resolved host:port/token."""
    print("TEST: _build_urls() uses resolved host:port from config")
    reset_mock()
    mod = make_module()
    mod.cfg.host = "0.0.0.0"
    mod.cfg.port = 3000
    mod.cfg.token = "mytoken123"
    mod.cfg.without_token = False
    urls = mod._build_urls()
    health = mod._build_url()
    assert ":3000" in health, f"Health URL missing port: {health}"
    assert "mytoken123" in health, f"Health URL missing token: {health}"
    assert len(urls) >= 1, f"Expected at least 1 URL, got {urls}"
    found_port = any(":3000" in u for u in urls.values())
    found_token = any("mytoken123" in u for u in urls.values())
    assert found_port, f"Port not found in URLs: {urls}"
    assert found_token, f"Token not found in URLs: {urls}"
    print(f"  Health URL: {health}")
    print(f"  Display URLs: {urls}")
    print("  PASS\n")


def test_url_without_token():
    print("TEST: URL without --without-token")
    reset_mock()
    mod = make_module()
    mod.cfg.host = "localhost"
    mod.cfg.port = 8080
    mod.cfg.without_token = True
    url = mod._build_url()
    assert "tkn=" not in url, f"URL should not have token: {url}"
    # _build_url converts localhost → 127.0.0.1
    assert "127.0.0.1" in url or "localhost" in url, f"Expected localhost in URL: {url}"
    assert ":8080" in url, f"Expected :8080 in URL: {url}"
    print(f"  URL: {url}")
    print("  PASS\n")


def test_url_from_env_override():
    """Simulate .env setting host=0.0.0.0 port=3000, then verify URLs."""
    print("TEST: _build_urls() reflects .env overrides (host=0.0.0.0 port=3000)")
    reset_mock()
    mod = make_module()
    mod.cfg.host = "0.0.0.0"
    mod.cfg.port = 3000
    mod.cfg.token = "devsession"
    urls = mod._build_urls()
    health = mod._build_url()
    assert ":3000" in health
    assert "devsession" in health
    found_port = any(":3000" in u for u in urls.values())
    assert found_port, f"Port 3000 not in display URLs: {urls}"
    print(f"  Health URL: {health}")
    print(f"  Display URLs: {urls}")
    print("  PASS\n")


TESTS = [
    test_help,
    test_config,
    test_port_invalid,
    test_remove,
    test_remove_all_yes,
    test_stop_running,
    test_stop_not_running,
    test_logs_missing,
    test_unknown_flag,
    test_run_welcome,
    test_run_project,
    test_run_shell,
    test_run_port_flag,
    test_url_from_resolved_config,
    test_url_without_token,
    test_url_from_env_override,
]


def main() -> int:
    return run_lpb_suite("lpb.py basic tests", TESTS)


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Unified initial-setup preflight tests (all launch modes).

The wizard itself is tested in test_localpibox_setup.py; here lpb.py's
preflight is exercised with a FAKE setup module (no real network):

  - the wizard runs before a fresh start in EVERY mode (cli/shell/ssh/web)
    when the provider is missing or broken (health probe)
  - a healthy provider → silent env passthrough from the stored creds
  - wizard success → validated URL/key passed to the container env
  - wizard abort → no container started
  - wizard 'start anyway' → container starts with a visible warning
  - non-TTY / --non-interactive → no wizard, env passthrough + note
  - attaching to a running container never triggers the wizard
  - `lpb setup` and `lpb doctor` commands
  - post-launch status line

Part of the lpb.py family (run via test_lpb.py or directly)."""
from __future__ import annotations

import io
import json
import os
import sys
from types import SimpleNamespace

from testharness import (
    make_module, reset_mock, run_lpb_suite, MOCK_STATE, _OutputCapture,
)

LEMONADE_ENV_KEYS = ("LPB_LEMONADE_BASE_URL", "LEMONADE_BASE_URL",
                     "LPB_LEMONADE_API_KEY", "LEMONADE_API_KEY")


class _TTYStdin(io.StringIO):
    """Fake TTY stdin. The fake wizard consumes no input, so any leftover
    prompt would surface as an unexpected wizard call (loud failure)."""

    def isatty(self) -> bool:
        return True


def _clean_lemonade_env():
    for k in LEMONADE_ENV_KEYS:
        os.environ.pop(k, None)


def _env_joined(mod) -> str:
    return "\n".join(mod._build_run_env("/home/lpb/workspace"))


class FakeSetup:
    """Stands in for localpibox.setup inside lpb.py (no real network)."""

    def __init__(self, status="missing", msg="fake status", wizard=None,
                 creds=None):
        self.status = status
        self.msg = msg
        self.wizard = wizard
        self.creds = creds
        self.calls = []

    def quick_status(self, agent_dir, timeout=3.0):
        self.calls.append(("quick_status", str(agent_dir)))
        return self.status, self.msg

    def lemonade_creds(self, agent_dir):
        return self.creds

    def decode_creds(self, entry):
        p = json.loads(entry["refresh"])
        return p["baseUrl"], p["apiKey"]

    def run_wizard(self, **kw):
        self.calls.append(("run_wizard", kw))
        if callable(self.wizard):
            return self.wizard(**kw)
        return self.wizard


def _wizard_ok(base="http://192.168.0.20:13305", key="wizard-key",
               model="Model-A", warnings=None):
    return SimpleNamespace(ok=True, aborted=False, base_url=base,
                           api_key=key, model=model,
                           warnings=warnings or [], error="", steps=[])


def _run(mod, args, stdin: str | None = None, capture=False):
    """parse + overrides + cmd_run, optionally under a TTY stdin."""
    old = sys.stdin
    if stdin is not None:
        sys.stdin = _TTYStdin(stdin)
    out = _OutputCapture() if capture else None
    try:
        if out:
            out.__enter__()
        mod.parse_cli(args)
        mod.apply_overrides()
        mod.cmd_run()
    finally:
        sys.stdin = old
        if out:
            out.__exit__(None, None, None)
    return "".join(out.out) if out else ""


def _wizard_called(fs: FakeSetup) -> bool:
    return any(c[0] == "run_wizard" for c in fs.calls)


# ─── Wizard runs on first boot in ALL modes ────────────────────────────────

def _test_mode_common(mod_args, fs_status="missing"):
    """One helper asserting wizard + env passthrough for a given arg set."""
    print(f"TEST: first boot (provider {fs_status}) + TTY {mod_args} → wizard")
    reset_mock()
    _clean_lemonade_env()
    mod = make_module()
    fs = FakeSetup(status=fs_status, wizard=_wizard_ok())
    mod.lpb_setup = fs
    try:
        _run(mod, mod_args, stdin="")
    finally:
        _clean_lemonade_env()
    assert _wizard_called(fs), f"wizard not called for {mod_args}"
    env = _env_joined(mod)
    assert "LPB_LEMONADE_BASE_URL=http://192.168.0.20:13305" in env
    assert "LPB_LEMONADE_API_KEY=wizard-key" in env
    print("  PASS\n")


def test_wizard_cli_mode():
    _test_mode_common(["/tmp"], "missing")


def test_wizard_shell_mode():
    _test_mode_common(["--shell"], "missing")


def test_wizard_ssh_mode():
    _test_mode_common(["--ssh", "ssh-ed25519 AAAA k@h"], "missing")


def test_wizard_web_mode():
    print("TEST: first boot (provider missing) + TTY --web → wizard")
    reset_mock()
    _clean_lemonade_env()
    mod = make_module()
    fs = FakeSetup(status="missing", wizard=_wizard_ok())
    mod.lpb_setup = fs
    mod._wait_editor_ready = lambda *a, **k: True   # no real socket probing
    try:
        _run(mod, ["--web"], stdin="")
    finally:
        _clean_lemonade_env()
    assert _wizard_called(fs)
    env = _env_joined(mod)
    assert "LPB_LEMONADE_BASE_URL=http://192.168.0.20:13305" in env
    assert "LPB_LEMONADE_API_KEY=wizard-key" in env
    print("  PASS\n")


def test_wizard_reconfigure_on_broken():
    """A stored-but-broken provider (server down / key rejected) re-runs
    the wizard — the ability to correct a bad configuration."""
    _test_mode_common(["--ssh", "ssh-ed25519 AAAA k@h"], "broken")
    print("TEST: broken provider + TTY → wizard re-runs")


# ─── Healthy provider → silent passthrough ────────────────────────────────

def test_healthy_provider_no_wizard():
    print("TEST: healthy provider → no wizard, env from stored creds")
    reset_mock()
    _clean_lemonade_env()
    mod = make_module()
    creds = {"type": "oauth",
             "refresh": json.dumps({"baseUrl": "http://stored:13305",
                                    "apiKey": "stored-key", "serverName": "stored"}),
             "access": "stored-key", "expires": 0}
    fs = FakeSetup(status="ok", msg="lemonade/Model-A @ http://stored:13305",
                   wizard=_wizard_ok(), creds=creds)
    mod.lpb_setup = fs
    try:
        _run(mod, ["--ssh", "ssh-ed25519 AAAA k@h"], stdin="")
    finally:
        _clean_lemonade_env()
    assert not _wizard_called(fs)
    env = _env_joined(mod)
    assert "LPB_LEMONADE_BASE_URL=http://stored:13305" in env
    assert "LPB_LEMONADE_API_KEY=stored-key" in env
    print("  PASS\n")


# ─── Abort / start-anyway ──────────────────────────────────────────────────

def test_wizard_abort_no_container():
    print("TEST: wizard abort → no container started")
    reset_mock()
    _clean_lemonade_env()
    mod = make_module()
    fs = FakeSetup(status="missing",
                   wizard=SimpleNamespace(ok=False, aborted=True, base_url="",
                                          api_key="", model="", warnings=[],
                                          error=""))
    mod.lpb_setup = fs
    try:
        try:
            _run(mod, ["--ssh", "ssh-ed25519 AAAA k@h"], stdin="")
            raise AssertionError("expected DevstackError")
        except mod.DevstackError:
            pass
    finally:
        _clean_lemonade_env()
    assert not MOCK_STATE["running"], "container must not start after abort"
    print("  PASS\n")


def test_wizard_start_anyway_warns_but_starts():
    print("TEST: wizard 'start anyway' → warning + container starts")
    reset_mock()
    _clean_lemonade_env()
    mod = make_module()
    fs = FakeSetup(status="missing",
                   wizard=SimpleNamespace(ok=False, aborted=False, base_url="",
                                          api_key="", model="",
                                          warnings=["server was NOT validated"],
                                          error=""))
    mod.lpb_setup = fs
    try:
        out = _run(mod, ["--ssh", "ssh-ed25519 AAAA k@h"], stdin="",
                   capture=True)
    finally:
        _clean_lemonade_env()
    assert MOCK_STATE["running"]
    assert "starting anyway" in out.lower()
    assert "NOT validated" in out
    print("  PASS\n")


# ─── Non-TTY / --non-interactive ───────────────────────────────────────────

def test_non_tty_no_wizard_env_passthrough():
    print("TEST: missing + non-TTY → no wizard, env passthrough + note")
    reset_mock()
    _clean_lemonade_env()
    mod = make_module()
    os.environ["LPB_LEMONADE_BASE_URL"] = "http://env:13305/v1"
    os.environ["LPB_LEMONADE_API_KEY"] = "env-key"
    fs = FakeSetup(status="missing", wizard=_wizard_ok())
    mod.lpb_setup = fs
    try:
        out = _run(mod, ["--ssh", "ssh-ed25519 AAAA k@h"], capture=True)
    finally:
        _clean_lemonade_env()
    assert not _wizard_called(fs)
    env = _env_joined(mod)
    assert "LPB_LEMONADE_BASE_URL=http://env:13305/v1" in env
    assert "lpb setup" in out                       # visible pointer to the wizard
    print("  PASS\n")


def test_non_interactive_flag_skips_wizard_on_tty():
    print("TEST: --non-interactive on a TTY → no wizard")
    reset_mock()
    _clean_lemonade_env()
    mod = make_module()
    fs = FakeSetup(status="missing", wizard=_wizard_ok())
    mod.lpb_setup = fs
    try:
        _run(mod, ["--non-interactive", "--ssh", "ssh-ed25519 AAAA k@h"],
             stdin="")
    finally:
        _clean_lemonade_env()
    assert not _wizard_called(fs)
    print("  PASS\n")


# ─── Attach paths never trigger the wizard ─────────────────────────────────

def test_attach_running_container_no_wizard():
    print("TEST: attaching to a running container → no wizard")
    reset_mock()
    _clean_lemonade_env()
    mod = make_module()
    fs = FakeSetup(status="missing", wizard=_wizard_ok())
    mod.lpb_setup = fs
    mod.cfg.container_name = "localpibox"   # harness mock's container name
    MOCK_STATE["running"] = True
    MOCK_STATE["exists"] = True
    try:
        try:
            _run(mod, ["--shell"], stdin="")        # sys.exit(0) on attach
        except SystemExit as e:
            assert e.code in (0, None)
    finally:
        _clean_lemonade_env()
    assert not _wizard_called(fs)
    print("  PASS\n")


# ─── lpb setup / lpb doctor commands ───────────────────────────────────────

def test_cmd_setup_success():
    print("TEST: lpb setup → wizard → exit 0")
    reset_mock()
    _clean_lemonade_env()
    mod = make_module()
    fs = FakeSetup(wizard=_wizard_ok())
    mod.lpb_setup = fs
    try:
        mod.parse_cli(["setup"])
        mod.apply_overrides()
        try:
            mod.cmd_setup()
            raise AssertionError("expected SystemExit(0)")
        except SystemExit as e:
            assert e.code == 0
    finally:
        _clean_lemonade_env()
    assert _wizard_called(fs)
    kw = [c for c in fs.calls if c[0] == "run_wizard"][0][1]
    assert kw["interactive"] is False   # harness stdin is non-TTY
    assert "config_remote" in kw and "config_ref" in kw
    print("  PASS\n")


def test_cmd_setup_failure_exits_1():
    print("TEST: lpb setup → wizard fails → exit 1")
    reset_mock()
    _clean_lemonade_env()
    mod = make_module()
    fs = FakeSetup(wizard=SimpleNamespace(ok=False, aborted=False, base_url="",
                                          api_key="", model="", warnings=[],
                                          error="lemonade server unreachable "
                                                "at http://h:13305: refused"))
    mod.lpb_setup = fs
    try:
        mod.parse_cli(["setup"])
        mod.apply_overrides()
        try:
            mod.cmd_setup()
            raise AssertionError("expected SystemExit(1)")
        except SystemExit as e:
            assert e.code == 1
    finally:
        _clean_lemonade_env()
    print("  PASS\n")


def test_cmd_setup_missing_module():
    print("TEST: lpb setup without the localpibox package → helpful error")
    reset_mock()
    _clean_lemonade_env()
    mod = make_module()
    mod.lpb_setup = None
    try:
        mod.parse_cli(["setup"])
        mod.apply_overrides()
        try:
            mod.cmd_setup()
            raise AssertionError("expected DevstackError")
        except mod.DevstackError:
            pass
    finally:
        _clean_lemonade_env()
    print("  PASS\n")


def test_cmd_doctor():
    print("TEST: lpb doctor → checklist, exit code follows result")
    reset_mock()
    _clean_lemonade_env()
    mod = make_module()

    class DoctorFake:
        def __init__(self, rc):
            self.rc = rc
            self.calls = []

        def doctor(self, agent_dir, cons, timeout=5.0, final_line=True):
            self.calls.append(agent_dir)
            cons.info(f"  ✓ fake doctor line (rc={self.rc})")
            return self.rc

    fake = DoctorFake(0)
    mod.lpb_setup = fake
    mod.parse_cli(["doctor"])
    mod.apply_overrides()
    try:
        with _OutputCapture() as cap:
            try:
                mod.cmd_doctor()
                raise AssertionError("expected SystemExit")
            except SystemExit as e:
                assert e.code == 0
        assert "fake doctor line" in "".join(cap.out)
        assert "container runtime" in "".join(cap.out)
    finally:
        _clean_lemonade_env()

    fake1 = DoctorFake(1)
    mod.lpb_setup = fake1
    try:
        try:
            mod.cmd_doctor()
            raise AssertionError("expected SystemExit")
        except SystemExit as e:
            assert e.code == 1
    finally:
        _clean_lemonade_env()
    print("  PASS\n")


# ─── Post-launch status line ───────────────────────────────────────────────

def test_postlaunch_status_line():
    print("TEST: post-launch summary shows model provider status")
    reset_mock()
    _clean_lemonade_env()
    mod = make_module()
    fs = FakeSetup(status="ok",
                   msg="lemonade/Model-A @ http://192.168.0.20:13305",
                   wizard=_wizard_ok())
    mod.lpb_setup = fs
    mod._wait_editor_ready = lambda *a, **k: True   # no real socket probing
    try:
        out = _run(mod, ["--web"], stdin="", capture=True)
    finally:
        _clean_lemonade_env()
    assert "Model:" in out
    assert "lemonade/Model-A" in out
    print("  PASS\n")


def test_postlaunch_status_missing_warns():
    print("TEST: post-launch status with missing provider → lpb setup pointer")
    reset_mock()
    _clean_lemonade_env()
    mod = make_module()
    fs = FakeSetup(status="missing", msg="no model provider configured",
                   wizard=_wizard_ok())
    mod.lpb_setup = fs
    # wizard returns 'ok' with empty base/key → launcher warns (start-anyway)
    try:
        out = _run(mod, ["--ssh", "ssh-ed25519 AAAA k@h"], stdin="",
                   capture=True)
    finally:
        _clean_lemonade_env()
    assert "lpb setup" in out
    print("  PASS\n")


TESTS = [
    test_wizard_cli_mode,
    test_wizard_shell_mode,
    test_wizard_ssh_mode,
    test_wizard_web_mode,
    test_wizard_reconfigure_on_broken,
    test_healthy_provider_no_wizard,
    test_wizard_abort_no_container,
    test_wizard_start_anyway_warns_but_starts,
    test_non_tty_no_wizard_env_passthrough,
    test_non_interactive_flag_skips_wizard_on_tty,
    test_attach_running_container_no_wizard,
    test_cmd_setup_success,
    test_cmd_setup_failure_exits_1,
    test_cmd_setup_missing_module,
    test_cmd_doctor,
    test_postlaunch_status_line,
    test_postlaunch_status_missing_warns,
]


def main() -> int:
    return run_lpb_suite("lpb.py unified setup preflight tests", TESTS)


if __name__ == "__main__":
    raise SystemExit(main())

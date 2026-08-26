#!/usr/bin/env python3
"""SSH mode tests: key auto-detection from ~/.ssh profile, explicit key
(literal or path), password auth (--ssh-password), env var passthrough.

Part of the lpb.py family (run via test_lpb.py or directly)."""
from __future__ import annotations

import os
import shutil

from testharness import make_module, reset_mock


def _clear_ssh():
    shutil.rmtree(os.path.join(os.environ["HOME"], ".ssh"), ignore_errors=True)


def _ssh_dir():
    d = os.path.join(os.environ["HOME"], ".ssh")
    os.makedirs(d, exist_ok=True)
    return d


def _write_key(name: str) -> str:
    d = _ssh_dir()
    key = f"ssh-ed25519 AAAAC3-{name} {name}@host"
    path = os.path.join(d, name)
    with open(path, "w") as f:
        f.write(key + "\n")
    return path


def test_ssh_explicit_key_literal():
    print("TEST: --ssh <literal key>")
    reset_mock()
    mod = make_module()
    mod.parse_cli(["--ssh", "ssh-ed25519 AAAA literal@host"])
    mod.apply_overrides()
    assert mod.cfg.ssh_mode and mod.cfg.shell_mode
    assert mod.cfg.ssh_pubkey == "ssh-ed25519 AAAA literal@host"
    assert mod.cfg.ssh_password == ""
    print("  PASS\n")


def test_ssh_explicit_key_path():
    print("TEST: --ssh <path to .pub>")
    reset_mock()
    _clear_ssh()
    mod = make_module()
    path = _write_key("id_ed25519.pub")
    mod.parse_cli(["--ssh", path])
    mod.apply_overrides()
    assert mod.cfg.ssh_pubkey == f"ssh-ed25519 AAAAC3-id_ed25519.pub id_ed25519.pub@host"
    print("  PASS\n")


def test_ssh_explicit_key_tilde_path():
    print("TEST: --ssh ~/.ssh/<pub> → tilde expanded, file read")
    reset_mock()
    _clear_ssh()
    mod = make_module()
    _write_key("id_ed25519.pub")
    mod.parse_cli(["--ssh", "~/.ssh/id_ed25519.pub"])
    mod.apply_overrides()
    assert mod.cfg.ssh_pubkey == "ssh-ed25519 AAAAC3-id_ed25519.pub id_ed25519.pub@host"
    assert "~" not in mod.cfg.ssh_pubkey
    print("  PASS\n")


def test_ssh_project_dir_as_value():
    print("TEST: --ssh <project dir> → project dir + profile key selection")
    reset_mock()
    _clear_ssh()
    mod = make_module()
    _write_key("id_ed25519.pub")
    proj = os.path.join(os.environ["HOME"], "myproject")
    os.makedirs(proj, exist_ok=True)
    mod.parse_cli(["--ssh", proj])
    mod.apply_overrides()
    assert mod.cfg.ssh_mode
    assert mod.cfg.project_dir == proj, "dir passed to --ssh must become the project dir"
    # non-TTY + single profile key → auto-used (the prompt shows on a TTY)
    assert mod.cfg.ssh_pubkey == "ssh-ed25519 AAAAC3-id_ed25519.pub id_ed25519.pub@host"
    shutil.rmtree(proj, ignore_errors=True)
    print("  PASS\n")


def test_ssh_invalid_value_errors():
    print("TEST: --ssh <nonexistent path> → error (not silently a key)")
    reset_mock()
    _clear_ssh()
    mod = make_module()
    try:
        mod.parse_cli(["--ssh", os.path.join(os.environ["HOME"], "nowhere", "missing.pub")])
        raise AssertionError("expected DevstackError")
    except mod.DevstackError:
        pass
    print("  PASS\n")


def test_ssh_file_without_key_content_errors():
    print("TEST: --ssh <file that is not a pub key> → error")
    reset_mock()
    _clear_ssh()
    mod = make_module()
    bad = os.path.join(os.environ["HOME"], "not-a-key.pub")
    with open(bad, "w") as f:
        f.write("just some text\n")
    try:
        mod.parse_cli(["--ssh", bad])
        raise AssertionError("expected DevstackError")
    except mod.DevstackError:
        pass
    finally:
        os.unlink(bad)
    print("  PASS\n")


def test_ssh_no_key_no_profile_keys():
    print("TEST: --ssh with no key and empty ~/.ssh → error")
    reset_mock()
    _clear_ssh()
    mod = make_module()
    try:
        mod.parse_cli(["--ssh"])
        raise AssertionError("expected DevstackError")
    except mod.DevstackError:
        pass
    print("  PASS\n")


def test_ssh_auto_single_key():
    print("TEST: --ssh with one profile key → auto-used (non-interactive)")
    reset_mock()
    _clear_ssh()
    mod = make_module()
    _write_key("id_ed25519.pub")
    mod.parse_cli(["--ssh"])
    mod.apply_overrides()
    assert mod.cfg.ssh_pubkey == "ssh-ed25519 AAAAC3-id_ed25519.pub id_ed25519.pub@host"
    print("  PASS\n")


def test_ssh_auto_multiple_keys_noninteractive():
    print("TEST: --ssh with multiple profile keys, no TTY → error")
    reset_mock()
    _clear_ssh()
    mod = make_module()
    _write_key("a.pub")
    _write_key("b.pub")
    try:
        mod.parse_cli(["--ssh"])
        raise AssertionError("expected DevstackError")
    except mod.DevstackError:
        pass
    print("  PASS\n")


def test_ssh_password_flag_random():
    print("TEST: --ssh --ssh-password → random password, no key needed")
    reset_mock()
    mod = make_module()
    mod.parse_cli(["--ssh", "--ssh-password"])
    mod.apply_overrides()
    assert mod.cfg.ssh_mode and mod.cfg.shell_mode
    assert mod.cfg.ssh_pubkey == ""
    assert len(mod.cfg.ssh_password) >= 12
    print("  PASS\n")


def test_ssh_password_value():
    print("TEST: --ssh --ssh-password <pw> → user-chosen password")
    reset_mock()
    mod = make_module()
    mod.parse_cli(["--ssh", "--ssh-password", "hunter2"])
    mod.apply_overrides()
    assert mod.cfg.ssh_password == "hunter2"
    print("  PASS\n")


def test_ssh_password_alone_enables_ssh_mode():
    print("TEST: --ssh-password without --ssh → ssh mode enabled")
    reset_mock()
    mod = make_module()
    mod.parse_cli(["--ssh-password"])
    mod.apply_overrides()
    assert mod.cfg.ssh_mode and mod.cfg.shell_mode
    assert len(mod.cfg.ssh_password) >= 12
    print("  PASS\n")


def test_ssh_key_and_password_combine():
    print("TEST: --ssh <key> --ssh-password <pw> → both")
    reset_mock()
    mod = make_module()
    mod.parse_cli(["--ssh", "ssh-ed25519 AAAA k@h", "--ssh-password", "pw1"])
    mod.apply_overrides()
    assert mod.cfg.ssh_pubkey == "ssh-ed25519 AAAA k@h"
    assert mod.cfg.ssh_password == "pw1"
    print("  PASS\n")


def test_ssh_env_vars_passthrough():
    print("TEST: LPB_SSH_PUBKEY / LPB_SSH_PASSWORD / LPB_SSH_PORT env vars")
    reset_mock()
    mod = make_module()
    mod.parse_cli(["--ssh", "ssh-ed25519 AAAA k@h", "--ssh-password", "pw1"])
    mod.apply_overrides()
    env = mod._build_run_env("/home/lpb/workspace")
    joined = "\n".join(env)
    assert "LPB_SSH_PUBKEY=ssh-ed25519 AAAA k@h" in joined
    assert "LPB_SSH_PASSWORD=pw1" in joined
    assert any(v.startswith("LPB_SSH_PORT=") for v in env)
    print("  PASS\n")


def test_ssh_password_only_env_vars():
    print("TEST: password-only SSH → no LPB_SSH_PUBKEY, port present")
    reset_mock()
    mod = make_module()
    mod.parse_cli(["--ssh-password"])
    mod.apply_overrides()
    env = mod._build_run_env("/home/lpb/workspace")
    assert not any(v.startswith("LPB_SSH_PUBKEY=") for v in env)
    assert any(v.startswith("LPB_SSH_PASSWORD=") for v in env)
    assert any(v.startswith("LPB_SSH_PORT=") for v in env)
    print("  PASS\n")


# ─── sshd launch verification (daemon-mode sshd fails SILENTLY — exit 0 —
#     when it cannot bind the host port; the error goes to syslog which the
#     container has no daemon for. Both sides must therefore verify the
#     port instead of trusting the exit code.) ─────────────────────────────

class _FakeSetupOk:
    """Provider healthy → no wizard during the launch flow."""

    def quick_status(self, agent_dir, timeout=3.0):
        return "ok", "fake ok"

    def lemonade_creds(self, agent_dir):
        return None

    def run_wizard(self, **kw):
        raise AssertionError("wizard should not run (provider ok)")


def _run_ssh_flow(port_in_use: bool, wait_ok: bool):
    """Full --ssh launch (cmd_run) with controlled port checks.

    Returns (captured output, raised DevstackError or None)."""
    from testharness import _OutputCapture
    mod = make_module()
    mod.lpb_setup = _FakeSetupOk()
    mod._port_in_use = lambda port: port_in_use
    mod._wait_ssh_port = lambda port, timeout=20: wait_ok
    out = _OutputCapture()
    raised = None
    out.__enter__()
    try:
        mod.parse_cli(["--ssh", "ssh-ed25519 AAAA k@h"])
        mod.apply_overrides()
        mod.cmd_run()
    except mod.DevstackError as e:
        raised = e
    finally:
        out.__exit__(None, None, None)
    return "".join(out.out), raised


def test_ssh_port_in_use_fails_fast():
    print("TEST: host port busy → refuse start with actionable error (no container)")
    from testharness import MOCK_STATE
    reset_mock()
    out, raised = _run_ssh_flow(port_in_use=True, wait_ok=True)
    assert raised is not None, "expected DevstackError when the host port is busy"
    assert "already in use" in out
    assert "ss -tlnp" in out
    assert MOCK_STATE["running"] is False, "container must not start when the port is busy"
    print("  PASS\n")


def test_ssh_no_false_success_when_port_never_opens():
    print("TEST: sshd never listens → no 'SSH server ready' claim")
    reset_mock()
    out, raised = _run_ssh_flow(port_in_use=False, wait_ok=False)
    assert raised is not None, "must not exit clean when the port never opens"
    assert "SSH server ready" not in out, "must not claim success when the port never opens"
    assert "NOT accepting the SSH protocol" in out
    assert "lpb --stop" in out
    print("  PASS\n")


def test_ssh_ready_only_after_port_opens():
    print("TEST: port opens → 'SSH server ready' shown once")
    reset_mock()
    out, raised = _run_ssh_flow(port_in_use=False, wait_ok=True)
    assert raised is None
    assert "SSH server ready" in out
    assert "listening on port" in out
    print("  PASS\n")


def _first_run_marker():
    """Host-side first-run marker: <state dir>/.initialized (start.sh creates it
    when the in-container bootstrap finishes)."""
    return os.path.join(os.environ["HOME"], ".lpb-stack", "state", ".initialized")


def _run_ssh_flow_timeout(wait_ok: bool, first_run: bool) -> int:
    """Run the --ssh flow and return the timeout _wait_ssh_port got."""
    from testharness import _OutputCapture
    mod = make_module()  # redirects HOME to the isolated test home
    mod.lpb_setup = _FakeSetupOk()
    mod._port_in_use = lambda port: False
    marker = _first_run_marker()
    try:
        if first_run:
            if os.path.exists(marker):
                os.unlink(marker)
        else:
            os.makedirs(os.path.dirname(marker), exist_ok=True)
            open(marker, "w").close()
        captured = {}

        def fake_wait(port, timeout=30):
            captured["timeout"] = timeout
            return wait_ok

        mod._wait_ssh_port = fake_wait
        out = _OutputCapture()
        out.__enter__()
        try:
            mod.parse_cli(["--ssh", "ssh-ed25519 AAAA k@h"])
            mod.apply_overrides()
            mod.cmd_run()
        finally:
            out.__exit__(None, None, None)
        assert captured.get("timeout") is not None, "_wait_ssh_port was not called"
        return captured["timeout"]
    finally:
        if os.path.exists(marker):
            os.unlink(marker)


def test_ssh_first_run_waits_for_bootstrap():
    print("TEST: first run (no .initialized) → long sshd wait (bootstrap)")
    reset_mock()
    t_first = _run_ssh_flow_timeout(wait_ok=True, first_run=True)
    t_warm = _run_ssh_flow_timeout(wait_ok=True, first_run=False)
    assert t_first >= 120, f"first-run wait too short: {t_first}s"
    assert t_warm < t_first, f"warm wait ({t_warm}s) should be shorter than first-run ({t_first}s)"
    print("  PASS\n")


def test_ssh_first_run_failure_hint():
    print("TEST: first-run wait exhausted → hint says the bootstrap may still be running")
    from testharness import _OutputCapture
    reset_mock()
    mod = make_module()  # redirects HOME to the isolated test home
    mod.lpb_setup = _FakeSetupOk()
    mod._port_in_use = lambda port: False
    mod._wait_ssh_port = lambda port, timeout=30: False
    marker = _first_run_marker()
    try:
        if os.path.exists(marker):
            os.unlink(marker)
        out = _OutputCapture()
        out.__enter__()
        raised = None
        try:
            mod.parse_cli(["--ssh", "ssh-ed25519 AAAA k@h"])
            mod.apply_overrides()
            mod.cmd_run()
        except mod.DevstackError:
            raised = "DevstackError"
        finally:
            out.__exit__(None, None, None)
        out_text = "".join(out.out)
        assert raised == "DevstackError", "must fail when the port never opens"
        assert "SSH server ready" not in out_text
        assert "bootstrap" in out_text, "first-run failure must mention the bootstrap"
        assert "lpb --logs" in out_text, "first-run failure must point at the live log"
    finally:
        if os.path.exists(marker):
            os.unlink(marker)
    print("  PASS\n")


TESTS = [
    test_ssh_explicit_key_literal,
    test_ssh_explicit_key_path,
    test_ssh_explicit_key_tilde_path,
    test_ssh_project_dir_as_value,
    test_ssh_invalid_value_errors,
    test_ssh_file_without_key_content_errors,
    test_ssh_no_key_no_profile_keys,
    test_ssh_auto_single_key,
    test_ssh_auto_multiple_keys_noninteractive,
    test_ssh_password_flag_random,
    test_ssh_password_value,
    test_ssh_password_alone_enables_ssh_mode,
    test_ssh_key_and_password_combine,
    test_ssh_env_vars_passthrough,
    test_ssh_password_only_env_vars,
    test_ssh_port_in_use_fails_fast,
    test_ssh_no_false_success_when_port_never_opens,
    test_ssh_ready_only_after_port_opens,
    test_ssh_first_run_waits_for_bootstrap,
    test_ssh_first_run_failure_hint,
]


def main() -> int:
    from testharness import run_lpb_suite
    return run_lpb_suite("lpb.py SSH tests", TESTS)


if __name__ == "__main__":
    raise SystemExit(main())

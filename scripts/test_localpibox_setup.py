#!/usr/bin/env python3
"""localpibox.setup (unified initial setup wizard) tests:
URL normalization, auth-aware server probe, non-interactive + interactive
wizard (incl. the lpb-memory wizard), abort/anyway semantics, persistence
(auth.json/settings.json/lpb-memory-config.json), doctor checklist,
quick_status, config-repo ensure/clone/in-place, template rendering, and
the lpb-config delegation (setup/check/memory setup).

No real network: setup_mod._http_get_json is faked; git remotes are local
bare repos (testharness._bare_remote)."""
from __future__ import annotations

import builtins
import io
import json
import os
import subprocess
import sys
import urllib.error

from testharness import (
    run_lpbx_suite, _quiet_console, _load_script, _bare_remote,
    SCRIPTS_DIR,
)
from localpibox import log as log_mod
from localpibox import setup as setup_mod

lc = _load_script('lpb_config', SCRIPTS_DIR / 'lpb-config')

MODELS = {"data": [
    {"id": "Qwen3.8-27B-GGUF", "name": "Qwen 3.8 27B", "max_context_window": 262144},
    {"id": "qwen3.5-9b-FLM", "name": "Qwen 3.5 9B", "max_context_window": 32768},
]}
HEALTH = {"all_models_loaded": [{"model_name": "qwen3.5-9b-FLM"}]}


def _capture_console():
    out, err = io.StringIO(), io.StringIO()
    cons = log_mod.Console(color=False, out=out, err=err)
    return cons, lambda: out.getvalue() + err.getvalue()


def _http_ok(url, timeout=8.0, api_key=""):
    if url.endswith("/api/v1/models"):
        return MODELS
    if url.endswith("/api/v1/health"):
        return HEALTH
    raise AssertionError(f"unexpected url {url}")


def _http_auth_required(url, timeout=8.0, api_key=""):
    """Models endpoint 401s without a key (the case the old wizard failed on)."""
    if url.endswith("/api/v1/models"):
        if not api_key:
            raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)
        return MODELS
    if url.endswith("/api/v1/health"):
        return HEALTH
    raise AssertionError(f"unexpected url {url}")


def _http_down(url, timeout=8.0, api_key=""):
    raise OSError("urlopen error [Errno 111] Connection refused")


def _http_always_401(url, timeout=8.0, api_key=""):
    raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)


def _make_agent_dir(tmpdir, *, with_repo=False, with_templates=True):
    agent = tmpdir / "agent"
    agent.mkdir()
    if with_templates:
        (agent / "settings.json.template").write_text(json.dumps(
            {"theme": "dark", "packages": ["git:github.com/lpb-stack/lpb-memory@__LPB_VERSION__"]}))
        (agent / "lpb-memory-config.json.template").write_text(json.dumps(
            {"reviewTransport": "subprocess", "memoryMode": "legacy-inject",
             "memoryCharLimit": 3000, "userCharLimit": 3000,
             "failureInjectionMaxEntries": 3}))
        (agent / "mcp.json.template").write_text(json.dumps({
            "settings": {"directTools": False, "idleTimeout": 0},
            "mcpServers": {
                "exa": {"command": "npx", "args": ["-y", "exa-mcp-server"],
                        "env": {"EXA_API_KEY": "${EXA_API_KEY}"}},
                "chrome-devtools": {"command": "chrome-devtools-mcp",
                                    "args": ["--wsEndpoint", "ws://127.0.0.1:9222"],
                                    "enabled": False},
            }}, indent=2))
    (agent / "settings.json").write_text(json.dumps({"theme": "dark"}))
    if with_repo:
        subprocess.run(["git", "-C", str(agent), "init", "-q", "-b", "main"], check=True)
        subprocess.run(["git", "-C", str(agent), "config", "user.email", "t@t"], check=True)
        subprocess.run(["git", "-C", str(agent), "config", "user.name", "t"], check=True)
        subprocess.run(["git", "-C", str(agent), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(agent), "commit", "-qm", "init"], check=True)
    return agent


def _env_setup(url="http://192.168.0.13:13305/v1", key="lemony"):
    os.environ["LEMONADE_BASE_URL"] = url
    os.environ["LEMONADE_API_KEY"] = key


def _env_teardown():
    os.environ.pop("LEMONADE_BASE_URL", None)
    os.environ.pop("LEMONADE_API_KEY", None)


class _TTY:
    """Piped answers for input(); EOFError when exhausted (loud failure)."""
    def __init__(self, answers):
        self.answers = list(answers)

    def __call__(self, prompt=""):
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)


def _run_interactive(answers, **kw):
    """Run the wizard with fake TTY stdin and a patched http layer."""
    orig_input, orig_isatty = builtins.input, sys.stdin.isatty
    fake = _TTY(answers)
    builtins.input = fake
    sys.stdin.isatty = lambda: True
    try:
        return setup_mod.run_wizard(interactive=True, **kw)
    finally:
        builtins.input = orig_input
        sys.stdin.isatty = orig_isatty


# ─── URL normalization ─────────────────────────────────────────────────────

def test_bare_base_url():
    b = setup_mod.bare_base_url
    assert b("http://127.0.0.1:13305/v1") == "http://127.0.0.1:13305"
    assert b("http://192.168.0.13:13305/api/v1/") == "http://192.168.0.13:13305"
    assert b("https://h:8000/api/v0") == "https://h:8000"
    assert b("192.168.0.13:8000") == "http://192.168.0.13:8000"
    assert b("http://h:13305/extra/stuff") == "http://h:13305/extra/stuff"  # only vN suffix stripped
    assert b("") == ""
    print("  PASS\n")


# ─── Probe ─────────────────────────────────────────────────────────────────

def test_probe_ok(tmpdir):
    orig = setup_mod._http_get_json
    setup_mod._http_get_json = _http_ok
    try:
        r = setup_mod.probe_server("http://h:13305/v1", "k")
    finally:
        setup_mod._http_get_json = orig
    assert r.ok and r.reachable and not r.auth_failed
    assert [m["id"] for m in r.models] == ["Qwen3.8-27B-GGUF", "qwen3.5-9b-FLM"]
    assert r.loaded == {"qwen3.5-9b-FLM"}
    print("  PASS\n")


def test_probe_auth_failed_distinct_from_unreachable(tmpdir):
    orig = setup_mod._http_get_json
    setup_mod._http_get_json = _http_auth_required
    try:
        r = setup_mod.probe_server("http://h:13305", "")
        assert not r.ok and r.reachable and r.auth_failed
        assert "API key" in r.error
        r2 = setup_mod.probe_server("http://h:13305", "good-key")
        assert r2.ok
    finally:
        setup_mod._http_get_json = orig
    setup_mod._http_get_json = _http_down
    try:
        r3 = setup_mod.probe_server("http://h:13305", "k")
    finally:
        setup_mod._http_get_json = orig
    assert not r3.reachable and not r3.auth_failed
    assert "refused" in r3.error
    r4 = setup_mod.probe_server("", "k")
    assert not r4.ok and "no server URL" in r4.error
    print("  PASS\n")


# ─── Wizard: non-interactive ───────────────────────────────────────────────

def test_wizard_noninteractive_success(tmpdir):
    remote, _ = _bare_remote(tmpdir, "config", "dev")
    agent = _make_agent_dir(tmpdir)
    orig = setup_mod._http_get_json
    setup_mod._http_get_json = _http_ok
    _env_setup()
    cons, text = _capture_console()
    try:
        r = setup_mod.run_wizard(agent_dir=agent, cons=cons, interactive=False,
                                 config_remote=str(remote), config_ref="dev")
    finally:
        setup_mod._http_get_json = orig
        _env_teardown()
    assert r.ok, text()
    auth = json.loads((agent / "auth.json").read_text())
    refresh = json.loads(auth["lemonade"]["refresh"])
    assert refresh["baseUrl"] == "http://192.168.0.13:13305"  # /v1 stripped
    assert refresh["apiKey"] == "lemony"
    assert auth["lemonade"]["access"] == "lemony"
    settings = json.loads((agent / "settings.json").read_text())
    assert settings["defaultProvider"] == "lemonade"
    assert settings["defaultModel"] == "qwen3.5-9b-FLM"      # loaded model wins
    assert settings["theme"] == "dark"                        # user key preserved
    mem = json.loads((agent / "lpb-memory-config.json").read_text())
    assert mem["llmModelOverride"] == "qwen3.5-9b-FLM"
    assert mem["llmThinkingOverride"] == "off"
    assert (agent / ".git").is_dir()                          # repo cloned by wizard
    print("  PASS\n")


def test_wizard_noninteractive_unreachable_writes_nothing(tmpdir):
    agent = _make_agent_dir(tmpdir, with_repo=True)
    orig = setup_mod._http_get_json
    setup_mod._http_get_json = _http_down
    os.environ.pop("LEMONADE_BASE_URL", None)
    try:
        r = setup_mod.run_wizard(agent_dir=agent, cons=_quiet_console(),
                                 interactive=False)
    finally:
        setup_mod._http_get_json = orig
        os.environ.pop("LEMONADE_API_KEY", None)
    assert not r.ok
    assert "unreachable" in r.error
    assert "lpb setup" in r.error                             # actionable remedy
    assert not (agent / "auth.json").exists()                 # nothing persisted
    print("  PASS\n")


def test_wizard_noninteractive_401_without_key_but_key_in_env(tmpdir):
    """The 0.0.63 killer case: server requires auth for /models; the old
    wizard sent no key and died. The wizard must use the key and succeed."""
    agent = _make_agent_dir(tmpdir, with_repo=True)
    orig = setup_mod._http_get_json
    setup_mod._http_get_json = _http_auth_required
    _env_setup(key="the-right-key")
    try:
        r = setup_mod.run_wizard(agent_dir=agent, cons=_quiet_console(),
                                 interactive=False)
    finally:
        setup_mod._http_get_json = orig
        _env_teardown()
    assert r.ok, r.error
    refresh = json.loads(json.loads((agent / "auth.json").read_text())["lemonade"]["refresh"])
    assert refresh["apiKey"] == "the-right-key"
    print("  PASS\n")


def test_wizard_noninteractive_401_no_key_fails_loudly(tmpdir):
    agent = _make_agent_dir(tmpdir, with_repo=True)
    orig = setup_mod._http_get_json
    setup_mod._http_get_json = _http_always_401
    os.environ.pop("LEMONADE_BASE_URL", None)
    os.environ.pop("LEMONADE_API_KEY", None)
    try:
        r = setup_mod.run_wizard(agent_dir=agent, cons=_quiet_console(),
                                 interactive=False)
    finally:
        setup_mod._http_get_json = orig
    # default key 'lemonade' is sent and rejected → precise auth error
    assert not r.ok
    assert "rejected" in r.error or "401" in r.error or "403" in r.error
    assert not (agent / "auth.json").exists()
    print("  PASS\n")


def test_wizard_noninteractive_repo_failure_still_configures_provider(tmpdir):
    agent = _make_agent_dir(tmpdir)                            # no .git
    (agent / "settings.json").unlink()                         # no settings either
    orig = setup_mod._http_get_json
    setup_mod._http_get_json = _http_ok
    _env_setup()
    try:
        r = setup_mod.run_wizard(agent_dir=agent, cons=_quiet_console(),
                                 interactive=False,
                                 config_remote="file:///nonexistent/config.git",
                                 config_ref="main")
    finally:
        setup_mod._http_get_json = orig
        _env_teardown()
    assert r.ok, r.error
    assert (agent / "auth.json").exists()                      # provider persisted
    assert not (agent / "settings.json").exists()              # model NOT silently faked
    assert any("template" in w for w in r.warnings)
    print("  PASS\n")


# ─── Wizard: interactive ───────────────────────────────────────────────────

def test_wizard_interactive_full(tmpdir):
    agent = _make_agent_dir(tmpdir, with_repo=True)
    orig = setup_mod._http_get_json
    setup_mod._http_get_json = _http_ok
    _env_setup()
    # answers: URL (accept), key (accept), model pick 1,
    #          memory: mode (accept), transport (accept), bg model (accept),
    #          limits: 4000, (accept), (accept),
    #          mcp: exa (accept), chrome-devtools (accept)
    answers = ["", "", "1", "", "", "", "4000", "", "", "", ""]
    try:
        r = _run_interactive(answers, agent_dir=agent, cons=_quiet_console(),
                             config_remote="file:///nonexistent", config_ref="main")
    finally:
        setup_mod._http_get_json = orig
        _env_teardown()
    assert r.ok, r.error
    assert r.model == "Qwen3.8-27B-GGUF"                        # user picked 1
    mem = json.loads((agent / "lpb-memory-config.json").read_text())
    assert mem["memoryCharLimit"] == 4000
    assert mem["llmModelOverride"] == "Qwen3.8-27B-GGUF"
    mcp = json.loads((agent / "mcp.json").read_text())
    assert mcp["mcpServers"]["exa"]["enabled"] is True            # kept, normalized
    assert mcp["mcpServers"]["chrome-devtools"]["enabled"] is False
    print("  PASS\n")


def test_wizard_interactive_invalid_inputs_reprompt(tmpdir):
    """Bad choices / non-integer limits must re-prompt, not crash."""
    agent = _make_agent_dir(tmpdir, with_repo=True)
    orig = setup_mod._http_get_json
    setup_mod._http_get_json = _http_ok
    _env_setup()
    # URL (accept), key (accept), model: 99/x invalid → 2,
    # memory: mode 9 invalid → accept, transport accept, bg model accept,
    # limits: abc invalid → 4000, accept, accept,
    # mcp: exa (accept), chrome-devtools (accept)
    answers = ["", "", "99", "x", "2", "9", "", "", "", "abc", "4000", "", "", "", ""]
    try:
        r = _run_interactive(answers, agent_dir=agent, cons=_quiet_console(),
                             config_remote="file:///nonexistent", config_ref="main")
    finally:
        setup_mod._http_get_json = orig
        _env_teardown()
    assert r.ok, r.error
    mem = json.loads((agent / "lpb-memory-config.json").read_text())
    assert mem["memoryCharLimit"] == 4000
    print("  PASS\n")


def test_wizard_interactive_abort_writes_nothing(tmpdir):
    agent = _make_agent_dir(tmpdir, with_repo=True)
    orig = setup_mod._http_get_json
    setup_mod._http_get_json = _http_ok
    _env_setup()
    try:
        r = _run_interactive(["q"], agent_dir=agent, cons=_quiet_console(),
                             config_remote="file:///nonexistent", config_ref="main")
    finally:
        setup_mod._http_get_json = orig
        _env_teardown()
    assert r.aborted
    assert not r.ok
    assert not (agent / "auth.json").exists()
    print("  PASS\n")


def test_wizard_interactive_anyway_unreachable(tmpdir):
    """'a' continues without validation — flagged in warnings, and the
    container can still start (the host launcher honors this)."""
    agent = _make_agent_dir(tmpdir, with_repo=True)
    orig = setup_mod._http_get_json
    setup_mod._http_get_json = _http_down
    _env_setup()
    # answers: URL 'a' (anyway), model continue (accept),
    #          memory: mode, transport, model, limits x3 (accepts),
    #          mcp: exa (accept), chrome-devtools (accept)
    answers = ["a", "", "", "", "", "", "", "", "", ""]
    try:
        r = _run_interactive(answers, agent_dir=agent, cons=_quiet_console(),
                             config_remote="file:///nonexistent", config_ref="main")
    finally:
        setup_mod._http_get_json = orig
        _env_teardown()
    assert r.ok
    assert any("server was NOT validated" in w for w in r.warnings)
    assert (agent / "auth.json").exists()                       # URL persisted for next run
    refresh = json.loads(json.loads((agent / "auth.json").read_text())["lemonade"]["refresh"])
    assert refresh["baseUrl"] == "http://192.168.0.13:13305"
    print("  PASS\n")


# ─── Memory wizard (standalone) ────────────────────────────────────────────

def test_memory_noninteractive(tmpdir):
    agent = _make_agent_dir(tmpdir)
    cons, text = _capture_console()
    ok = setup_mod.configure_memory(agent, cons, interactive=False,
                                    default_model="qwen3.5-9b-FLM")
    assert ok
    mem = json.loads((agent / "lpb-memory-config.json").read_text())
    assert mem["llmModelOverride"] == "qwen3.5-9b-FLM"
    print("  PASS\n")


def test_memory_without_template_uses_builtin_defaults(tmpdir):
    agent = tmpdir / "agent"
    agent.mkdir()
    cons, text = _capture_console()
    ok = setup_mod.configure_memory(agent, cons, interactive=False)
    assert ok
    mem = json.loads((agent / "lpb-memory-config.json").read_text())
    assert mem["memoryMode"] == "legacy-inject"
    assert mem["reviewTransport"] == "subprocess"
    assert "built-in defaults" in text()
    print("  PASS\n")


def test_memory_prefills_current_values(tmpdir):
    agent = _make_agent_dir(tmpdir)
    (agent / "lpb-memory-config.json").write_text(json.dumps(
        {"memoryMode": "policy-only", "reviewTransport": "direct",
         "memoryCharLimit": 1234}))
    orig_input, orig_isatty = builtins.input, sys.stdin.isatty
    builtins.input = _TTY(["", "", "", "", "", ""])   # all defaults (current values)
    sys.stdin.isatty = lambda: True
    try:
        ok = setup_mod.configure_memory(agent, _quiet_console(), interactive=True)
    finally:
        builtins.input = orig_input
        sys.stdin.isatty = orig_isatty
    assert ok
    mem = json.loads((agent / "lpb-memory-config.json").read_text())
    assert mem["memoryMode"] == "policy-only"       # current value preserved
    assert mem["reviewTransport"] == "direct"
    assert mem["memoryCharLimit"] == 1234
    print("  PASS\n")


# ─── quick_status / doctor ─────────────────────────────────────────────────

def test_mcp_interactive_toggles(tmpdir):
    """Per-server toggles: disable exa, enable chrome-devtools."""
    agent = _make_agent_dir(tmpdir)
    _write_mcp_runtime(agent)
    orig_input = builtins.input
    builtins.input = _TTY(["n", "y"])
    try:
        ok = setup_mod.configure_mcp(agent, _quiet_console(), interactive=True)
    finally:
        builtins.input = orig_input
    assert ok
    data = json.loads((agent / "mcp.json").read_text())
    assert data["mcpServers"]["exa"]["enabled"] is False
    assert data["mcpServers"]["chrome-devtools"]["enabled"] is True
    print("  PASS\n")


def test_mcp_interactive_invalid_then_keep(tmpdir):
    agent = _make_agent_dir(tmpdir)
    _write_mcp_runtime(agent)
    orig_input = builtins.input
    builtins.input = _TTY(["maybe", "", "n"])
    try:
        ok = setup_mod.configure_mcp(agent, _quiet_console(), interactive=True)
    finally:
        builtins.input = orig_input
    assert ok
    data = json.loads((agent / "mcp.json").read_text())
    assert data["mcpServers"]["exa"]["enabled"] is True          # invalid → re-prompt, kept
    assert data["mcpServers"]["chrome-devtools"]["enabled"] is False
    print("  PASS\n")


def test_mcp_abort_writes_nothing(tmpdir):
    agent = _make_agent_dir(tmpdir)
    before = _write_mcp_runtime(agent)
    orig_input = builtins.input
    builtins.input = _TTY(["q"])
    try:
        try:
            setup_mod.configure_mcp(agent, _quiet_console(), interactive=True)
            assert False, "expected AbortError"
        except setup_mod.AbortError:
            pass
    finally:
        builtins.input = orig_input
    assert (agent / "mcp.json").read_text() == before            # untouched
    print("  PASS\n")


def test_mcp_noninteractive_keeps_defaults_no_prompt(tmpdir):
    agent = _make_agent_dir(tmpdir)
    _write_mcp_runtime(agent)
    orig_input = builtins.input

    def _no_input(prompt=""):
        raise AssertionError("non-interactive configure_mcp must not prompt")
    builtins.input = _no_input
    try:
        ok = setup_mod.configure_mcp(agent, _quiet_console(), interactive=False)
    finally:
        builtins.input = orig_input
    assert ok
    data = json.loads((agent / "mcp.json").read_text())
    assert data["mcpServers"]["chrome-devtools"]["enabled"] is False  # defaults untouched
    assert "enabled" not in data["mcpServers"]["exa"]               # no spurious rewrite
    print("  PASS\n")


def test_mcp_missing_file_skips(tmpdir):
    agent = _make_agent_dir(tmpdir, with_templates=False)
    cons, text = _capture_console()
    ok = setup_mod.configure_mcp(agent, cons, interactive=False)
    assert not ok
    assert "mcp.json" in text() and "skipped" in text()
    print("  PASS\n")


def _write_mcp_runtime(agent) -> str:
    """Render mcp.json from the fixture template; returns the text."""
    text = (agent / "mcp.json.template").read_text()
    (agent / "mcp.json").write_text(text)
    return text


def test_quick_status(tmpdir):
    agent = _make_agent_dir(tmpdir, with_repo=True)
    status, msg = setup_mod.quick_status(agent)
    assert status == "missing"

    (agent / "auth.json").write_text(json.dumps({"lemonade": {
        "type": "oauth",
        "refresh": json.dumps({"baseUrl": "http://h:13305", "apiKey": "k",
                              "serverName": "h"}),
        "access": "k", "expires": 0}}))
    (agent / "settings.json").write_text(json.dumps(
        {"defaultProvider": "lemonade", "defaultModel": "qwen3.5-9b-FLM"}))

    orig = setup_mod._http_get_json
    setup_mod._http_get_json = _http_ok
    try:
        status, msg = setup_mod.quick_status(agent)
        assert status == "ok" and "qwen3.5-9b-FLM" in msg
    finally:
        setup_mod._http_get_json = orig

    setup_mod._http_get_json = _http_always_401
    try:
        status, msg = setup_mod.quick_status(agent)
        # stored key is rejected by the server → broken
        assert status == "broken" and "rejected" in msg
    finally:
        setup_mod._http_get_json = orig
    print("  PASS\n")


def test_doctor_healthy_and_broken(tmpdir):
    remote, _ = _bare_remote(tmpdir, "config", "dev")
    agent = _make_agent_dir(tmpdir)
    orig = setup_mod._http_get_json
    setup_mod._http_get_json = _http_ok
    _env_setup()
    try:
        # wizard configures everything first
        r = setup_mod.run_wizard(agent_dir=agent, cons=_quiet_console(),
                                 interactive=False,
                                 config_remote=str(remote), config_ref="dev")
        assert r.ok
        cons, text = _capture_console()
        rc = setup_mod.doctor(agent_dir=agent, cons=cons)
        assert rc == 0, text()
        assert "Setup healthy" in text()
    finally:
        setup_mod._http_get_json = orig
        _env_teardown()

    # now break the server
    setup_mod._http_get_json = _http_down
    try:
        cons, text = _capture_console()
        rc = setup_mod.doctor(agent_dir=agent, cons=cons)
        assert rc == 1
        assert "unreachable" in text()
        assert "lpb setup" in text()                    # actionable remedy
    finally:
        setup_mod._http_get_json = orig
    print("  PASS\n")


def test_doctor_missing_everything(tmpdir):
    agent = tmpdir / "agent"
    cons, text = _capture_console()
    rc = setup_mod.doctor(agent_dir=agent, cons=cons)
    assert rc == 1
    assert "config repo" in text() and "no lemonade credentials" in text()
    print("  PASS\n")


# ─── ensure_config_repo / render_templates ─────────────────────────────────

def test_ensure_config_repo_clone_and_inplace(tmpdir):
    remote, _ = _bare_remote(tmpdir, "config", "dev")
    agent = tmpdir / "agent"
    ok, detail = setup_mod.ensure_config_repo(agent, str(remote), "dev", _quiet_console())
    assert ok and (agent / ".git").is_dir()

    # in-place init: non-empty, untracked runtime file must survive
    agent2 = tmpdir / "agent2"
    agent2.mkdir()
    (agent2 / "auth.json").write_text("{}")
    ok, detail = setup_mod.ensure_config_repo(agent2, str(remote), "dev", _quiet_console())
    assert ok
    assert (agent2 / ".git").is_dir()
    assert (agent2 / "auth.json").is_file()             # untracked state preserved

    # present but broken origin → still usable (local copy)
    subprocess.run(["git", "-C", str(agent), "remote", "set-url",
                    "origin", "file:///nonexistent"], check=True)
    ok, detail = setup_mod.ensure_config_repo(agent, "file:///nonexistent", "dev",
                                              _quiet_console())
    assert ok and "fetch failed" in detail
    print("  PASS\n")


def test_render_templates(tmpdir):
    agent = _make_agent_dir(tmpdir)
    (agent / "settings.json").unlink()
    cons, text = _capture_console()
    ok = setup_mod.render_templates(agent, cons)
    assert ok
    settings = json.loads((agent / "settings.json").read_text())
    assert "__LPB_VERSION__" not in (agent / "settings.json").read_text()
    # mcp.json is now rendered from its template too
    mcp = json.loads((agent / "mcp.json").read_text())
    assert "exa" in mcp["mcpServers"]
    # non-destructive: existing files are never overwritten
    (agent / "settings.json").write_text(json.dumps({"theme": "user"}))
    setup_mod.render_templates(agent, _quiet_console())
    assert json.loads((agent / "settings.json").read_text())["theme"] == "user"
    print("  PASS\n")


# ─── lpb-config delegation ─────────────────────────────────────────────────

def test_lpbconfig_setup_noninteractive_noop_when_configured(tmpdir):
    agent = _make_agent_dir(tmpdir, with_repo=True)
    (agent / "auth.json").write_text(json.dumps({"lemonade": {
        "type": "oauth",
        "refresh": json.dumps({"baseUrl": "http://h:13305", "apiKey": "k",
                              "serverName": "h"}),
        "access": "k", "expires": 0}}))
    cons, text = _capture_console()
    # no http fake in place — a network call would fail the test loudly
    rc = lc.cmd_setup(agent_dir=agent, non_interactive=True, cons=cons)
    assert rc == 0
    assert "already configured" in text()
    print("  PASS\n")


def test_lpbconfig_setup_delegates_to_wizard(tmpdir):
    remote, _ = _bare_remote(tmpdir, "config", "dev")
    agent = _make_agent_dir(tmpdir)
    orig = setup_mod._http_get_json
    setup_mod._http_get_json = _http_ok
    _env_setup()
    os.environ["CONFIG_REMOTE"] = str(remote)
    os.environ["CONFIG_REF"] = "dev"
    try:
        rc = lc.cmd_setup(agent_dir=agent, non_interactive=True,
                          cons=_quiet_console())
    finally:
        setup_mod._http_get_json = orig
        _env_teardown()
        os.environ.pop("CONFIG_REMOTE", None)
        os.environ.pop("CONFIG_REF", None)
    assert rc == 0
    assert (agent / "auth.json").exists()
    settings = json.loads((agent / "settings.json").read_text())
    assert settings["defaultModel"] == "qwen3.5-9b-FLM"
    print("  PASS\n")


def test_lpbconfig_setup_failure_surfaces(tmpdir):
    agent = _make_agent_dir(tmpdir, with_repo=True)
    orig = setup_mod._http_get_json
    setup_mod._http_get_json = _http_down
    os.environ.pop("LEMONADE_BASE_URL", None)
    try:
        cons, text = _capture_console()
        rc = lc.cmd_setup(agent_dir=agent, non_interactive=True, cons=cons)
    finally:
        setup_mod._http_get_json = orig
        os.environ.pop("LEMONADE_API_KEY", None)
    assert rc == 1
    assert "not complete" in text() or "unreachable" in text()
    assert not (agent / "auth.json").exists()
    print("  PASS\n")


def test_lpbconfig_check_runs_doctor(tmpdir):
    agent = _make_agent_dir(tmpdir, with_repo=True)
    cons, text = _capture_console()
    rc = lc.cmd_check(agent_dir=agent, cons=cons)
    assert rc == 1                       # no credentials
    assert "no lemonade credentials" in text()
    print("  PASS\n")


def test_lpbconfig_memory_setup_delegates(tmpdir):
    agent = _make_agent_dir(tmpdir)
    rc = lc.cmd_memory_setup(agent_dir=agent, non_interactive=True,
                             default_model="qwen3.5-9b-FLM", cons=_quiet_console())
    assert rc == 0
    mem = json.loads((agent / "lpb-memory-config.json").read_text())
    assert mem["llmModelOverride"] == "qwen3.5-9b-FLM"
    print("  PASS\n")


def main() -> int:
    return run_lpbx_suite("localpibox.setup (unified setup wizard) tests", globals())


if __name__ == "__main__":
    raise SystemExit(main())

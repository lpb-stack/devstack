#!/usr/bin/env python3
"""scripts/lpb-config tests: legacy layout migration, update
(clone/fast-forward/refuse-dirty), reset (force/abort), status states,
merge."""
from __future__ import annotations

from testharness import run_lpbx_suite, _quiet_console, log_mod, _load_script, SCRIPTS_DIR

import io
import json
import os
import subprocess
from unittest import mock

from localpibox.setup import ProbeResult

lc = _load_script('lpb_config', SCRIPTS_DIR / 'lpb-config')

# NOTE: These tests are stubs for lpb-config features (repo listing,
# mirror management) that are not yet implemented. They are skipped
# until the underlying functions exist.
# ═══════════════════════════════════════════════════════════════════════════



def _setup_git_remote(tmpdir):
    """Create a bare remote + work clone with one commit on 'main'."""
    remote = tmpdir / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    work = tmpdir / "work"
    subprocess.run(["git", "clone", "-q", str(remote), str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(work), "branch", "-M", "main"], check=True)
    (work / "f").write_text("one")
    subprocess.run(["git", "-C", str(work), "add", "."], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-qm", "one"], check=True)
    subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "main"], check=True)
    return remote


def _push_commit(work, msg, content):
    (work / "f").write_text(content)
    subprocess.run(["git", "-C", str(work), "commit", "-qam", msg], check=True)
    subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "main"], check=True)




def test_lpb_config_migrate_legacy_layout(tmpdir):
    pi_root = tmpdir / ".pi"
    pi_root.mkdir()
    (pi_root / ".git").mkdir()
    (pi_root / "auth.json").write_text("{}")
    (pi_root / ".initialized").touch()
    (pi_root / "agent").mkdir()
    cons = _quiet_console()
    lc.migrate_legacy_layout(pi_root, tmpdir / "agent_dir", cons)
    assert (tmpdir / "agent_dir" / "auth.json").exists()
    assert (pi_root / ".initialized").exists()       # marker preserved
    assert (pi_root / "agent").exists()              # subdir preserved
    assert not (pi_root / "auth.json").exists()


def test_lpb_config_migrate_noop_when_already_migrated(tmpdir):
    pi_root = tmpdir / ".pi"
    pi_root.mkdir()
    agent = tmpdir / "agent"
    agent.mkdir()
    (agent / ".git").mkdir()
    cons = _quiet_console()
    lc.migrate_legacy_layout(pi_root, agent, cons)  # must not raise
    assert True


def test_lpb_config_update_clones(tmpdir):
    remote = _setup_git_remote(tmpdir)
    agent = tmpdir / "agent"
    cons = _quiet_console()
    code = lc.cmd_update(agent, str(remote), "main", cons)
    assert code == 0
    assert (agent / "f").exists()


def test_lpb_config_update_fast_forward(tmpdir):
    remote = _setup_git_remote(tmpdir)
    agent = tmpdir / "agent"
    lc.cmd_update(agent, str(remote), "main", _quiet_console())
    _push_commit(tmpdir / "work", "two", "two")
    cons = _quiet_console()
    assert lc.cmd_update(agent, str(remote), "main", cons) == 0
    assert (agent / "f").read_text() == "two"


def test_lpb_config_update_refuses_when_dirty(tmpdir):
    remote = _setup_git_remote(tmpdir)
    agent = tmpdir / "agent"
    lc.cmd_update(agent, str(remote), "main", _quiet_console())
    _push_commit(tmpdir / "work", "two", "two")
    (agent / "local").write_text("keep")  # untracked = dirty
    cons = _quiet_console()
    assert lc.cmd_update(agent, str(remote), "main", cons) == 1
    assert (agent / "local").exists()


def test_lpb_config_reset_force(tmpdir):
    remote = _setup_git_remote(tmpdir)
    agent = tmpdir / "agent"
    lc.cmd_update(agent, str(remote), "main", _quiet_console())
    _push_commit(tmpdir / "work", "two", "two")
    (agent / "junk").write_text("x")
    cons = _quiet_console()
    assert lc.cmd_reset(agent, str(remote), "main", cons, force=True) == 0
    assert not (agent / "junk").exists()
    assert (agent / "f").read_text() == "two"


def test_lpb_config_reset_abort_without_confirmation(tmpdir):
    remote = _setup_git_remote(tmpdir)
    agent = tmpdir / "agent"
    lc.cmd_update(agent, str(remote), "main", _quiet_console())
    (agent / "junk").write_text("x")
    cons = _quiet_console()
    assert lc.cmd_reset(agent, str(remote), "main", cons, force=False, inp=io.StringIO("n\n")) == 0
    assert (agent / "junk").exists()


def test_lpb_config_status_states(tmpdir):
    remote = _setup_git_remote(tmpdir)
    agent = tmpdir / "agent"
    lc.cmd_update(agent, str(remote), "main", _quiet_console())
    out, err = io.StringIO(), io.StringIO()
    cons = log_mod.Console(color=False, out=out, err=err)
    assert lc.cmd_status(agent, str(remote), "main", cons) == 0
    text = out.getvalue() + err.getvalue()
    assert "Current:" in text and "Remote:" in text and "clean" in text
    # no repo yet
    out2, err2 = io.StringIO(), io.StringIO()
    cons2 = log_mod.Console(color=False, out=out2, err=err2)
    assert lc.cmd_status(tmpdir / "nope", str(remote), "main", cons2) == 0
    assert "No config repo" in (out2.getvalue() + err2.getvalue())


def test_lpb_config_merge_uptodate(tmpdir):
    remote = _setup_git_remote(tmpdir)
    agent = tmpdir / "agent"
    lc.cmd_update(agent, str(remote), "main", _quiet_console())
    cons = _quiet_console()
    assert lc.cmd_merge(agent, str(remote), "main", cons) == 0
    # merge when no repo -> error
    assert lc.cmd_merge(tmpdir / "nope", str(remote), "main", _quiet_console()) == 1


# ─── Template rendering (render / auto-render after reset/update/merge) ──


def _make_templates(dir_):
    (dir_ / "settings.json.template").write_text(json.dumps({
        "packages": ["git:github.com/lpb-stack/demo@__LPB_VERSION__", "npm:somepkg"],
        "theme": "dark",
    }))
    (dir_ / "lpb-memory-config.json.template").write_text(json.dumps({
        "reviewTransport": "subprocess",
        "memoryCharLimit": 3000,
    }))


def _push_templates(work):
    _make_templates(work)
    # Mirror the real config repo: rendered runtime files are gitignored,
    # otherwise they'd trip lpb-config's local-change guard.
    (work / ".gitignore").write_text("settings.json\nlpb-memory-config.json\n")
    subprocess.run(["git", "-C", str(work), "add", "."], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-qm", "templates"], check=True)
    subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "main"], check=True)


def _with_lpb_version(value: str):
    """Set LPB_VERSION for the duration of a test; returns restore closure."""
    old = os.environ.get("LPB_VERSION")
    os.environ["LPB_VERSION"] = value

    def _restore():
        if old is None:
            os.environ.pop("LPB_VERSION", None)
        else:
            os.environ["LPB_VERSION"] = old
    return _restore


def _capture_console():
    out, err = io.StringIO(), io.StringIO()
    cons = log_mod.Console(color=False, out=out, err=err)
    return cons, lambda: out.getvalue() + err.getvalue()


def test_render_creates_from_template(tmpdir):
    agent = tmpdir / "agent"
    agent.mkdir()
    _make_templates(agent)
    restore = _with_lpb_version("0.9.9-lpb")
    try:
        cons, text = _capture_console()
        assert lc.cmd_render(agent, cons) == 0
    finally:
        restore()
    text = text()
    raw = (agent / "settings.json").read_text()
    settings = json.loads(raw)
    assert settings["packages"][0] == "git:github.com/lpb-stack/demo@0.9.9-lpb"
    assert "__LPB_VERSION__" not in raw
    mem = json.loads((agent / "lpb-memory-config.json").read_text())
    assert mem["reviewTransport"] == "subprocess"
    assert "Generated" in text


def test_render_no_env_uses_version_file_fallback(tmpdir):
    """With LPB_VERSION unset, falls back to the devstack VERSION file."""
    agent = tmpdir / "agent"
    agent.mkdir()
    _make_templates(agent)
    old = os.environ.pop("LPB_VERSION", None)
    try:
        cons, text = _capture_console()
        assert lc.cmd_render(agent, cons) == 0
    finally:
        if old is not None:
            os.environ["LPB_VERSION"] = old
    raw = (agent / "settings.json").read_text()
    # Must be a real version (no placeholder left, no empty pin)
    assert "__LPB_VERSION__" not in raw
    assert "demo@" in raw


def test_render_nonforce_never_overwrites(tmpdir):
    agent = tmpdir / "agent"
    agent.mkdir()
    _make_templates(agent)
    customized = {
        "packages": ["git:github.com/lpb-stack/demo@0.1.0-lpb", "npm:user-pkg"],
        "theme": "light",
        "defaultProvider": "lemonade",
    }
    (agent / "settings.json").write_text(json.dumps(customized))
    restore = _with_lpb_version("0.2.0-lpb")
    try:
        cons, text = _capture_console()
        assert lc.cmd_render(agent, cons) == 0
    finally:
        restore()
    # untouched file, but stale pins reported
    text = text()
    assert json.loads((agent / "settings.json").read_text()) == customized
    assert "stale" in text and "0.1.0-lpb -> 0.2.0-lpb" in text


def test_render_force_repins_and_keeps_user_keys(tmpdir):
    agent = tmpdir / "agent"
    agent.mkdir()
    _make_templates(agent)
    customized = {
        "packages": ["git:github.com/lpb-stack/demo@0.1.0-lpb", "npm:user-pkg"],
        "theme": "light",
        "defaultProvider": "lemonade",
    }
    (agent / "settings.json").write_text(json.dumps(customized))
    restore = _with_lpb_version("0.2.0-lpb")
    try:
        cons, text = _capture_console()
        assert lc.cmd_render(agent, cons, force=True) == 0
    finally:
        restore()
    s = json.loads((agent / "settings.json").read_text())
    assert s["packages"][0] == "git:github.com/lpb-stack/demo@0.2.0-lpb"  # re-pinned
    assert "npm:user-pkg" in s["packages"]        # user package preserved
    assert s["defaultProvider"] == "lemonade"     # user key preserved
    assert s["theme"] == "dark"                   # template key wins


def test_render_force_memory_local_wins(tmpdir):
    agent = tmpdir / "agent"
    agent.mkdir()
    _make_templates(agent)
    (agent / "lpb-memory-config.json").write_text(json.dumps({
        "reviewTransport": "direct",
        "llmModelOverride": "qwen-x",
    }))
    restore = _with_lpb_version("0.2.0-lpb")
    try:
        cons, text = _capture_console()
        assert lc.cmd_render(agent, cons, force=True) == 0
    finally:
        restore()
    m = json.loads((agent / "lpb-memory-config.json").read_text())
    assert m["reviewTransport"] == "direct"       # local (wizard) wins
    assert m["llmModelOverride"] == "qwen-x"      # wizard key kept
    assert m["memoryCharLimit"] == 3000           # template fills gaps


def test_reset_renders_runtime_config(tmpdir):
    remote = _setup_git_remote(tmpdir)
    _push_templates(tmpdir / "work")
    agent = tmpdir / "agent"
    restore = _with_lpb_version("0.5.5-lpb")
    try:
        lc.cmd_update(agent, str(remote), "main", _quiet_console())
        (agent / "junk").write_text("x")
        cons, text = _capture_console()
        assert lc.cmd_reset(agent, str(remote), "main", cons, force=True) == 0
    finally:
        restore()
    assert not (agent / "junk").exists()
    s = json.loads((agent / "settings.json").read_text())
    assert s["packages"][0] == "git:github.com/lpb-stack/demo@0.5.5-lpb"
    assert (agent / "lpb-memory-config.json").is_file()
    assert "regenerated from templates" in text()


def test_update_restores_missing_rendered(tmpdir):
    remote = _setup_git_remote(tmpdir)
    _push_templates(tmpdir / "work")
    agent = tmpdir / "agent"
    restore = _with_lpb_version("0.6.6-lpb")
    try:
        lc.cmd_update(agent, str(remote), "main", _quiet_console())
        assert (agent / "settings.json").is_file()
        (agent / "settings.json").unlink()
        _push_commit(tmpdir / "work", "bump", "three")
        cons, text = _capture_console()
        assert lc.cmd_update(agent, str(remote), "main", cons) == 0
    finally:
        restore()
    raw = (agent / "settings.json").read_text()
    assert "0.6.6-lpb" in raw
    assert "Generated settings.json" in text()


def test_update_warns_stale_pins_without_clobbering(tmpdir):
    """Stack version moves (new image), rendered file keeps old pins:
    update must warn, not silently overwrite."""
    remote = _setup_git_remote(tmpdir)
    _push_templates(tmpdir / "work")
    agent = tmpdir / "agent"
    restore = _with_lpb_version("0.1.0-lpb")
    try:
        lc.cmd_update(agent, str(remote), "main", _quiet_console())
    finally:
        restore()
    restore2 = _with_lpb_version("0.2.0-lpb")
    try:
        _push_commit(tmpdir / "work", "x", "four")
        cons, text = _capture_console()
        assert lc.cmd_update(agent, str(remote), "main", cons) == 0
    finally:
        restore2()
    assert "stale" in text()
    s = json.loads((agent / "settings.json").read_text())
    assert s["packages"][0].endswith("@0.1.0-lpb")  # untouched


def test_clone_or_init_survives_stale_remote(tmpdir):
    """Partial wipe: leftover .git already has an origin — repoint, don't abort."""
    remote = _setup_git_remote(tmpdir)
    agent = tmpdir / "agent"
    agent.mkdir()
    subprocess.run(["git", "-C", str(agent), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(agent), "remote", "add", "origin",
                    "/old/nonexistent.git"], check=True)
    (agent / "preexisting").write_text("keep me")
    cons, text = _capture_console()
    assert lc.clone_or_init(agent, str(remote), "main", cons) is True
    assert (agent / "f").read_text() == "one"
    r = subprocess.run(
        ["git", "-C", str(agent), "remote", "get-url", "origin"],
        capture_output=True, text=True, check=False)
    assert r.returncode == 0 and r.stdout.strip() == str(remote)
    assert (agent / "preexisting").exists()


def test_reset_warns_on_incomplete_wipe(tmpdir):
    """rmtree that leaves survivors (files in use) must warn, not claim a clean reset."""
    remote = _setup_git_remote(tmpdir)
    agent = tmpdir / "agent"
    lc.cmd_update(agent, str(remote), "main", _quiet_console())
    (agent / "busy").write_text("in use")
    real_rmtree = lc.shutil.rmtree
    lc.shutil.rmtree = lambda *a, **k: None  # simulate failure
    try:
        cons, text = _capture_console()
        assert lc.cmd_reset(agent, str(remote), "main", cons, force=True) == 0
    finally:
        lc.shutil.rmtree = real_rmtree
    assert "Wipe incomplete" in text() and "busy" in text()
    assert (agent / "busy").exists()          # untracked survivor preserved
    assert (agent / "f").read_text() == "one"  # tracked tree still reset


def test_render_missing_dir_errors(tmpdir):
    cons, text = _capture_console()
    assert lc.cmd_render(tmpdir / "nope", cons) == 1
    assert "No config area" in text()


# ─── Runtime settings: show / models / set ──────────────────────────────────

def _make_agent_runtime(tmpdir):
    """Agent dir with rendered runtime files (settings/auth/mcp/memory)."""
    agent = tmpdir / "agent"
    agent.mkdir()
    (agent / "settings.json").write_text(json.dumps({
        "packages": [
            "git:github.com/lpb-stack/lemonade-pi-plugin@0.1.0-lpb-dev",
            "npm:pi-mcp-adapter",
        ],
        "defaultProvider": "lemonade",
        "defaultModel": "ModelA",
        "defaultThinkingLevel": "medium",
    }))
    (agent / "auth.json").write_text(json.dumps({
        "lemonade": {
            "type": "oauth",
            "refresh": json.dumps({"baseUrl": "http://10.0.0.5:13305",
                                   "apiKey": "sekret", "serverName": "10.0.0.5"}),
            "access": "sekret",
        },
    }))
    (agent / "mcp.json").write_text(json.dumps({
        "mcpServers": {"exa": {"enabled": True}, "chrome-devtools": {"enabled": False}},
    }))
    (agent / "lpb-memory-config.json").write_text(json.dumps({
        "memoryMode": "legacy-inject", "memoryCharLimit": 5000, "userCharLimit": 5000,
        "failureInjectionMaxEntries": 5, "failureInjectionMaxAgeDays": 3,
        "llmModelOverride": "small-model",
    }))
    return agent


def _ok_probe(models=("ModelA", "ModelB"), loaded=()):
    return ProbeResult(
        ok=True, reachable=True, auth_failed=False,
        models=[{"id": m, "name": None, "ctx": None} for m in models],
        loaded=set(loaded),
    )


def test_show_human(tmpdir):
    agent = _make_agent_runtime(tmpdir)
    with mock.patch.object(lc, "probe_server", return_value=_ok_probe()):
        cons, text = _capture_console()
        assert lc.cmd_show(agent, cons) == 0
    text = text()
    assert "ModelA" in text
    assert "http://10.0.0.5:13305" in text
    assert "reachable, 2 model(s)" in text
    assert "exa" in text and "chrome-devtools" in text and "disabled" in text
    assert "lemonade-pi-plugin@0.1.0-lpb-dev" in text
    assert "legacy-inject" in text and "small-model" in text
    assert "sekret" not in text  # API key never printed


def test_show_json(tmpdir):
    agent = _make_agent_runtime(tmpdir)
    buf = io.StringIO()
    with mock.patch.object(lc, "probe_server", return_value=_ok_probe()), \
         mock.patch("sys.stdout", buf):
        assert lc.cmd_show(agent, _quiet_console(), as_json=True) == 0
    data = json.loads(buf.getvalue())
    assert data["model"] == "ModelA"
    assert data["provider"] == "lemonade"
    assert data["thinkingLevel"] == "medium"
    assert data["lemonade"]["baseUrl"] == "http://10.0.0.5:13305"
    assert data["lemonade"]["keySet"] is True
    assert data["lemonade"]["reachable"] is True
    assert data["lemonade"]["models"] == 2
    assert data["mcpServers"] == {"exa": True, "chrome-devtools": False}
    assert data["extensionPins"]["lemonade-pi-plugin"] == "0.1.0-lpb-dev"
    assert data["memory"]["mode"] == "legacy-inject"


def test_show_unconfigured(tmpdir):
    agent = tmpdir / "agent"
    agent.mkdir()
    (agent / "settings.json").write_text(json.dumps({"defaultModel": "X"}))
    with mock.patch.object(lc, "probe_server") as probe:
        cons, text = _capture_console()
        assert lc.cmd_show(agent, cons) == 0
    probe.assert_not_called()
    assert "not configured" in text()


def test_models_lists_and_marks_default(tmpdir):
    agent = _make_agent_runtime(tmpdir)
    with mock.patch.object(lc, "probe_server", return_value=_ok_probe(("ModelA", "ModelB"), ("ModelA",))):
        cons, text = _capture_console()
        assert lc.cmd_models(agent, cons) == 0
    text = text()
    assert "ModelA" in text and "ModelB" in text
    assert "← default" in text and "[loaded]" in text


def test_models_unreachable(tmpdir):
    agent = _make_agent_runtime(tmpdir)
    bad = ProbeResult(ok=False, reachable=False, auth_failed=False, error="connection refused")
    with mock.patch.object(lc, "probe_server", return_value=bad):
        cons, text = _capture_console()
        assert lc.cmd_models(agent, cons) == 1
    assert "connection refused" in text()


def test_set_model_writes_and_warns_on_unknown(tmpdir):
    agent = _make_agent_runtime(tmpdir)
    # ModelC is not served by the mocked server → warn, but still write
    with mock.patch.object(lc, "probe_server", return_value=_ok_probe()) as probe:
        cons, text = _capture_console()
        assert lc.cmd_set_model(agent, "ModelC", cons) == 0
    s = json.loads((agent / "settings.json").read_text())
    assert s["defaultModel"] == "ModelC"
    assert s["defaultProvider"] == "lemonade"   # write_default_model sets provider
    assert probe.called and "not in the server's model list" in text()


def test_set_model_valid_no_warning(tmpdir):
    agent = _make_agent_runtime(tmpdir)
    with mock.patch.object(lc, "probe_server", return_value=_ok_probe()):
        cons, text = _capture_console()
        assert lc.cmd_set_model(agent, "ModelB", cons) == 0
    assert json.loads((agent / "settings.json").read_text())["defaultModel"] == "ModelB"
    assert "not in the server's model list" not in text()


def test_set_thinking_preserves_other_keys(tmpdir):
    agent = _make_agent_runtime(tmpdir)
    cons, text = _capture_console()
    assert lc.cmd_set_thinking(agent, "high", cons) == 0
    s = json.loads((agent / "settings.json").read_text())
    assert s["defaultThinkingLevel"] == "high"
    assert s["defaultModel"] == "ModelA"        # untouched
    assert s["packages"]  # untouched


def test_set_thinking_requires_settings(tmpdir):
    agent = tmpdir / "agent"
    agent.mkdir()
    cons, text = _capture_console()
    assert lc.cmd_set_thinking(agent, "high", cons) == 1
    assert "settings.json not found" in text()


def test_set_base_url_preserves_key(tmpdir):
    agent = _make_agent_runtime(tmpdir)
    with mock.patch.object(lc, "probe_server", return_value=_ok_probe()):
        cons, text = _capture_console()
        assert lc.cmd_set_base_url(agent, "http://10.0.0.9:13305/v1", cons) == 0
    auth = json.loads((agent / "auth.json").read_text())
    refresh = json.loads(auth["lemonade"]["refresh"])
    assert refresh["baseUrl"] == "http://10.0.0.9:13305"  # /v1 normalized away
    assert refresh["apiKey"] == "sekret"                   # key preserved


def test_set_api_key_preserves_url(tmpdir):
    agent = _make_agent_runtime(tmpdir)
    with mock.patch.object(lc, "probe_server", return_value=_ok_probe()):
        cons, text = _capture_console()
        assert lc.cmd_set_api_key(agent, "newkey", cons) == 0
    auth = json.loads((agent / "auth.json").read_text())
    refresh = json.loads(auth["lemonade"]["refresh"])
    assert refresh["baseUrl"] == "http://10.0.0.5:13305"   # URL preserved
    assert refresh["apiKey"] == "newkey"


def test_set_api_key_requires_url(tmpdir):
    agent = tmpdir / "agent"
    agent.mkdir()
    (agent / "settings.json").write_text(json.dumps({}))
    cons, text = _capture_console()
    assert lc.cmd_set_api_key(agent, "newkey", cons) == 1
    assert "set base-url" in text()


def main() -> int:
    return run_lpbx_suite("lpb-config tests", globals())


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""localpibox.stack tests: pipeline detection (tag override, env tag),
expected branch/pin, VERSION parse/bump math."""
from __future__ import annotations

from testharness import run_lpbx_suite

import json
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

# ─── cmd_validate: host run vs container run ─────────────────────────────

import contextlib
import io
import subprocess
from localpibox.stack import repos as repos_mod
from localpibox.stack import validate as val_mod
from localpibox.stack import workspace as ws_mod
from localpibox.stack import version as version_mod
from localpibox import log as stack_log_mod


STACK_VERSION = "0.0.1-lpb-dev"


def _devstack_worktree(tmpdir):
    """Main clone on `dev` + a linked worktree on a feature branch (the
    worktree cannot hold `dev` — that is the point of the fixture)."""
    main = tmpdir / "main-clone"
    sig = main / "scripts" / "localpibox" / "stack"
    sig.mkdir(parents=True, exist_ok=True)
    (sig / "workspace.py").write_text("")
    (main / "VERSION").write_text(STACK_VERSION + "\n")
    subprocess.run(["git", "init", "-q", "-b", "dev", str(main)], check=True)
    subprocess.run(["git", "-C", str(main), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(main), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(main), "add", "."], check=True)
    subprocess.run(["git", "-C", str(main), "commit", "-qm", "init"], check=True)
    wt = tmpdir / "workspace" / "molly"
    subprocess.run(["git", "-C", str(main), "worktree", "add", "-q",
                    "-b", "localpibox/molly", str(wt)], check=True)
    return main, wt


def _config_repo(tmpdir, branch="dev", pins=True):
    """Agent dir that is a config repo on *branch*, with rendered pins."""
    agent = tmpdir / "agent"
    agent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", branch, str(agent)], check=True)
    subprocess.run(["git", "-C", str(agent), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(agent), "config", "user.name", "t"], check=True)
    packages = ([f"git:github.com/lpb-stack/lemonade-pi-plugin@{STACK_VERSION}",
                 f"git:github.com/lpb-stack/lpb-memory@{STACK_VERSION}"]
                if pins else [])
    # Real config repo: the rendered files are gitignored (only templates are
    # tracked), so writing them keeps the worktree clean.
    (agent / ".gitignore").write_text("settings.json\nauth.json\ngit/\n")
    subprocess.run(["git", "-C", str(agent), "add", ".gitignore"], check=True)
    (agent / "settings.json").write_text(json.dumps({"packages": packages}))
    subprocess.run(["git", "-C", str(agent), "add", "-f", "settings.json"], check=True)
    subprocess.run(["git", "-C", str(agent), "commit", "-qm", "cfg"], check=True)
    return agent


@contextlib.contextmanager
def _validate_env(tmpdir, ws, agent, container):
    """Point cmd_validate at the tmp stack (devstack worktree + agent dir)."""
    with contextlib.ExitStack() as stack:
        stack.enter_context(mock.patch.multiple(
            val_mod,
            WORKSPACE_ROOT=ws,
            DEFAULT_AGENT_DIR=str(agent),
            WORKSPACE_REPOS=[("devstack", False, False, "dev", "main")],
        ))
        stack.enter_context(mock.patch.multiple(
            ws_mod,
            WORKSPACE_ROOT=ws,                       # where _resolve_repo_path looks
            DEFAULT_AGENT_DIR=str(agent),
            AGENT_GIT=agent / "git" / "github.com" / "lpb-stack",
        ))
        stack.enter_context(mock.patch.object(repos_mod, "_DEVSTACK_ROOT", tmpdir / "elsewhere"))
        stack.enter_context(mock.patch.dict(
            os.environ, {"LPB_STACK_IN_CONTAINER": "1" if container else "0"}))
        stack.enter_context(mock.patch.object(
            version_mod, "_VERSION_FILE", ws / "molly" / "VERSION"))
        stack.enter_context(mock.patch.object(
            val_mod, "get_stack_env",
            lambda *_: {"LPB_PI_VERSION": "1.2.0", "LPB_CONFIG_REF": "dev"}))
        stack.enter_context(mock.patch.object(
            val_mod, "get_stack_env_base", lambda: {"LPB_PI_VERSION": "1.2.0"}))
        yield


def test_validate_host_run_skips_container_checks(tmpdir):
    """Outside a stack container: no config repo/pins/server ❌, only real checks."""
    main, wt = _devstack_worktree(tmpdir)
    ws = tmpdir / "workspace"
    agent = tmpdir / "nope"                    # nothing installed (host reality)
    out, err = io.StringIO(), io.StringIO()
    cons = stack_log_mod.Console(color=False, out=out, err=err)
    with _validate_env(tmpdir, ws, agent, container=False):
        code = val_mod.cmd_validate("dev", cons)
    text = out.getvalue() + err.getvalue()
    assert code == 0, text
    assert "LocalPibox container" in text
    assert "skipped (container-only)" in text
    assert "Config repo exists" not in text     # not failed — just not applicable
    assert "linked worktree" in text


def test_validate_container_run_checks_the_agent_dir(tmpdir):
    """Inside the container the same state IS a failure (config repo missing)."""
    main, wt = _devstack_worktree(tmpdir)
    ws = tmpdir / "workspace"
    agent = tmpdir / "nope"
    out, err = io.StringIO(), io.StringIO()
    cons = stack_log_mod.Console(color=False, out=out, err=err)
    with _validate_env(tmpdir, ws, agent, container=True):
        code = val_mod.cmd_validate("dev", cons)
    text = out.getvalue() + err.getvalue()
    assert code == 1
    assert "Config repo exists" in text


def test_validate_worktree_feature_branch_is_not_a_failure(tmpdir):
    """A worktree on localpibox/molly passes; alignment is the main clone's job."""
    main, wt = _devstack_worktree(tmpdir)
    ws = tmpdir / "workspace"
    agent = _config_repo(tmpdir)
    out, err = io.StringIO(), io.StringIO()
    cons = stack_log_mod.Console(color=False, out=out, err=err)
    with _validate_env(tmpdir, ws, agent, container=True):
        code = val_mod.cmd_validate("dev", cons)
    text = out.getvalue() + err.getvalue()
    assert code == 0, text
    assert "linked worktree" in text
    assert "Pipeline consistency" in text       # config still checked


def test_validate_unreachable_server_skips_instead_of_failing(tmpdir):
    """Server down = one skip, not two hard failures (its documented contract)."""
    from localpibox import setup as setup_mod
    main, wt = _devstack_worktree(tmpdir)
    ws = tmpdir / "workspace"
    agent = _config_repo(tmpdir)
    # auth.json as the plugin stores it: refresh is a JSON *string* carrying
    # the baseUrl/apiKey (see setup.lemonade_creds / decode_creds).
    (agent / "auth.json").write_text(json.dumps({"lemonade": {
        "type": "oauth",
        "refresh": json.dumps({"baseUrl": "http://127.0.0.1:9/v1", "apiKey": "k"}),
        "access": "token"}}))
    probe = setup_mod.ProbeResult(ok=False, reachable=False, auth_failed=False,
                                  error="[Errno 111] Connection refused")
    out, err = io.StringIO(), io.StringIO()
    cons = stack_log_mod.Console(color=False, out=out, err=err)
    with _validate_env(tmpdir, ws, agent, container=True), \
         mock.patch.object(setup_mod, "probe_server", return_value=probe):
        code = val_mod.cmd_validate("dev", cons)
    text = out.getvalue() + err.getvalue()
    assert code == 0, text
    assert "lemonade probes" in text and "unreachable" in text
    assert "KV/prompt cache" not in text        # deep probes not run at all


def main() -> int:
    return run_lpbx_suite("localpibox.stack tests", globals())


if __name__ == "__main__":
    raise SystemExit(main())

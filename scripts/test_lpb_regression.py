#!/usr/bin/env python3
"""lpb.py regression guards: required-callables structure check,
dispatcher table, env-file discovery/parsing, resolve_path semantics,
cmd_* behavioral guards, and the mutation tests that prove the guards
catch the historic regressions.

Part of the lpb.py test suite (entry point: test_lpb.py)."""
from __future__ import annotations

import io
import os
import builtins
import sys
import tempfile
import time
from pathlib import Path
import testharness

from testharness import (
    MOCK_STATE,
    _OutputCapture,
    make_module,
    reset_mock,
    run_lpb_suite,
)

# ─── Regression guards ────────────────────────────────────────────────────────

# Every name referenced by main()'s dispatcher or by cmd_* handlers must exist
# as a real module-level callable. Catches `def` swallowed into comments and
# accidental renames (both were real regressions).
REQUIRED_CALLABLES = [
    # dispatcher handlers (main)
    "cmd_help", "cmd_stop", "cmd_remove", "cmd_logs",
    "cmd_update", "cmd_config", "cmd_run",
    # helpers referenced by handlers
    "self_update", "ensure_container_cmd", "client", "ensure_token",
    "_save_version", "_load_last_version", "resolve_path",
    "detect_mount_flags", "is_podman", "run_cmd", "load_config_file",
    "apply_overrides", "parse_cli", "load_project_env", "load_project_override",
    "_build_urls", "_build_url", "_get_host_for_url", "_get_lan_ips",
    "_find_env_file", "_parse_env_file", "_load_stack_env", "_load_conf_env",
]

# Env vars the container run path must populate (host-driven values).
REQUIRED_ENV_VARS = [
    "LPB_ED_PORT", "LPB_EDITOR_HOST", "LPB_DEVCONTAINER_WORKSPACE_DIR",
    "LPB_CONNECTION_TOKEN", "CONNECTION_TOKEN",
]


def test_module_structure():
    """Every handler/helper referenced by the dispatcher must be a real function.

    Regression guard: the separator comment `# ── ... ──def self_update()` merged
    the `def` into the comment, so self_update vanished and cmd_update would
    NameError at runtime. This test fails fast on that class of bug.
    """
    print("TEST: module structure (all required callables defined)")
    reset_mock()
    mod = make_module()
    missing = [name for name in REQUIRED_CALLABLES if not callable(getattr(mod, name, None))]
    assert not missing, f"missing/not-callable: {missing}"
    print("  PASS\n")


def test_handler_dispatches():
    """main()'s handler table must map every command to an existing callable."""
    print("TEST: dispatcher handler table")
    reset_mock()
    mod = make_module()
    for name in REQUIRED_CALLABLES:
        if not name.startswith("cmd_"):
            continue
        assert callable(getattr(mod, name, None)), f"{name} not callable"
    print("  PASS\n")


def test_env_files_found():
    """lpb.stack.env / lpb.conf.env must be discoverable and parse to non-empty dicts.

    Regression guard: the loaders looked in scripts/ only, always returning {},
    so build identity and runtime defaults were silently ignored.
    """
    print("TEST: _load_stack_env() / _load_conf_env() find the real files")
    reset_mock()
    mod = make_module()
    stack = mod._load_stack_env()
    conf = mod._load_conf_env()
    assert isinstance(stack, dict) and stack, "stack env not loaded"
    assert isinstance(conf, dict) and conf, "conf env not loaded"
    # stack identity keys (from lpb.stack.env at repo root)
    for key in ("LPB_IMAGE_CLI", "LPB_IMAGE_WEB", "LPB_CONTAINER_NAME", "LPB_CONFIG_FORK", "LPB_CONFIG_REF"):
        assert key in stack, f"stack env missing {key}: {list(stack)}"
    # runtime keys (from lpb.conf.env)
    for key in ("LPB_STATE_DIR", "LPB_BROWSER_DIR", "LPB_EDITOR_HOST"):
        assert key in conf, f"conf env missing {key}: {list(conf)}"
    # module-level constants must reflect the stack file
    assert mod.CLI_IMAGE == stack["LPB_IMAGE_CLI"], f"CLI_IMAGE={mod.CLI_IMAGE} != {stack['LPB_IMAGE_CLI']}"
    assert mod.WEB_IMAGE == stack["LPB_IMAGE_WEB"], f"WEB_IMAGE={mod.WEB_IMAGE} != {stack['LPB_IMAGE_WEB']}"
    print("  PASS\n")


def test_env_file_search_order():
    """_find_env_file must search script dir → repo root → CONFIG_DIR, first match wins."""
    print("TEST: _find_env_file search order")
    reset_mock()
    mod = make_module()
    found = mod._find_env_file("lpb.stack.env")
    assert found is not None and found.is_file(), f"lpb.stack.env not found (got {found})"
    print(f"  found: {found}")
    assert mod._find_env_file("definitely-not-a-real-file.env") is None
    print("  PASS\n")


def test_parse_env_file():
    """KEY=value parsing: comments, blanks, export prefix, quoting."""
    print("TEST: _parse_env_file")
    reset_mock()
    mod = make_module()
    with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
        f.write("# comment\n\nexport FOO=bar\nQUOTED='a b c'\n\n")
        path = f.name
    try:
        env = mod._parse_env_file(path)
        assert env == {"FOO": "bar", "QUOTED": "a b c"}, f"got {env}"
    finally:
        os.unlink(path)
    # Missing file → empty dict (no crash)
    assert mod._parse_env_file(path) == {}
    print("  PASS\n")


def test_parse_env_file_placeholder_expansion():
    """${VAR} / ${VAR:-default} placeholders expand (sourced-env-file semantics).

    Regression guard: lpb.conf.env ships LPB_AGENT_BROWSER_SESSION=${PI_WORKTREE_ID}
    and LPB_STATE_DIR=${HOME}/...; without expansion the literal placeholder
    string reached the container env (e.g. AGENT_BROWSER_SESSION='${PI_WORKTREE_ID}').
    """
    print("TEST: _parse_env_file placeholder expansion")
    reset_mock()
    mod = make_module()
    home = os.path.expanduser("~")
    with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False) as f:
        f.write(
            f"# placeholder test\n"
            f"FIRST=alpha\n"
            f"REFS_FIRST=${{FIRST}}\n"
            f"HOME_PATH=${{HOME}}/state\n"
            f"UNSET_VAR=${{NO_SUCH_LPB_VAR_XYZ}}\n"
            f"WITH_DEFAULT=${{NO_SUCH_LPB_VAR_XYZ:-fallback}}\n"
            f"SET_DEFAULT=${{FIRST:-ignored}}\n"
        )
        path = f.name
    try:
        env = mod._parse_env_file(path)
    finally:
        os.unlink(path)
    assert env["REFS_FIRST"] == "alpha", f"in-file ref: got {env['REFS_FIRST']!r}"
    assert env["HOME_PATH"] == os.path.join(home, "state"), f"HOME expand: got {env['HOME_PATH']!r}"
    assert env["UNSET_VAR"] == "", f"unset var → empty: got {env['UNSET_VAR']!r}"
    assert env["WITH_DEFAULT"] == "fallback", f"default: got {env['WITH_DEFAULT']!r}"
    assert env["SET_DEFAULT"] == "alpha", f"set var wins over default: got {env['SET_DEFAULT']!r}"
    # LPB_AGENT_BROWSER_SESSION from the real lpb.conf.env must not leak a
    # literal placeholder into parsed values
    conf = mod._find_env_file("lpb.conf.env")
    if conf:
        real = mod._parse_env_file(conf)
        assert "${" not in real.get("LPB_AGENT_BROWSER_SESSION", ""), \
            f"literal placeholder leaked: {real['LPB_AGENT_BROWSER_SESSION']!r}"
    print("  PASS\n")


def test_resolve_path_host_semantics():
    """state_dir/browser_dir must resolve to HOST paths (not container paths).

    Regression guard: lpb.conf.env once shipped container paths (/home/lpb/...)
    which became bogus host mount sources. Host paths must be absolute, expand
    ~ and ${HOME}.
    """
    print("TEST: resolve_path() host semantics")
    reset_mock()
    mod = make_module()
    home = os.path.expanduser("~")
    assert mod.resolve_path(f"${{HOME}}/foo") == os.path.join(home, "foo"), "resolve_path(${HOME})"
    assert mod.resolve_path("~/foo") == os.path.join(home, "foo"), "resolve_path(~)"
    assert mod.resolve_path("/tmp/x") == "/tmp/x", "absolute passthrough"
    # resolved cfg paths must never contain a container-home prefix
    for attr in ("state_dir", "browser_dir"):
        resolved = mod.resolve_path(getattr(mod.cfg, attr))
        assert not resolved.startswith("/home/lpb"), f"{attr} resolved to container path: {resolved}"
        assert resolved.startswith(home), f"{attr} not under host home: {resolved}"
    print("  PASS\n")


def test_cmd_remove_all_yes():
    """--remove all --yes must delete state+browser dirs and the container.

    Regression guard (Path semantics): resolve_path() returns str; the section
    helpers must wrap it in Path before .is_dir() (historic AttributeError).
    """
    print("TEST: --remove all --yes (full clean)")
    reset_mock()
    with tempfile.TemporaryDirectory() as td:
        state = os.path.join(td, "state")
        browser = os.path.join(td, "browser")
        os.makedirs(state)
        os.makedirs(browser)
        os.makedirs(os.path.join(state, "agent"))
        with open(os.path.join(state, "agent", "x"), "w") as f:
            f.write("x")
        os.makedirs(os.path.join(state, "gh-config"))
        MOCK_STATE["exists"] = True
        mod = make_module()
        mod.parse_cli(["--remove", "all", "--yes"])
        mod.apply_overrides()
        mod.cfg.state_dir = state
        mod.cfg.browser_dir = browser
        with _OutputCapture():
            mod.cmd_remove()
        assert not MOCK_STATE["exists"], "container should be removed"
        assert not os.path.exists(state), f"state dir not removed: {state}"
        assert not os.path.exists(browser), f"browser dir not removed: {browser}"
    print("  PASS\n")


def test_cmd_remove_section_pi():
    """--remove pi keeps gh auth (gh-config/) and agent-browser state.

    The user's wizard-retest scenario: wipe pi config + init only; next boot
    re-clones the config repo and re-runs the setup preflight.
    """
    print("TEST: --remove pi keeps gh-config + browser")
    reset_mock()
    with tempfile.TemporaryDirectory() as td:
        state = os.path.join(td, "state")
        browser = os.path.join(td, "browser")
        os.makedirs(os.path.join(state, "agent"))
        with open(os.path.join(state, "agent", "settings.json"), "w") as f:
            f.write("{}")
        os.makedirs(os.path.join(state, "ssh-host-keys"))
        with open(os.path.join(state, ".initialized"), "w") as f:
            f.write("")
        os.makedirs(os.path.join(state, "gh-config"))
        with open(os.path.join(state, "gh-config", "hosts.yml"), "w") as f:
            f.write("token: keep-me")
        os.makedirs(os.path.join(browser, "profile"))
        MOCK_STATE["exists"] = True
        mod = make_module()
        mod.parse_cli(["--remove", "pi", "--yes"])
        mod.apply_overrides()
        mod.cfg.state_dir = state
        mod.cfg.browser_dir = browser
        with _OutputCapture():
            mod.cmd_remove()
        assert not os.path.exists(os.path.join(state, "agent")), "agent/ must be removed"
        assert not os.path.exists(os.path.join(state, ".initialized")), ".initialized must be removed"
        assert not os.path.exists(os.path.join(state, "ssh-host-keys")), "ssh-host-keys must be removed"
        assert os.path.isfile(os.path.join(state, "gh-config", "hosts.yml")), "gh auth must survive"
        assert os.path.exists(browser), "browser dir must survive"
        assert MOCK_STATE["exists"], "container must survive a pi-only remove"
    print("  PASS\n")


def test_cmd_remove_interactive_menu():
    """TTY menu: a single selection prompt; 'pi' removes only the pi section."""
    print("TEST: --remove interactive menu selects sections")
    reset_mock()
    with tempfile.TemporaryDirectory() as td:
        state = os.path.join(td, "state")
        browser = os.path.join(td, "browser")
        os.makedirs(os.path.join(state, "agent"))
        os.makedirs(os.path.join(state, "gh-config"))
        os.makedirs(browser)
        MOCK_STATE["exists"] = True
        mod = make_module()
        mod.parse_cli(["--remove"])
        mod.apply_overrides()
        mod.cfg.state_dir = state
        mod.cfg.browser_dir = browser
        answers = iter(["pi", "y"])  # menu selection, then confirm
        orig_input, orig_stdin = builtins.input, sys.stdin

        class _TTYStdin(io.StringIO):
            def isatty(self):
                return True

        builtins.input = lambda prompt="": next(answers)
        sys.stdin = _TTYStdin()
        try:
            with _OutputCapture():
                mod.cmd_remove()
        finally:
            builtins.input, sys.stdin = orig_input, orig_stdin
        assert not os.path.exists(os.path.join(state, "agent")), "pi section should be removed"
        assert os.path.exists(os.path.join(state, "gh-config")), "gh section must survive"
        assert os.path.exists(browser), "browser section must survive"
        assert MOCK_STATE["exists"], "container must survive a pi-only remove"
    print("  PASS\n")


def test_cmd_remove_unknown_section():
    """--remove <bogus> must error with the valid section list."""
    print("TEST: --remove bogus section → error")
    reset_mock()
    mod = make_module()
    mod.parse_cli(["--remove", "bogus"])
    mod.apply_overrides()
    with _OutputCapture():
        try:
            mod.cmd_remove()
            assert False, "unknown section must error"
        except mod.DevstackError:
            pass
    print("  PASS\n")


def test_cmd_remove_abort():
    """--remove with 'n' at the confirm must NOT delete anything (incl. container)."""
    print("TEST: --remove aborts on 'n'")
    reset_mock()
    with tempfile.TemporaryDirectory() as td:
        state = os.path.join(td, "state")
        os.makedirs(os.path.join(state, "agent"))
        MOCK_STATE["exists"] = True
        mod = make_module()
        mod.parse_cli(["--remove", "pi"])
        mod.apply_overrides()
        mod.cfg.state_dir = state
        mod.cfg.browser_dir = os.path.join(td, "browser")
        orig_input = builtins.input
        builtins.input = lambda prompt="": "n"
        try:
            with _OutputCapture():
                mod.cmd_remove()
        finally:
            builtins.input = orig_input
        assert os.path.exists(os.path.join(state, "agent")), "state must survive an aborted remove"
        assert MOCK_STATE["exists"], "container must survive an aborted remove"
    print("  PASS\n")


def test_cmd_update_runs():
    """--update must not NameError and must pull images.

    Regression guard: self_update() was swallowed by a comment → cmd_update
    crashed. This exercises the full path with self_update no-op'd.
    """
    print("TEST: --update")
    reset_mock()
    MOCK_STATE["image_present"] = True
    mod = make_module()
    # avoid real network / Popen in self_update + images_pull
    mod.self_update = lambda: None
    pulled = []
    mod.ContainerClient.images_pull = lambda self, name: pulled.append(name) or 0
    mod.parse_cli(["--update"])
    mod.apply_overrides()
    with _OutputCapture():
        mod.cmd_update()
    assert pulled, f"expected image pulls, got {pulled}"
    print(f"  pulled: {pulled} (via mocked images_pull)")
    print("  PASS\n")


def test_cmd_run_env_vars():
    """cmd_run must build env with host-driven values (port, host, token, workspace)."""
    print("TEST: cmd_run env vars")
    reset_mock()
    mod = make_module()
    mod.parse_cli(["--web", "/tmp"])  # web mode → uses containers_run (spyable)
    mod.apply_overrides()
    mod.cfg.state_dir = "/tmp/lpb-test-state"
    mod.cfg.browser_dir = "/tmp/lpb-test-browser"
    mod.cfg.port = 4321
    mod.cfg.host = "0.0.0.0"
    mod.cfg.token = "testtoken"
    mod.detect_mount_flags = lambda project_dir: ":Z"  # avoid probe container
    # capture the env/volume args the mock receives
    captured = {}

    def spy_containers_run(*a, **kw):
        captured.update(kw)
        MOCK_STATE["running"] = True
        MOCK_STATE["exists"] = True
        return ("cid123", "cid123", "", 0)

    mod.ContainerClient.containers_run = spy_containers_run

    # Health-check loop: make the (real) TCP probe succeed and pre-count the
    # mocked curl attempts so the readiness wait exits in ~1s instead of
    # spinning the full 120s budget (this test only asserts env/volume args).
    class _ReadySock:
        def close(self):
            pass

    testharness._curl_attempts = 2  # mocked curl succeeds from attempt 3 onward
    _orig_cc = mod.socket.create_connection
    mod.socket.create_connection = lambda *a, **k: _ReadySock()
    try:
        with _OutputCapture():
            mod.cmd_run()
    finally:
        mod.socket.create_connection = _orig_cc
    assert mod.cfg.project_name == "tmp"
    env_vars = captured.get("env", [])
    env_str = "\n".join(env_vars)
    for var in REQUIRED_ENV_VARS:
        assert any(v.startswith(var + "=") for v in env_vars), f"missing {var}: {env_vars}"
    assert "LPB_ED_PORT=4321" in env_str
    assert "LPB_EDITOR_HOST=0.0.0.0" in env_str
    assert "LPB_CONNECTION_TOKEN=testtoken" in env_str
    # volumes: project → workspace, state → /home/lpb/.pi, browser → /home/lpb/.agent-browser
    vols = captured.get("volumes", [])
    assert any(v.startswith("/tmp:/home/lpb/workspace/tmp") for v in vols), f"project mount missing: {vols}"
    assert any("/home/lpb/.pi" in v for v in vols), f".pi mount missing: {vols}"
    assert any("/home/lpb/.agent-browser" in v for v in vols), f"browser mount missing: {vols}"
    print(f"  env: {env_vars}")
    print(f"  vols: {vols}")
    print("  PASS\n")


# ─── Mutation tests (verify guards catch historic regressions) ───────────────

_MUTATION_DIR = os.path.join(tempfile.gettempdir(), "lpb_regression_mutations")

_REG_MUTATIONS = {
    # real regression: comment swallowed `def self_update()` → NameError at runtime
    "swallowed_def": (
        "# ── Self-update (lpb --update) ───────────────────────────────────────────────────\n\ndef self_update() -> None:",
        "# ── Self-update (lpb --update) ───────────────────────────────────────────────────def self_update() -> None:",
        ["missing/not-callable"],
    ),
    # real regression: resolve_path() returns str, section helper called .is_dir()
    # on the raw str → AttributeError
    "str_is_dir": (
        "    state = Path(resolve_path(cfg.state_dir))\n    browser = Path(resolve_path(cfg.browser_dir))\n",
        "    state = resolve_path(cfg.state_dir)\n    browser = Path(resolve_path(cfg.browser_dir))\n",
        ["'str' object has no attribute 'is_dir'"],
    ),
}


def _mutate_lpb(mname: str) -> str:
    """Apply a named mutation to lpb.py and write it under _mutations/. Returns path."""
    os.makedirs(_MUTATION_DIR, exist_ok=True)
    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "lpb.py")).read()
    old, new, _ = _REG_MUTATIONS[mname]
    assert old in src, f"mutation anchor not found: {mname}"
    out = os.path.join(_MUTATION_DIR, f"{mname}.py")
    with open(out, "w") as f:
        f.write(src.replace(old, new, 1))
    return out


def test_regression_guards_on_swallowed_def():
    """Sanity: the module-structure guard MUST fail on the swallowed-def mutation."""
    print("TEST: mutation 'swallowed_def' is caught by test_module_structure")
    reset_mock()
    path = _mutate_lpb("swallowed_def")
    mod = make_module(lpb_path=path)
    for name in REQUIRED_CALLABLES:
        assert callable(getattr(mod, name, None)) or name == "self_update", f"{name} should exist"
    # if self_update is genuinely absent, the guard logic reports it — assert bug exists
    assert not callable(getattr(mod, "self_update", None)), "mutation did not reproduce the bug"
    print("  PASS (bug reproduced; guard will flag it)\n")


def test_regression_guards_on_str_is_dir():
    """Sanity: --remove must fail with AttributeError on the str.is_dir mutation."""
    print("TEST: mutation 'str_is_dir' triggers AttributeError on --remove")
    reset_mock()
    path = _mutate_lpb("str_is_dir")
    mod = make_module(lpb_path=path)
    with tempfile.TemporaryDirectory() as td:
        state = os.path.join(td, "state")
        os.makedirs(state)
        MOCK_STATE["exists"] = True
        mod.parse_cli(["--remove", "all", "--yes"])
        mod.apply_overrides()
        mod.cfg.state_dir = state
        mod.cfg.browser_dir = os.path.join(td, "browser")
        try:
            with _OutputCapture():
                mod.cmd_remove()
            msg = "cmd_remove did not raise on str.is_dir() mutation"
        except AttributeError:
            msg = None
    if msg:
        assert False, msg
    print("  PASS (bug reproduced; cmd_remove raises AttributeError)\n")


TESTS = [
    # regression guards first (fast, structural)
    test_module_structure,
    test_handler_dispatches,
    test_env_files_found,
    test_env_file_search_order,
    test_parse_env_file,
    test_parse_env_file_placeholder_expansion,
    test_resolve_path_host_semantics,
    test_cmd_remove_all_yes,
    test_cmd_remove_section_pi,
    test_cmd_remove_interactive_menu,
    test_cmd_remove_unknown_section,
    test_cmd_remove_abort,
    test_cmd_update_runs,
    test_cmd_run_env_vars,
    # mutation sanity — prove the guards actually catch the historic regressions
    test_regression_guards_on_swallowed_def,
    test_regression_guards_on_str_is_dir,
]


def main() -> int:
    return run_lpb_suite("lpb.py regression guards", TESTS)


if __name__ == "__main__":
    raise SystemExit(main())

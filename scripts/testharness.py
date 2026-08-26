#!/usr/bin/env python3
"""Shared test plumbing for the scripts/test_*.py suites.

lpb.py family (test_lpb.py + test_lpb_*.py):
  - mock_podman / mock_run / mock_which — podman/docker + curl mocks
  - MOCK_STATE, reset_mock()
  - _OutputCapture — stdout/stderr/logger capture
  - make_module() — import a fresh, mocked lpb.py (isolated HOME)
  - run_lpb_suite(name, tests) — runner (reset_mock before each test)

localpibox family (test_localpibox.py + test_localpibox_*.py):
  - SCRIPTS_DIR / SUPPORT_DIR + sys.path setup
  - _load_script() — load extensionless support scripts as modules
  - _quiet_console() — silent log_mod.Console for tool invocations
  - _TmpDir — Path with .path alias
  - run_lpbx_suite(name, ns) — auto-discover test_* in ns, one tmpdir each
"""
from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
import inspect
import io
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ── Path setup (all suites import from scripts/ + support/) ──────────────
SCRIPTS_DIR = Path(__file__).resolve().parent
DEVSTACK_DIR = SCRIPTS_DIR.parent
SUPPORT_DIR = DEVSTACK_DIR / "support"

for _d in (str(SCRIPTS_DIR), str(SUPPORT_DIR)):
    if _d not in sys.path:
        sys.path.insert(0, _d)

from localpibox import log as log_mod  # noqa: E402

# ─── Mock podman/docker ──────────────────────────────────────────────────────

MOCK_STATE = {
    "running": False,
    "exists": False,
    "logs": [],
    "image_present": True,
    "interactive": False,  # whether -it was requested
}

_curl_attempts = 0


def mock_podman(args):
    """Mock podman CLI. Stores stdout in MOCK_STATE['_stdout']."""
    MOCK_STATE['_stdout'] = ''
    if len(args) < 2:
        return 0
    cmd = args[1]

    if cmd == "ps":
        has_format = False
        for i, a in enumerate(args):
            if a == "-a":
                MOCK_STATE["exists"] = True
            if a == "--format":
                has_format = True
        names = []
        if MOCK_STATE["exists"] and MOCK_STATE["running"]:
            names = ["localpibox"]
        if has_format:
            MOCK_STATE['_stdout'] = "\n".join(names)
        else:
            if names:
                MOCK_STATE['_stdout'] = "localpibox  Up  ..."
        return 0

    if cmd == "stop":
        if not MOCK_STATE["running"]:
            return 1
        MOCK_STATE["running"] = False
        return 0

    if cmd == "rm":
        if not MOCK_STATE["exists"]:
            return 1
        MOCK_STATE["exists"] = False
        MOCK_STATE["running"] = False
        return 0

    if cmd == "image":
        if len(args) > 2 and args[2] == "inspect":
            return 0 if MOCK_STATE["image_present"] else 1

    if cmd == "pull":
        MOCK_STATE["image_present"] = True
        return 0

    if cmd == "run":
        if MOCK_STATE["running"] and MOCK_STATE["exists"]:
            return 125
        MOCK_STATE["running"] = True
        MOCK_STATE["exists"] = True
        MOCK_STATE["interactive"] = "-it" in args
        MOCK_STATE['_stdout'] = "abc123def456"
        return 0

    if cmd == "logs":
        if MOCK_STATE["exists"]:
            return 0
        return 1

    if cmd == "exec":
        if MOCK_STATE["running"]:
            MOCK_STATE["interactive"] = True
            return 0
        return 1

    return 0


def mock_run(args, **kwargs):
    global _curl_attempts
    if len(args) >= 2 and args[0] in ("podman", "docker"):
        result = mock_podman(args)
        class R:
            stdout = MOCK_STATE.get('_stdout', '')
            stderr = ""
            returncode = result
        return R()
    if "curl" in args:
        _curl_attempts += 1
        if _curl_attempts >= 3:
            class R:
                stdout = "<html></html>"
                stderr = ""
                returncode = 0
            return R()
        class R:
            stdout = ""
            stderr = ""
            returncode = 7
        return R()
    # Real subprocess.run for everything else
    return _subprocess_orig(args, **kwargs)


def mock_which(name):
    if name in ("podman", "docker"):
        return "podman"  # return bare command name, not path
    if name == "curl":
        return "curl"
    return _shutil_orig(name)


def reset_mock():
    """Reset all mock state for a fresh test."""
    MOCK_STATE["running"] = False
    MOCK_STATE["exists"] = False
    MOCK_STATE["image_present"] = True
    MOCK_STATE["interactive"] = False
    MOCK_STATE["_stdout"] = ""
    sys.stdin = _NonTTYStdin()
    global _curl_attempts
    _curl_attempts = 0


_module_counter = 0
_subprocess_orig = None
_shutil_orig = None
_ISOLATED_HOME = tempfile.mkdtemp(prefix="lpb_test_home_")


class _NonTTYStdin(io.StringIO):
    """Deterministic non-interactive stdin for lpb.py tests.

    Interactive prompts (SSH key selection, first-boot lemonade URL) gate on
    sys.stdin.isatty(); defaulting to False keeps the suite from hanging when
    run inside a terminal. Prompt tests swap in their own TTY fake.
    """

    def isatty(self) -> bool:
        return False


class _OutputCapture:
    """Redirect stdout+stderr to a sink while active (suppresses expected noise).

    Also redirects the root logger's handler streams — lpb.py's err()/warn()
    write through logger.error/logger.warning, and the logger handler captured
    the original sys.stderr at import time, so it would bypass a plain
    sys.stderr swap.
    """

    def __init__(self):
        self.out = []
        self._old = None
        self._old_streams = None

    def write(self, s):
        self.out.append(s)

    def flush(self):
        pass

    def isatty(self):
        # Duck-typed stream: some helpers (localpibox.log.Console) probe
        # isatty() to decide on color — a capture sink is never a TTY.
        return False

    def __enter__(self):
        self._old = (sys.stdout, sys.stderr)
        sys.stdout, sys.stderr = self, self
        self._old_streams = [h.stream for h in logging.getLogger().handlers]
        for h in logging.getLogger().handlers:
            h.stream = self
        return self

    def __exit__(self, *exc):
        sys.stdout, sys.stderr = self._old
        for h, stream in zip(logging.getLogger().handlers, self._old_streams):
            h.stream = stream
        return False


def make_module(lpb_path: str | None = None):
    """Import lpb.py with all mocks in place. Each call gets unique module name.

    lpb_path: override which lpb source file to import (used by regression
    mutation tests to load a deliberately-broken copy).

    HOME is redirected to a private temp dir, so the module never touches the
    real ~/.lpb-stack (token / last-project / last-image / state defaults).
    """
    global _module_counter, _subprocess_orig, _shutil_orig
    _module_counter += 1
    # Save originals ONCE (first call only)
    if _subprocess_orig is None:
        _subprocess_orig = subprocess.run
        _shutil_orig = shutil.which

    # Apply mocks BEFORE importing lpb
    subprocess.run = mock_run
    shutil.which = mock_which

    # Remove LPB_* env vars so tests get clean defaults, and point HOME at a
    # private temp dir so the module's default paths are isolated per run.
    for key in list(os.environ.keys()):
        if key.startswith("LPB_"):
            os.environ.pop(key, None)
    os.environ["HOME"] = _ISOLATED_HOME

    # Use unique name to bypass sys.modules cache
    mod_name = f"lpb_test_{_module_counter}_{id(make_module)}"
    if lpb_path is None:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        lpb_path = os.path.join(script_dir, "lpb.py")
    spec = importlib.util.spec_from_file_location(mod_name, lpb_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Keep the ssh-mode port checks hermetic: _run_ssh probes the real host
    # port (bind test + connect wait). Default to a healthy port; tests that
    # exercise the failure paths override these per-module.
    mod._port_in_use = lambda port: False
    mod._wait_ssh_port = lambda port, timeout=20: True

    return mod


def run_lpb_suite(suite_name: str, tests: list) -> int:
    """Run the lpb.py family: reset_mock() before each test, summary at end.

    SystemExit with code 0 counts as pass (some commands sys.exit(0)).
    Returns the process exit code (1 if any test failed).
    """
    print("=" * 60)
    print(suite_name)
    print("=" * 60)
    print()
    passed = failed = 0
    for t in tests:
        reset_mock()
        try:
            t()
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {e}")
            failed += 1
        except SystemExit as e:
            # Some commands call sys.exit(0) — treat clean exits as pass
            if e.code == 0:
                passed += 1
            else:
                print(f"  FAIL: unexpected exit code {e.code}")
                failed += 1
        except Exception as e:
            print(f"  ERROR: {type(e).__name__}: {e}")
            failed += 1
    print("=" * 60)
    print(f"Results: {passed}/{len(tests)} passed, {failed} failed")
    return 1 if failed else 0


# ─── localpibox family ──────────────────────────────────────────────────────

def _load_script(name: str, path: Path):
    """Load an (extensionless) script file as a module for testing."""
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

def _quiet_console():
    return log_mod.Console(color=False, out=io.StringIO(), err=io.StringIO())


def _bare_remote(tmpdir, name, branch="dev"):
    """Bare remote with one commit + tag 0.0.1 on *branch*; returns (remote, src_work)."""
    remote = tmpdir / "remotes" / f"{name}.git"
    remote.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    work = tmpdir / "src" / name
    work.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "-q", str(remote), str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(work), "checkout", "-q", "-b", branch], check=True)
    (work / "f").write_text("one")
    subprocess.run(["git", "-C", str(work), "add", "."], check=True)
    subprocess.run(["git", "-C", str(work), "commit", "-qm", "one"], check=True)
    subprocess.run(["git", "-C", str(work), "push", "-q", "origin", branch], check=True)
    # Point the remote HEAD at the pushed branch so plain clones check it out
    subprocess.run(["git", "-C", str(remote), "symbolic-ref", "HEAD", f"refs/heads/{branch}"], check=True)
    subprocess.run(["git", "-C", str(work), "tag", "0.0.1"], check=True)
    subprocess.run(["git", "-C", str(work), "push", "-q", "origin", "0.0.1"], check=True)
    return remote, work


def _push_branch(work, branch, content="two"):
    """Add a commit on *branch* of src work tree and push it."""
    subprocess.run(["git", "-C", str(work), "checkout", "-q", branch], check=True)
    (work / "f").write_text(content)
    subprocess.run(["git", "-C", str(work), "commit", "-qam", "next"], check=True)
    subprocess.run(["git", "-C", str(work), "push", "-q", "origin", branch], check=True)


class _TmpDir(Path):
    """Path-like tmpdir for tests (also exposes ``.path``)."""

    @property
    def path(self) -> "_TmpDir":
        return self


def run_lpbx_suite(suite_name: str, ns: dict) -> int:
    """Auto-discover test_* in ns and run each in its own tmpdir.

    Tests taking a parameter receive the per-test _TmpDir. SystemExit with
    code 0 counts as pass. Returns the process exit code (1 if any failed).
    """
    print("=" * 60)
    print(suite_name)
    print("=" * 60)
    passed = failed = 0
    tmp = Path(tempfile.mkdtemp(prefix="lpb-stack-tests-"))
    try:
        for name, fn in sorted(ns.items()):
            if not (name.startswith("test_") and callable(fn)):
                continue
            tmpdir = _TmpDir(tmp) / name
            tmpdir.mkdir(parents=True, exist_ok=True)
            try:
                if inspect.signature(fn).parameters:
                    fn(tmpdir)
                else:
                    fn()
                print(f"  PASS  {name}")
                passed += 1
            except SystemExit as e:
                if e.code == 0:
                    print(f"  PASS  {name}")
                    passed += 1
                else:
                    print(f"  FAIL  {name}: unexpected exit {e.code}")
                    failed += 1
            except Exception as e:  # noqa: BLE001
                print(f"  FAIL  {name}: {type(e).__name__}: {e}")
                failed += 1
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("=" * 60)
    print(f"Results: {passed}/{passed + failed} passed, {failed} failed")
    return 1 if failed else 0

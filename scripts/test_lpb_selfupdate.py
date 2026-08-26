#!/usr/bin/env python3
"""lpb.py self-update tests: `lpb --update` branch selection, engine/wrapper
replacement, missing wrapper, network failure, and VERSION sync.

Part of the lpb.py test suite (entry point: test_lpb.py)."""
from __future__ import annotations

import tempfile

from testharness import (
    _OutputCapture,
    make_module,
    reset_mock,
    run_lpb_suite,
)

class _FakeResp:
    """Minimal urlopen() response stand-in."""
    def __init__(self, data: bytes):
        self._data = data
    def read(self):
        return self._data
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def _run_self_update(mod, tag: str, tmpdir: str, create_wrapper: bool = True) -> list[str]:
    """Run mod.self_update() against a fake urlopen; return fetched URLs.

    Sets up a temp install layout (lpb + lpb.py) and points the module's
    LPB_ENGINE_PATH at it, so the real repo files are never touched.
    """
    from pathlib import Path as _P
    fetched: list[str] = []

    def fake_urlopen(url, timeout=None):
        fetched.append(url)
        return _FakeResp(b"NEW-CONTENT-" + str(url).encode())

    engine = _P(tmpdir) / "lpb.py"
    wrapper = _P(tmpdir) / "lpb"
    engine.write_text("old engine\n")
    if create_wrapper:
        wrapper.write_text("old wrapper\n")

    orig_engine = mod.LPB_ENGINE_PATH
    orig_urlopen = mod.urllib.request.urlopen
    orig_config_dir = mod.CONFIG_DIR
    mod.LPB_ENGINE_PATH = engine
    # Isolate the installed-layout dir so the VERSION sync step (and any
    # CONFIG_DIR access) hits the temp tree, never the real ~/.lpb-stack.
    # The temp "cfg" dir does not exist unless a test creates it, which
    # keeps the engine/wrapper fetch count at 2.
    mod.CONFIG_DIR = _P(tmpdir) / "cfg"
    mod.urllib.request.urlopen = fake_urlopen
    mod.cfg.image_tag = tag
    try:
        with _OutputCapture():
            mod.self_update()
    finally:
        mod.LPB_ENGINE_PATH = orig_engine
        mod.urllib.request.urlopen = orig_urlopen
        mod.CONFIG_DIR = orig_config_dir
    return fetched


def test_self_update_branch_selection():
    """self_update pulls from the branch matching the pipeline tag."""
    print("TEST: self_update branch selection")
    reset_mock()
    mod = make_module()
    cases = [
        ("dev", "dev"),                # dev pipeline
        ("main", "main"),              # stable pipeline
        ("latest", "main"),            # latest = main pipeline
        ("0.0.27-lpb-dev", "dev"),     # versioned dev pin
        ("0.0.27-lpb", "main"),        # versioned stable pin
        ("", "main"),                  # no tag → main (install default)
    ]
    # engine + wrapper + 13 localpibox package files (+ trailing VERSION fetch)
    for tag, expected_branch in cases:
        with tempfile.TemporaryDirectory() as td:
            fetched = _run_self_update(mod, tag, td)
        expect = f"https://raw.githubusercontent.com/lpb-stack/devstack/{expected_branch}/scripts/"
        non_version = [u for u in fetched if not u.endswith("/VERSION")]
        assert len(non_version) == 2 + 13, \
            f"tag={tag!r}: expected 15 script fetches, got {len(non_version)}: {non_version}"
        assert fetched[0] == expect + "lpb.py", f"tag={tag!r}: engine URL wrong: {fetched[0]}"
        assert fetched[1] == expect + "lpb", f"tag={tag!r}: wrapper URL wrong: {fetched[1]}"
        # every fetch (incl. the localpibox package) must come from the right branch
        assert all(u.startswith(expect) for u in non_version), \
            f"tag={tag!r}: off-branch fetch: {[u for u in non_version if not u.startswith(expect)]}"
    print("  PASS\n")


def test_self_update_replaces_files():
    """self_update atomically replaces engine + wrapper, leaves no .new files."""
    print("TEST: self_update replaces files")
    reset_mock()
    mod = make_module()
    with tempfile.TemporaryDirectory() as td:
        fetched = _run_self_update(mod, "dev", td)
        from pathlib import Path as _P
        engine = _P(td) / "lpb.py"
        wrapper = _P(td) / "lpb"
        assert engine.read_text() == "NEW-CONTENT-" + fetched[0]
        assert wrapper.read_text() == "NEW-CONTENT-" + fetched[1]
        assert not ( _P(td) / "lpb.py.new" ).exists(), "staging file left behind"
        assert (engine.stat().st_mode & 0o111) != 0, "engine lost exec bit"
    print("  PASS\n")


def test_self_update_no_wrapper():
    """self_update works when only the engine is present (no bash wrapper)."""
    print("TEST: self_update without wrapper")
    reset_mock()
    mod = make_module()
    from pathlib import Path as _P
    with tempfile.TemporaryDirectory() as td:
        # First run: full layout (engine + wrapper). Second: engine only.
        _run_self_update(mod, "dev", td)
        (_P(td) / "lpb").unlink()
        fetched = _run_self_update(mod, "dev", td, create_wrapper=False)
        # engine only (+ the 13 localpibox package files, re-fetched on run 2, + VERSION)
        assert len(fetched) == 1 + 13 + 1, f"expected engine-only fetch, got {len(fetched)}: {fetched}"
        assert ( _P(td) / "lpb.py" ).read_text().startswith("NEW-CONTENT-")
    print("  PASS\n")


def test_self_update_network_failure_warns_once():
    """A failed self_update warns exactly once and never raises."""
    print("TEST: self_update network failure")
    reset_mock()
    mod = make_module()
    with tempfile.TemporaryDirectory() as td:
        from pathlib import Path as _P
        engine = _P(td) / "lpb.py"
        wrapper = _P(td) / "lpb"
        engine.write_text("old engine\n")
        wrapper.write_text("old wrapper\n")

        class _Boom(Exception):
            pass

        def boom(url, timeout=None):
            raise _Boom("HTTP Error 404: Not Found")

        orig_engine = mod.LPB_ENGINE_PATH
        orig_urlopen = mod.urllib.request.urlopen
        mod.LPB_ENGINE_PATH = engine
        mod.urllib.request.urlopen = boom
        mod.cfg.image_tag = "dev"
        cap = _OutputCapture()
        try:
            with cap:
                mod.self_update()
        finally:
            mod.LPB_ENGINE_PATH = orig_engine
            mod.urllib.request.urlopen = orig_urlopen
        lines = [l for l in "".join(cap.out).splitlines() if "self-update skipped" in l]
        assert len(lines) == 1, f"expected exactly 1 warning line, got {len(lines)}: {lines}"
        assert engine.read_text() == "old engine\n", "engine must be untouched on failure"
    print("  PASS\n")


def test_self_update_syncs_version():
    """self_update refreshes the installed VERSION file from the same branch."""
    print("TEST: self_update syncs VERSION")
    reset_mock()
    mod = make_module()
    with tempfile.TemporaryDirectory() as td:
        from pathlib import Path as _P
        cfgdir = _P(td) / "cfg"
        cfgdir.mkdir()
        (cfgdir / "VERSION").write_text("0.0.1-lpb\n")
        fetched = _run_self_update(mod, "dev", td)
        version_urls = [u for u in fetched if u.endswith("/dev/VERSION")]
        assert len(version_urls) == 1, f"expected one VERSION fetch, got {fetched}"
        assert (cfgdir / "VERSION").read_text().strip() == "NEW-CONTENT-" + version_urls[0], \
            f"VERSION not synced: {(cfgdir / 'VERSION').read_text()!r}"
    print("  PASS\n")


TESTS = [
    test_self_update_branch_selection,
    test_self_update_replaces_files,
    test_self_update_no_wrapper,
    test_self_update_network_failure_warns_once,
    test_self_update_syncs_version,
]


def main() -> int:
    return run_lpb_suite("lpb.py self-update tests", TESTS)


if __name__ == "__main__":
    raise SystemExit(main())

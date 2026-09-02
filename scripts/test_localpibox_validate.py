#!/usr/bin/env python3
"""support/validate tests: checker counts, build tools, sqlite,
sudo, native modules, extensions, pi CLI."""
from __future__ import annotations

from testharness import run_lpbx_suite, _quiet_console

from unittest import mock
import importlib

validate = importlib.import_module('validate')


def test_validate_checker_counts():
    cons = _quiet_console()
    c = validate.Checker(cons)
    c.pass_("a"); c.pass_("b"); c.fail("x"); c.warn("w")
    assert c.checks == 4 and c.errors == 1


def test_validate_build_tools(tmpdir):
    cons = _quiet_console()
    c = validate.Checker(cons)
    with mock.patch.object(validate, "which", side_effect=lambda name: "/bin/" + name if name != "make" else None), \
         mock.patch.object(validate, "SQLITE_LIB_DIRS", [str(tmpdir)]):
        (tmpdir / "libsqlite3.so.0").touch()
        validate.check_build_tools(c, cons)
    assert c.checks == 6 and c.errors == 1   # make missing, sqlite present


def test_validate_sqlite_missing(tmpdir):
    cons = _quiet_console()
    c = validate.Checker(cons)
    with mock.patch.object(validate, "SQLITE_LIB_DIRS", [str(tmpdir)]), \
         mock.patch.object(validate, "which", side_effect=lambda name: "/bin/" + name):
        validate.check_build_tools(c, cons)
    assert c.errors == 1


def test_validate_sudo_ok(tmpdir):
    cons = _quiet_console()
    c = validate.Checker(cons)

    def fake_run(args, timeout=15, cwd=None):
        if args == ["sudo", "-n", "cat", "/etc/sudoers.d/nopasswd"]:
            return "lpb ALL=(ALL) NOPASSWD:ALL", "", 0
        if args == ["sudo", "-n", "true"]:
            return "", "", 0
        return "", "", 1

    with mock.patch.object(validate, "run_cmd", side_effect=fake_run):
        validate.check_sudo(c, cons)
    assert c.errors == 0


def test_validate_sudo_missing(tmpdir):
    cons = _quiet_console()
    c = validate.Checker(cons)
    with mock.patch.object(validate, "run_cmd", return_value=("", "No such file", 1)):
        validate.check_sudo(c, cons)
    assert c.errors == 1


def test_validate_native_modules(tmpdir):
    cons = _quiet_console()
    c = validate.Checker(cons)
    ext = tmpdir / "git" / "github.com" / "x" / "repo"
    node = ext / "node_modules" / "better-sqlite3" / "build" / "Release" / "better_sqlite3.node"
    node.parent.mkdir(parents=True)
    node.touch()
    calls = {"cwd": None}
    with mock.patch.object(validate, "EXT_BASE", ext), \
         mock.patch.object(
             validate, "run_cmd",
             side_effect=lambda args, timeout=60, cwd=None: (calls.update(cwd=cwd) or ("", "", 0)),
         ):
        validate.check_native_modules(c, cons)
    assert c.errors == 0
    assert calls["cwd"] == str(node.parent.parent)  # node -e runs from ext_dir


def test_validate_extensions(tmpdir):
    cons = _quiet_console()
    c = validate.Checker(cons)
    ext = tmpdir / "git"
    (ext / "github.com" / "lpb-stack" / "lemonade-pi-plugin" / "package.json").parent.mkdir(parents=True)
    (ext / "github.com" / "lpb-stack" / "lemonade-pi-plugin" / "package.json").touch()
    (ext / "github.com" / "lpb-stack" / "lpb-memory" / "package.json").parent.mkdir(parents=True)
    (ext / "github.com" / "lpb-stack" / "lpb-memory" / "package.json").touch()
    (ext / "github.com" / "lpb-stack" / "pi-subagents" / "package.json").parent.mkdir(parents=True)
    (ext / "github.com" / "lpb-stack" / "pi-subagents" / "package.json").touch()
    with mock.patch.object(validate, "EXT_BASE", ext):
        validate.check_extensions(c, cons)
    assert c.errors == 0 and c.checks == 3


def test_validate_pi_cli_missing(tmpdir):
    cons = _quiet_console()
    c = validate.Checker(cons)
    with mock.patch.object(validate, "which", return_value=None):
        validate.check_pi_cli(c, cons)
    assert c.errors == 1


def main() -> int:
    return run_lpbx_suite("validate tests", globals())


if __name__ == "__main__":
    raise SystemExit(main())

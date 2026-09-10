#!/usr/bin/env python3
"""validate — Check that the container is properly configured.

Python port of support/validate.

Run inside the container to verify:
  - NOPASSWD sudo is configured
  - Build tools are available
  - Native modules (better-sqlite3) compile and load
  - VSCodium server is accessible
  - Pi CLI is functional

Usage: podman exec -it lpb-stack /opt/devstack/validate
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

_SELF_DIR = Path(__file__).resolve().parent
for _c in (_SELF_DIR.parent / "scripts", _SELF_DIR, Path("/opt/pi-support")):
    if (_c / "localpibox").is_dir():
        sys.path.insert(0, str(_c))
        break

from localpibox.cli import install_sigpipe_handler  # noqa: E402
from localpibox.log import Console  # noqa: E402
from localpibox.run import run_cmd, which  # noqa: E402

EXT_BASE = Path("/home/lpb/.pi/agent/git")
# Git-cloned extensions (lemonade-pi-plugin, lpb-memory). pi-subagents is NOT
# here — it de-forked to the upstream npm package (installed by pi, not a git
# clone under the agent dir), so its presence is checked via the settings.json
# pin by the full validator instead.
CHECK_REPOS = ("lpb-stack/lemonade-pi-plugin", "lpb-stack/lpb-memory")
SQLITE_LIB_DIRS = ["/usr/lib/x86_64-linux-gnu"]


class Checker:
    """Tracks pass/fail/warn counts for the validation report."""

    def __init__(self, cons: Console) -> None:
        self.cons = cons
        self.checks = 0
        self.errors = 0

    def pass_(self, msg: str) -> None:
        self.cons.info(f"  \u2713 {msg}")
        self.checks += 1

    def fail(self, msg: str) -> None:
        self.cons.error(f"  \u2717 {msg}")
        self.checks += 1
        self.errors += 1

    def warn(self, msg: str) -> None:
        self.cons.warn(f"  \u26a0 {msg}")
        self.checks += 1


def section(cons: Console, title: str) -> None:
    cons.info(f"\u2500\u2500 {title} \u2500\u2500")


def check_sudo(c: Checker, cons: Console) -> None:
    section(cons, "NOPASSWD sudo")
    out, _err, code = run_cmd(["sudo", "-n", "cat", "/etc/sudoers.d/nopasswd"], timeout=15)
    if code == 0 and "NOPASSWD" in out:
        _, _, code2 = run_cmd(["sudo", "-n", "true"], timeout=15)
        if code2 == 0:
            c.pass_("NOPASSWD configured and working (sudo group)")
        else:
            c.fail("NOPASSWD file present but sudo still prompts")
    else:
        c.fail("/etc/sudoers.d/nopasswd missing or no NOPASSWD rule found")


def check_build_tools(c: Checker, cons: Console) -> None:
    section(cons, "Build tools")
    for tool in ("gcc", "g++", "make", "python3", "pkg-config"):
        if which(tool):
            c.pass_(f"{tool} available")
        else:
            c.fail(f"{tool} missing")
    if any(any(Path(d).glob("libsqlite3.so*")) for d in SQLITE_LIB_DIRS):
        c.pass_("libsqlite3-dev (shared library) installed")
    else:
        c.fail("libsqlite3-dev (shared library) missing")


def check_native_modules(c: Checker, cons: Console) -> None:
    section(cons, "Native modules")
    found = False
    for node_bin in EXT_BASE.rglob("better_sqlite3.node"):
        if not node_bin.is_file():
            continue
        found = True
        ext_dir = node_bin.parent.parent
        ext_name = ext_dir.name
        c.pass_(f"better-sqlite3 binary exists in {ext_name}")
        out, err, code = run_cmd(
            ["node", "-e", "const db = require('better-sqlite3')(':memory:'); db.close()"],
            timeout=60,
            cwd=str(ext_dir),
        )
        if code == 0:
            c.pass_(f"better-sqlite3 loads and queries in {ext_name}")
        else:
            c.fail(f"better-sqlite3 fails to load in {ext_name} ({err.strip() or out.strip()})")
    if not found:
        c.warn("No better-sqlite3 extensions found (expected if no extensions installed)")


def check_vscodium(c: Checker, cons: Console) -> None:
    section(cons, "VSCodium server")
    ed_port = os.environ.get("ED_PORT", os.environ.get("LPB_ED_PORT", "3000"))
    token = os.environ.get("CONNECTION_TOKEN", os.environ.get("LPB_CONNECTION_TOKEN", ""))
    if which("codium-server") or Path("/opt/vscodium/bin/codium-server").is_file():
        out, err, code = run_cmd(["curl", "-sf", f"http://localhost:{ed_port}/?tkn={token}"], timeout=20)
        if code == 0:
            c.pass_(f"VSCodium server responsive on port {ed_port}")
        else:
            c.fail(f"VSCodium server not responsive on port {ed_port} ({err.strip() or out.strip()})")
    else:
        c.pass_("VSCodium not installed — running CLI image")


def check_pi_cli(c: Checker, cons: Console) -> None:
    section(cons, "Pi CLI")
    if which("pi"):
        c.pass_("pi command available")
        out, err, code = run_cmd(["pi", "--version"], timeout=30)
        if code == 0:
            c.pass_("pi --version works")
        else:
            c.fail(f"pi --version failed ({err.strip() or out.strip()})")
    else:
        c.fail("pi command not found")


def check_extensions(c: Checker, cons: Console) -> None:
    section(cons, "Extensions")
    for ext_repo in CHECK_REPOS:
        match = next(
            (p for p in EXT_BASE.rglob("package.json") if ext_repo in str(p)),
            None,
        )
        if match:
            c.pass_(f"{match.parent.name} installed")
        else:
            c.fail(f"{ext_repo} not found in {EXT_BASE}")


def main(argv: list[str] | None = None) -> int:
    install_sigpipe_handler()
    parser = argparse.ArgumentParser(prog="validate", description=__doc__)
    parser.parse_args(argv)
    cons = Console()

    cons.done("=== LocalPibox Devstack Validation ===")
    cons.raw("")
    c = Checker(cons)

    check_sudo(c, cons)
    check_build_tools(c, cons)
    check_native_modules(c, cons)
    check_vscodium(c, cons)
    check_pi_cli(c, cons)
    check_extensions(c, cons)

    cons.done("=== Summary ===")
    cons.raw(f"  Checks:  {c.checks}")
    cons.raw(f"  Errors:  {c.errors}")
    cons.raw("")
    if c.errors == 0:
        cons.done("  \u2705 All checks passed — devstack is healthy")
    else:
        cons.error(f"  \u2717 {c.errors} check(s) failed — review above")
    return min(c.errors, 255)


if __name__ == "__main__":
    raise SystemExit(main())

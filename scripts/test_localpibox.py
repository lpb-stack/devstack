#!/usr/bin/env python3
"""Test harness entry point for the localpibox package + ported support tools.

The suite is split one file per target (all in scripts/):

  test_localpibox_env.py              — localpibox.env
  test_localpibox_log.py              — localpibox.log
  test_localpibox_run.py              — localpibox.run
  test_localpibox_cli.py              — localpibox.cli
  test_localpibox_build.py            — support/build.py
  test_localpibox_bsc.py              — browser-state-cleanup
  test_localpibox_config.py           — scripts/lpb-config
  test_localpibox_setup.py            — localpibox.setup (unified setup wizard)
                                        + lpb-config setup/check delegation
  test_localpibox_workspace.py        — workspace sync (localpibox.stack)
  test_localpibox_release.py          — release docs readiness (localpibox.stack)
  test_localpibox_stack.py            — localpibox.stack: pipeline + VERSION math
  test_localpibox_devstack.py         — scripts/lpb-devstack (bump, tag-repos)
  test_localpibox_validate.py         — support/validate
  test_localpibox_install_browser.py  — support/install-browser
  test_localpibox_install_openspec.py — support/install-openspec

Shared plumbing (path setup, _load_script, _quiet_console, _TmpDir, runner):
testharness.py

Run this file for the full suite, or any sub-file directly.
Runs with plain Python (no third-party deps).
"""
from __future__ import annotations

import sys

import test_localpibox_env as t_env
import test_localpibox_log as t_log
import test_localpibox_run as t_run
import test_localpibox_cli as t_cli
import test_localpibox_build as t_build
import test_localpibox_bsc as t_bsc
import test_localpibox_config as t_config
import test_localpibox_setup as t_setup
import test_localpibox_workspace as t_workspace
import test_localpibox_release as t_release
import test_localpibox_stack as t_stack
import test_localpibox_devstack as t_devstack
import test_localpibox_validate as t_validate
import test_localpibox_install_browser as t_install_browser
import test_localpibox_install_openspec as t_install_openspec

MODULES = [
    t_env,
    t_log,
    t_run,
    t_cli,
    t_build,
    t_bsc,
    t_config,
    t_setup,
    t_workspace,
    t_release,
    t_stack,
    t_devstack,
    t_validate,
    t_install_browser,
    t_install_openspec,
]


def main() -> int:
    failed = 0
    for m in MODULES:
        if m.main():
            failed += 1
    print("=" * 60)
    if failed:
        print(f"lpb-stack + ported-tools test suite: {failed}/{len(MODULES)} suite(s) FAILED")
    else:
        print(f"lpb-stack + ported-tools test suite: all {len(MODULES)} suites passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

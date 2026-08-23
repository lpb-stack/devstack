#!/usr/bin/env python3
"""lpb.py test suite — entry point.

The suite is split per concern, one file per area (all in scripts/):

  test_lpb_basic.py       — lifecycle commands, run modes, URL building
  test_lpb_tags.py        — image tag selection + image resolution
  test_lpb_selfupdate.py  — lpb --update (launcher self-update)
  test_lpb_ssh.py         — SSH mode (key auto-detect, password auth)
  test_lpb_lemonade.py    — first-boot lemonade URL/key prompt + env passthrough
  test_lpb_regression.py  — structural guards, env files, cmd_* regression
                            tests, mutation tests

Shared mocks + module loader: testharness.py

Run this file for the full suite (original test order preserved:
regression guards first, then behavioral), or any sub-file directly.
"""
from __future__ import annotations

import sys

from testharness import run_lpb_suite

from test_lpb_basic import TESTS as BASIC_TESTS
from test_lpb_tags import TESTS as TAG_TESTS
from test_lpb_selfupdate import TESTS as SELFUPDATE_TESTS
from test_lpb_ssh import TESTS as SSH_TESTS
from test_lpb_lemonade import TESTS as LEMONADE_TESTS
from test_lpb_regression import TESTS as REGRESSION_TESTS

TESTS = [
    # regression guards first (fast, structural)
    *REGRESSION_TESTS,
    # behavioral
    *BASIC_TESTS,
    *TAG_TESTS,
    *SELFUPDATE_TESTS,
    *SSH_TESTS,
    *LEMONADE_TESTS,
]


if __name__ == "__main__":
    sys.exit(run_lpb_suite("lpb.py test suite (mocked podman/docker)", TESTS))

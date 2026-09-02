#!/usr/bin/env python3
"""build.py — LocalPibox Devstack image builder (Python port of support/build.sh).

Single entry point to build the stack. Reads lpb.stack.env (image identity,
forks, versions) and lpb.conf.env (baked runtime defaults) so a fork only
edits those files — never this script or the Dockerfile.

Usage:
  support/build.py                      build cli + web, tag from lpb.stack.env
  support/build.py cli                  build only the cli image
  support/build.py web                  build only the web image
  support/build.py cli --push           build + push

The image tags come from lpb.stack.env LPB_IMAGE_CLI / LPB_IMAGE_WEB.
"""

from __future__ import annotations

import argparse
import datetime
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from localpibox import log  # noqa: E402
from localpibox.cli import install_sigpipe_handler  # noqa: E402
from localpibox.env import load_env_chain  # noqa: E402
from localpibox.run import run_cmd, which  # noqa: E402

REQUIRED_STACK_VARS = [
    "LPB_IMAGE_CLI",
    "LPB_IMAGE_WEB",
    "LPB_PI_VERSION",
    "LPB_CONFIG_FORK",
    "LPB_CONFIG_REF",
    "LPB_NODE_VERSION",
    "LPB_VSCODIUM_VERSION",
]
# No required conf vars (LPB_MAX_TOKENS_CONTEXT_RATIO retired 2026-09-02 —
# response ceilings are per-model in the lemonade-pi-plugin catalog now).
REQUIRED_CONF_VARS = []
ENV_FILES = ["lpb.stack.env", "lpb.conf.env"]

# Mainstream pi repo — used only to resolve the source sha of the pinned
# npm version for image metadata (PI_HEAD_SHA). De-forked 2026-08-31: the
# image no longer builds pi from source (the lpb-stack/pi fork is retired);
# a forked pi requires re-introducing a source build (docs/reference/forking.md).
PI_UPSTREAM = "https://github.com/earendil-works/pi.git"


def project_root() -> Path:
    """The devstack repo root (parent of the support/ directory)."""
    return ROOT


def load_build_env(
    root: str | Path | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> dict[str, str]:
    """Load + merge the two env files from the project root.

    Raises ``FileNotFoundError`` when an env file is missing and
    ``RuntimeError`` when a required variable is absent.
    """
    root = Path(root) if root else ROOT
    missing_files = [name for name in ENV_FILES if not (root / name).is_file()]
    if missing_files:
        raise FileNotFoundError(
            f"{', '.join(missing_files)} not found at {root}"
        )
    env = load_env_chain([root / name for name in ENV_FILES], environ=environ)
    missing_vars = [
        key
        for key in REQUIRED_STACK_VARS + REQUIRED_CONF_VARS
        if not env.get(key)
    ]
    if missing_vars:
        raise RuntimeError(f"missing required variable(s): {', '.join(missing_vars)}")
    return env


def git_ls_remote(fork: str, ref: str, runner=None, ref_kind: str = "heads") -> str:
    """HEAD sha of *ref* on *fork* via ``git ls-remote``; ``unknown`` on failure."""
    if not which("git"):
        return "unknown"
    runner = runner or run_cmd
    out, _err, code = runner(["git", "ls-remote", fork, f"refs/{ref_kind}/{ref}"], timeout=30)
    if code or not out.strip():
        return "unknown"
    return out.split()[0]


def git_head_short(root: str | Path, runner=None) -> str:
    """Short HEAD sha of the repo at *root*; ``unknown`` on failure."""
    runner = runner or run_cmd
    out, _err, code = runner(["git", "rev-parse", "--short", "HEAD"], timeout=30, cwd=str(root))
    return out.strip() if code == 0 and out.strip() else "unknown"


def build_args(
    env: dict[str, str],
    *,
    root: str | Path | None = None,
    now: datetime.datetime | None = None,
    runner=None,
) -> list[str]:
    """Single authoritative mapping from env files to Docker ``--build-arg``s."""
    root = Path(root) if root else ROOT
    now = now or datetime.datetime.now(datetime.timezone.utc)
    version_file = root / "VERSION"
    lpb_version = version_file.read_text().strip() if version_file.is_file() else "unknown"
    pi_version = env["LPB_PI_VERSION"]
    pi_head_sha = git_ls_remote(PI_UPSTREAM, f"v{pi_version}", runner=runner, ref_kind="tags")
    return [
        "--build-arg", f"PI_VERSION={pi_version}",
        "--build-arg", f"PI_HEAD_SHA={pi_head_sha}",
        "--build-arg", f"CONFIG_FORK={env['LPB_CONFIG_FORK']}",
        "--build-arg", f"CONFIG_REF={env['LPB_CONFIG_REF']}",
        "--build-arg", f"NODE_VERSION={env['LPB_NODE_VERSION']}",
        "--build-arg", f"VSCODIUM_VERSION={env['LPB_VSCODIUM_VERSION']}",
        "--build-arg", f"LPB_VERSION={lpb_version}",
        "--build-arg", f"IMAGE_REVISION={git_head_short(root, runner=runner)}",
        "--build-arg", f"IMAGE_BUILT={now.strftime('%Y-%m-%dT%H:%M:%SZ')}",
    ]


def build_command(
    target: str,
    image: str,
    args: list[str],
    *,
    push: bool = False,
    platform: str = "linux/amd64",
    root: str | Path | None = None,
) -> list[str]:
    """Assemble the ``docker buildx build`` command for *target*."""
    root = Path(root) if root else ROOT
    cmd = [
        "docker", "buildx", "build",
        "--target", target,
        "-t", image,
        "--platform", platform,
        "-f", str(root / "Dockerfile"),
    ]
    cmd += args
    if push:
        cmd.append("--push")
    cmd.append(".")
    return cmd


def main(argv: list[str] | None = None) -> int:
    install_sigpipe_handler()
    parser = argparse.ArgumentParser(prog="build", description=__doc__)
    parser.add_argument(
        "target", nargs="?", choices=["all", "cli", "web"], default="all",
        help="image target to build (default: all)",
    )
    parser.add_argument("--push", action="store_true", help="push images after build")
    parser.add_argument(
        "--root", default=str(ROOT), help="project root containing the env files",
    )
    args = parser.parse_args(argv)

    try:
        env = load_build_env(args.root)
    except (FileNotFoundError, RuntimeError) as exc:
        log.error(f"FATAL: {exc}")
        return 1

    build = build_args(env, root=args.root)
    targets = {"all": ["cli", "web"], "cli": ["cli"], "web": ["web"]}[args.target]
    for target in targets:
        image = env["LPB_IMAGE_CLI" if target == "cli" else "LPB_IMAGE_WEB"]
        cmd = build_command(target, image, build, push=args.push, root=args.root)
        log.info(f"Building '{target}' -> {image}")
        out, err, code = run_cmd(cmd, timeout=3600, cwd=args.root)
        if code:
            log.error(err.strip() or out.strip() or f"docker buildx exited {code}")
            return code
        log.done(f"{target}: {image}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

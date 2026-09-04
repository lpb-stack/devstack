"""Full-stack validation: check every alignment dimension against a pipeline.

Covers: VERSION file, config repo branch, workspace repo branches/symlinks,
pipeline consistency (no mixed dev/main state), extension alignment,
worktree state (config clean, extension-repo WIP report), stack env refs,
and settings.json pins.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..log import Console
from .gitutil import git
from .repos import (
    AGENT_GIT,
    DEFAULT_AGENT_DIR,
    LPB_EXTENSION_REPOS,
    WORKSPACE_REPOS,
    WORKSPACE_ROOT,
    _DEVSTACK_ROOT,
)
from .version import _find_version_file, expected_branch, expected_pin_version, get_stack_env, get_stack_env_base, get_version
from .workspace import (
    _detached_ref,
    _dirty_files,
    _get_pinned_versions,
    _read_settings,
    _repo_branch,
    _repo_head,
    _resolve_repo_path,
)


def cmd_validate(pipeline: str, cons: Console) -> int:
    """Validate the entire stack alignment to the current pipeline."""
    version = get_version()
    stack_env = get_stack_env(pipeline)

    cons.info("=" * 60)
    cons.info("  LocalPibox Stack Validation")
    cons.info("=" * 60)
    cons.info("")
    cons.info(f"  Pipeline:  {pipeline}")
    cons.info(f"  VERSION:   {version}")
    cons.info(f"  LPB_PI_VERSION:   {stack_env.get('LPB_PI_VERSION', '?')}")
    cons.info(f"  LPB_CONFIG_REF: {stack_env.get('LPB_CONFIG_REF', '?')}")
    cons.info("")

    total_checks = 0
    passed_checks = 0

    def check(label: str, condition: bool, detail: str = "", fix: str = "") -> None:
        nonlocal total_checks, passed_checks
        total_checks += 1
        if condition:
            passed_checks += 1
            cons.info(f"  ✅ {label}")
            if detail:
                cons.raw(f"     {detail}")
        else:
            cons.warn(f"  ❌ {label}")
            if detail:
                cons.warn(f"     {detail}")
            if fix:
                cons.info(f"     Fix: {fix}")

    # ── 1. VERSION file ────────────────────────────────────────────────
    vf = _find_version_file()
    if vf is not None:
        version_on_disk = vf.read_text().strip()
        check(
            "VERSION file exists",
            True,
            f"{vf} = {version_on_disk}",
        )
        version_matches = (pipeline == "dev" and "-dev" in version_on_disk) or \
                          (pipeline == "main" and "-dev" not in version_on_disk)
        check(
            "VERSION matches pipeline",
            version_matches,
            f"VERSION={version_on_disk}, pipeline={pipeline}",
            "Update devstack/VERSION to match pipeline",
        )
    else:
        check("VERSION file exists", False,
              f"checked {_DEVSTACK_ROOT}, /opt/devstack, {WORKSPACE_ROOT / 'devstack'}",
              "Ensure devstack/VERSION exists")
        check("VERSION matches pipeline", False,
              "no VERSION file found", "Ensure devstack/VERSION exists")

    # ── 2. Config repo ─────────────────────────────────────────────────
    # branch_map collects (name, actual_branch, expected_branch) for the
    # at-a-glance pipeline-consistency check at the end of section 3.
    branch_map: list[tuple[str, str, str]] = []
    config_path = Path(DEFAULT_AGENT_DIR)
    if (config_path / ".git").exists():
        config_branch = _repo_branch(config_path)
        config_expected = expected_branch("config", pipeline)
        branch_map.append(("config", config_branch, config_expected))
        check(
            "Config repo on correct branch",
            config_branch == config_expected,
            f"current={config_branch}, expected={config_expected}",
            "lpb-config reset (or git checkout <expected>)",
        )
    else:
        check("Config repo exists", False, f"{config_path} not found",
              "lpb-config update")

    # ── 3. Workspace repos ─────────────────────────────────────────────
    cons.info("")
    cons.info("  Workspace repos:")

    for name, is_sym, is_ext, dev_branch, main_branch in WORKSPACE_REPOS:
        expected = expected_branch(name, pipeline)
        path = _resolve_repo_path(name)

        if path is None:
            check(f"  {name} exists", False,
                  f"{WORKSPACE_ROOT / name} not found",
                  "Clone or create symlink")
            continue

        branch = _repo_branch(path)
        head = _repo_head(path)
        details = f"branch={branch} ({head})"
        branch_map.append((name, branch, expected))

        if is_sym:
            ws_path = WORKSPACE_ROOT / name
            symlink_ok = ws_path.is_symlink()
            check(f"  {name} symlink", symlink_ok,
                  f"{ws_path} → {ws_path.resolve() if symlink_ok else 'broken'}")

        check(f"  {name} branch", branch == expected, details,
              f"cd {path} && git checkout {expected}")

    # At-a-glance consistency: every repo must sit on its branch for this
    # pipeline — a partial sync leaves a mixed dev/main state, and that
    # shows up here in one line instead of scattered per-repo failures.
    bad = [(n, b) for n, b, e in branch_map if b != e]
    check(
        "Pipeline consistency (no mixed state)",
        not bad,
        ", ".join(f"{n}={b}" for n, b, _ in branch_map) or "no repos found",
        f"lpb-devstack --tag {pipeline} workspace sync",
    )

    # ── 3b. Worktree state — leftover uncommitted changes ─────────────
    cons.info("")
    cons.info("  Worktree state:")

    # config repo: everything runtime is gitignored there, so a dirty
    # worktree means uncommitted template/skill/doc content — always an
    # anomaly, and a hard check.
    if (config_path / ".git").exists():
        cfg_dirty = _dirty_files(config_path)
        check(
            "  config worktree clean",
            not cfg_dirty,
            "uncommitted changes:" if cfg_dirty else "clean",
            "git -C ~/.pi/agent status — commit or discard first",
        )
        if cfg_dirty:
            for line in cfg_dirty[:5]:
                cons.warn(f"     {line.strip()}")

    # Extension code repos: uncommitted WIP is normal during dev — report
    # it (it previously sat invisible until a sync skipped the repo), but
    # don't block on it. devstack's own tree is judged by the pre-commit
    # hook (unstaged check), not here.
    dirty_ext: list[str] = []
    for name, is_sym, is_ext, dev_branch, main_branch in WORKSPACE_REPOS:
        if name == "devstack":
            continue
        path = _resolve_repo_path(name)
        if path is None:
            continue
        files = _dirty_files(path)
        if files:
            dirty_ext.append(f"{name} ({len(files)} file(s))")
    if dirty_ext:
        cons.warn(f"  WIP in extension repos (not blocking): {', '.join(dirty_ext)}")
    else:
        cons.info("  extension repos: no uncommitted changes")

    # ── 4. Extension repos match workspace ─────────────────────────────
    cons.info("")
    cons.info("  Extension alignment:")

    for name, is_sym, is_ext, dev_branch, main_branch in WORKSPACE_REPOS:
        if not is_ext:
            continue

        ws_path = WORKSPACE_ROOT / name
        ext_path = AGENT_GIT / name

        if ws_path.is_symlink():
            # Symlink should point to extension
            resolved = ws_path.resolve()
            check(f"  {name} → extension",
                  resolved == ext_path,
                  f"symlink → {resolved}",
                  f"rm {ws_path} && ln -s {ext_path} {ws_path}")
        elif ext_path.exists():
            # Not symlink — check if they have same commit
            ws_head = _repo_head(ws_path)
            ext_head = _repo_head(ext_path)
            check(f"  {name} in sync",
                  ws_head == ext_head,
                  f"ws={ws_head}, ext={ext_head}",
                  f"cd {ext_path} && git checkout <branch>")

    # ── 5. Stack env alignment ─────────────────────────────────────────
    cons.info("")
    cons.info("  Stack env:")

    pi_version = stack_env.get("LPB_PI_VERSION", "")
    base_pi_version = get_stack_env_base().get("LPB_PI_VERSION", "")
    config_ref = stack_env.get("LPB_CONFIG_REF", "")
    config_ref_expected = expected_branch("config", pipeline)

    check(
        "LPB_PI_VERSION is a version",
        bool(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", pi_version)),
        f"current={pi_version!r} (expected X.Y.Z npm version of mainstream pi)",
        "Edit lpb.stack.env / lpb.stack.<pipeline>.env",
    )
    check(
        "LPB_PI_VERSION matches base lpb.stack.env",
        bool(pi_version) and pi_version == base_pi_version,
        f"profile={pi_version!r}, base={base_pi_version!r}",
        "Bump LPB_PI_VERSION in lpb.stack.env AND the pipeline profile together",
    )
    check(
        "LPB_CONFIG_REF correct",
        config_ref == config_ref_expected,
        f"current={config_ref}, expected={config_ref_expected}",
        f"Edit lpb.stack.{pipeline}.env or lpb.stack.env",
    )

    # ── 6. Settings.json extension pins ────────────────────────────────
    cons.info("")
    cons.info("  Extension pins:")

    # Determine target version for this pipeline
    target_version = expected_pin_version(pipeline)

    settings_path = config_path / "settings.json"
    settings = _read_settings(config_path)
    if settings:
        current_pins = _get_pinned_versions(settings)

        for pkg_name in LPB_EXTENSION_REPOS:
            pinned_tag = current_pins.get(pkg_name)
            if pinned_tag:
                if pinned_tag == target_version:
                    check(
                        f"  {pkg_name} pinned",
                        True,
                        f"@{pinned_tag} (matches VERSION)",
                    )
                else:
                    check(
                        f"  {pkg_name} pinned",
                        False,
                        f"@{pinned_tag} (expected: {target_version})",
                        "lpb-config sync-pins",
                    )
            else:
                check(f"  {pkg_name} pinned", False,
                      "not found in settings.json",
                      "lpb-config sync-pins")
    else:
        check("settings.json exists", False,
              f"{settings_path} not found",
              "Clone config repo or create settings.json")

    # ── 7. (pi fork branch consistency removed — de-forked 2026-08-31:
    #       the lpb-stack/pi fork is retired; mainstream pi installs from
    #       the npm registry at LPB_PI_VERSION, so fork branch state is moot)

    # ── Summary ────────────────────────────────────────────────────────
    cons.info("")
    cons.info("=" * 60)
    pct = (passed_checks / total_checks * 100) if total_checks > 0 else 0
    if passed_checks == total_checks:
        cons.done(f"  All {total_checks} checks passed ✅")
    else:
        cons.warn(f"  {passed_checks}/{total_checks} checks passed ({pct:.0f}%) ❌")
    cons.info("=" * 60)

    return 0 if passed_checks == total_checks else 1

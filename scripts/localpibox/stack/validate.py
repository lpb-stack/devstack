"""Full-stack validation: check every alignment dimension against a pipeline.

Covers: VERSION file, config repo branch, workspace repo branches/symlinks,
extension alignment, stack env refs, settings.json pins, and (informational)
pi fork branch consistency.
"""

from __future__ import annotations

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
from .version import _find_version_file, expected_branch, expected_pin_version, get_stack_env, get_version
from .workspace import (
    _detached_ref,
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
    cons.info(f"  LPB_PI_REF:     {stack_env.get('LPB_PI_REF', '?')}")
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
    config_path = Path(DEFAULT_AGENT_DIR)
    if (config_path / ".git").exists():
        config_branch = _repo_branch(config_path)
        config_expected = expected_branch("config", pipeline)
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

        if is_sym:
            ws_path = WORKSPACE_ROOT / name
            symlink_ok = ws_path.is_symlink()
            check(f"  {name} symlink", symlink_ok,
                  f"{ws_path} → {ws_path.resolve() if symlink_ok else 'broken'}")
            details = "symlink ✅" if symlink_ok else "symlink ❌"

        check(f"  {name} branch", branch == expected, details,
              f"cd {path} && git checkout {expected}")

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

    pi_ref = stack_env.get("LPB_PI_REF", "")
    config_ref = stack_env.get("LPB_CONFIG_REF", "")
    pi_ref_expected = expected_branch("pi", pipeline)
    config_ref_expected = expected_branch("config", pipeline)

    check(
        "LPB_PI_REF correct",
        pi_ref == pi_ref_expected,
        f"current={pi_ref}, expected={pi_ref_expected}",
        f"Edit lpb.stack.{pipeline}.env or lpb.stack.env",
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

    # ── 7. pi/lpb vs lpb-dev (informational only) ────────────────────
    cons.info("")
    cons.info("  Fork branch consistency:")

    pi_path = _resolve_repo_path("pi")
    if pi_path:
        lpb_hash_out, _, lpb_code = git(pi_path, "rev-parse", "--verify", "lpb")
        lpbdev_hash_out, _, lpbdev_code = git(pi_path, "rev-parse", "--verify", "lpb-dev")

        if lpb_code == 0 and lpbdev_code == 0:
            lpb_hash = lpb_hash_out.strip()
            lpbdev_hash = lpbdev_hash_out.strip()
            if lpb_hash == lpbdev_hash:
                check(
                    "pi: lpb == lpb-dev",
                    True,
                    f"lpb=lpb-dev ({lpb_hash[:8]})",
                )
            else:
                # lpb-dev ahead of lpb is normal during active development
                # Only warn, don't fail — stable merge to lpb happens when ready
                ahead_out, _, _ = git(pi_path, "rev-list", "--count", "lpb..lpb-dev")
                ahead = ahead_out.strip() or "0"
                cons.info(f"  ℹ️  pi: lpb-dev is {ahead} commit(s) ahead of lpb (normal during dev)")
                cons.raw(f"     lpb={lpb_hash[:8]}, lpb-dev={lpbdev_hash[:8]}")
        else:
            missing = []
            if lpb_code != 0:
                missing.append("lpb")
            if lpbdev_code != 0:
                missing.append("lpb-dev")
            check(
                "pi: lpb & lpb-dev both exist",
                False,
                f"missing local branch(es): {', '.join(missing)}",
                "lpb-devstack workspace sync (creates local tracking branches for both pipelines)",
            )

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

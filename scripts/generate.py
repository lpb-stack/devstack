#!/usr/bin/env python3
"""Prepare the docs build tree for the LocalPibox stack docs site.

Materializes the derived ``docs/`` directory (gitignored) from the tracked
content, and writes the version-stamped repo map page into it. Run
before ``mkdocs build`` / ``mike`` — CI does this automatically:

    python3 scripts/generate.py                        # current workspace state
    python3 scripts/generate.py --tag 0.0.53-lpb-dev   # stamp for a stack tag

With ``--tag`` the tag is fetched in each repo (a no-op when already present);
repo state is read at ``origin/<tag>``. Missing repos are reported, not fatal.

Repo paths follow the lpb-config env conventions:
    devstack            this repo (path of this file's parent dir)
    LPB_WORKSPACE_ROOT  workspace root (default: /home/lpb/workspace) — pi
    AGENT_DIR           config repo (default: /home/lpb/.pi/agent)
    LPB_AGENT_GIT       extension clones (default: $AGENT_DIR/git/github.com/lpb-stack)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_SELF = Path(__file__).resolve()
REPO_ROOT = _SELF.parent.parent
DOCS_DIR = REPO_ROOT / "docs"

WORKSPACE_ROOT = Path(os.environ.get("LPB_WORKSPACE_ROOT", "/home/lpb/workspace"))
AGENT_DIR = Path(os.environ.get("AGENT_DIR", "/home/lpb/.pi/agent"))
AGENT_GIT = Path(os.environ.get("LPB_AGENT_GIT", f"{AGENT_DIR}/git/github.com/lpb-stack"))

SITE_BASE = "https://lpb-stack.github.io/devstack"

# Tracked source file -> derived docs/ path (relative to DOCS_DIR)
CONTENT: list[tuple[str, str]] = [
    ("README.md", "index.md"),
    ("CONTRIBUTING.md", "contributing.md"),
    ("doc/fork-improvements.md", "reference/fork-improvements.md"),
    ("support/docs/subagent-spawning-pattern.md", "operations/subagent-spawning-pattern.md"),
    ("doc/lpb-cli.md", "reference/lpb-cli.md"),
    ("doc/lpb-config.md", "reference/lpb-config.md"),
    ("doc/lpb-devstack.md", "reference/lpb-devstack.md"),
    ("doc/env-vars.md", "reference/env-vars.md"),
    ("doc/config-repo.md", "reference/config-repo.md"),
    ("doc/lpb-memory-overview.md", "reference/lpb-memory-overview.md"),
    ("doc/forking.md", "reference/forking.md"),
]

# (name, path, role, dev_branch, stable_branch, tagged_by_ci)
REPOS: list[tuple[str, Path, str, str, str, bool]] = [
    ("devstack", REPO_ROOT, "workspace (single source of VERSION)", "dev", "main", False),
    ("config", AGENT_DIR, "workspace (agent preset)", "dev", "main", True),
    ("lpb-memory", AGENT_GIT / "lpb-memory", "Pi extension (memory)", "dev", "main", True),
    ("pi-subagents", AGENT_GIT / "pi-subagents", "Pi extension (subagents)", "lpb-dev", "lpb", True),
    ("lemonade-pi-plugin", AGENT_GIT / "lemonade-pi-plugin", "Pi extension (lemonade provider)", "lpb-dev", "lpb", True),
    ("pi", WORKSPACE_ROOT / "pi", "Reference clone of the retired fork (de-forked 2026-08-31 — image installs mainstream pi from npm at `LPB_PI_VERSION`)", "—", "—", False),
]


def git(path: Path, *args: str, timeout: int = 120) -> tuple[str, int]:
    """Run git in *path*; returns (stdout_stripped, returncode). Never raises."""
    try:
        r = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.stdout.strip(), r.returncode
    except (OSError, subprocess.TimeoutExpired):
        return "", 1


def resolve_ref(path: Path, tag: str | None) -> str | None:
    """Return the ref to read state from (or None when the repo is unavailable)."""
    if not (path / ".git").exists():
        return None
    if not tag:
        return "HEAD"
    git(path, "fetch", "--quiet", "origin", f"refs/tags/{tag}:refs/tags/{tag}", timeout=180)
    for cand in (f"origin/{tag}", tag):
        _, code = git(path, "rev-parse", "--verify", "--quiet", f"{cand}^{{commit}}")
        if code == 0:
            return cand
    # devstack is never tagged: find the commit whose VERSION file EXACTLY
    # equals the tag. (git log -S <tag> would also match sibling versions
    # as substrings — e.g. 0.0.62-lpb inside 0.0.62-lpb-dev.)
    if path == REPO_ROOT:
        out, code = git(path, "log", "--all", "--format=%H", "--", "VERSION")
        if code == 0:
            for sha in out.strip().splitlines():
                v, vcode = git(path, "show", f"{sha}:VERSION")
                if vcode == 0 and v.strip() == tag:
                    return sha
    return None


def file_at(path: Path, ref: str, file: str) -> str | None:
    out, code = git(path, "show", f"{ref}:{file}")
    return out.strip() if code == 0 else None


def latest_tags(path: Path) -> tuple[str, str]:
    """(latest dev tag, latest stable tag) for a repo, using version sort."""
    out, code = git(path, "tag", "--list", "*-lpb*")
    if code != 0:
        return "", ""
    tags = out.splitlines()
    dev = [t for t in tags if t.endswith("-lpb-dev")]
    stable = [t for t in tags if t.endswith("-lpb") and not t.endswith("-lpb-dev")]

    def sort_key(t: str) -> list[int]:
        try:
            return [int(p) for p in t.split("-lpb")[0].split(".")]
        except ValueError:
            return [0]

    dev.sort(key=sort_key)
    stable.sort(key=sort_key)
    return (dev[-1] if dev else ""), (stable[-1] if stable else "")


def repo_map_md(tag: str | None, now: str) -> str:
    lines = [
        "# Repository map",
        "",
        f"> Generated by `scripts/generate.py` on {now} — do not edit by hand.",
    ]
    if tag:
        lines.append(f"> Stamped for stack tag **`{tag}`**.")
    else:
        lines.append("> Generated from the current workspace state (no tag).")
    lines += [
        "",
        "All repos live under `github.com/lpb-stack`. devstack is the single "
        "source of the stack version (`VERSION` file) and is never tagged; "
        "CI tags the other four repos per pipeline. Every **stable** "
        f"release has a matching docs version at `{SITE_BASE}/<tag>/` "
        "— use the version switcher in the header (or visit "
        f"[the docs root]({SITE_BASE}/) for the latest).",
        "",
        "| Repo | Role | Dev branch | Stable branch | Latest dev tag | Latest stable tag |",
        "|---|---|---|---|---|---|",
    ]
    for name, path, role, dev_b, main_b, tagged in REPOS:
        if tagged:
            dev_t, stable_t = latest_tags(path)
            lines.append(
                f"| `{name}` | {role} | `{dev_b}` | `{main_b}` "
                f"| `{dev_t or '—'}` | `{stable_t or '—'}` |"
            )
        else:
            lines.append(
                f"| `{name}` | {role} | `{dev_b}` | `{main_b}` | — | — |"
            )

    version = None
    for name, path, _r, _d, _m, _t in REPOS:
        if name == "devstack":
            ref = resolve_ref(path, tag)
            if ref:
                version = file_at(path, ref, "VERSION")
    lines += [""]
    if version:
        lines.append(f"Stack version (devstack `VERSION`): **`{version}`**")
    lines += [
        "",
        "Pipeline suffixes: `-lpb-dev` → dev pipeline, bare `-lpb` → "
        "main (stable) pipeline.",
        "",
        "Full tag history: `git ls-remote --tags "
        "https://github.com/lpb-stack/<repo>`.",
    ]
    return "\n".join(lines) + "\n"


def copy_content() -> list[str]:
    """Copy tracked content into the derived docs tree; returns missing sources."""
    if DOCS_DIR.exists():
        shutil.rmtree(DOCS_DIR)
    missing = []
    for src, dest in CONTENT:
        src_path = REPO_ROOT / src
        if not src_path.is_file():
            missing.append(src)
            continue
        dest_path = DOCS_DIR / dest
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        text = src_path.read_text()
        # The README (site home) links to doc pages as `doc/<x>.md` — that
        # path works on GitHub but not on the site, where the same files
        # live under reference/. Rewrite the links for the derived copy.
        if dest == "index.md":
            for s, d in CONTENT:
                if s.startswith("doc/") and d.startswith("reference/"):
                    text = text.replace(f"({s})", f"({d})")
        dest_path.write_text(text)
    return missing


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tag", default=None,
                    help="stack tag to stamp (e.g. 0.0.53-lpb-dev)")
    args = ap.parse_args(argv)

    tag = (args.tag or "").strip() or None
    if tag:
        ok = tag.endswith("-lpb") or tag.endswith("-lpb-dev")
        if not ok:
            print(f"warning: '{tag}' does not look like a stack tag "
                  f"(expected *-lpb or *-lpb-dev)", file=sys.stderr)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    missing = copy_content()
    (DOCS_DIR / "repo-map.md").write_text(repo_map_md(tag, now))
    print(f"docs tree: {DOCS_DIR} ({len(CONTENT) - len(missing)}/{len(CONTENT)} content files)")
    if missing:
        print(f"warning: missing source files: {', '.join(missing)}", file=sys.stderr)
    print(f"stamped:   {DOCS_DIR / 'repo-map.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

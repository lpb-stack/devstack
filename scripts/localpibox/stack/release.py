"""Stable-release promotion engine (dev branches → stable branches).

Per repo: reset local stable branch to origin/<stable>, merge
origin/<dev> (ff or clean 3-way), push. Unrelated histories (first
release) require --rebase: stable is replaced with the dev history
(force-push). Conflicts leave the repo untouched. devstack only: the
-dev VERSION suffix is stripped on the stable branch and committed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from ..cli import confirm
from ..log import Console
from ..run import run_cmd
from .gitutil import git, git_auth
from .repos import repo_path, stack_repos
from .version import get_version


# ─── Docs readiness ──────────────────────────────────────────────────────────
#
# The docs site (MkDocs + mike) lives on the `docs` branch. Before a stable
# release the docs content must be in sync with dev AND explicitly flagged
# as reviewed: `release docs-ready` merges dev→docs, builds the site for
# local review (mike serve), then commits DOCS_READY=<stable-version> on the
# docs branch. `release promote` gates on that flag so a forgotten docs
# update blocks the release. The main pipeline re-verifies the flag before
# publishing the immutable docs version (docs-publish job).

DOCS_BRANCH = "docs"
DOCS_READY_FILE = "DOCS_READY"
# Files that legitimately differ between dev and docs (site machinery + flag).
DOCS_ONLY_FILES = frozenset({
    "mkdocs.yml", "scripts/generate.py", "DOCS.md", DOCS_READY_FILE,
})
# Paths on dev that are code/machinery, not site content — a change there
# does not invalidate a docs flag (the site never builds them). Anything
# else counts as content. support/docs/ is content even though support/
# holds runtime tools.
_NON_CONTENT_FILES = frozenset({
    ".dockerignore", ".env.example", ".gitignore", "Dockerfile", "VERSION",
    "lpb.conf.env", "lpb.stack.env", "lpb.stack.dev.env", "lpb.stack.main.env",
})
_NON_CONTENT_DIRS = ("scripts/", ".githooks/", ".github/")


def _is_content(path: str) -> bool:
    """True when a dev↔docs tree difference is site content (drift-worthy)."""
    if path in DOCS_ONLY_FILES or path in _NON_CONTENT_FILES:
        return False
    if path.startswith(_NON_CONTENT_DIRS):
        return False
    if path.startswith("support/") and not path.startswith("support/docs/"):
        return False
    return True


def _docs_preview_dir() -> Path:
    """Persistent worktree where the docs site is built for local review."""
    return Path(os.environ.get(
        "LPB_DOCS_PREVIEW",
        str(Path.home() / ".lpb-stack" / "docs-preview"),
    ))


def _stable_version(dev_version: str) -> str:
    """The stable version a promotion of *dev_version* would create."""
    return dev_version[:-len("-dev")] if dev_version.endswith("-dev") else dev_version


def _docs_drift_files(path: Path) -> list[str]:
    """Site-content files differing between origin/dev and origin/docs.

    Machinery (DOCS_ONLY_FILES) and code paths (_NON_CONTENT_*) never appear
    in the site build, so they are not drift; anything else means dev's
    doc content is not in docs.
    """
    out, _err, code = git(path, "diff", "--name-only",
                          "origin/dev", f"origin/{DOCS_BRANCH}")
    if code != 0:
        return []
    return sorted(f for f in out.splitlines() if f and _is_content(f))


def _docs_verdict(flag: str | None, target: str, drift: list[str]) -> str:
    """Docs readiness verdict for a target stable version (pure)."""
    if flag is None:
        return "missing"
    if flag != target:
        return "wrong-version"
    if drift:
        return "stale"
    return "ready"


def _docs_gate_error(docs: dict, all_noop: bool) -> str | None:
    """Blocking message when the promote docs gate fails (None = pass)."""
    if all_noop or docs["verdict"] == "ready":
        return None
    v = docs["verdict"]
    if v == "missing":
        why = (f"no {DOCS_READY_FILE} on the {DOCS_BRANCH} branch")
    elif v == "wrong-version":
        why = (f"flagged for {docs['flagged']}, releasing {docs['target']}")
    elif v == "stale":
        files = docs["drift"][:5]
        more = f" (+{len(docs['drift']) - 5} more)" if len(docs["drift"]) > 5 else ""
        why = f"doc content drifted from dev: {', '.join(files)}{more}"
    else:  # unknown
        why = f"{DOCS_BRANCH} branch not found or fetch failed — cannot verify docs"
    return (f"docs not ready for {docs['target']}: {why} — run "
            f"'lpb-devstack release docs-ready', then re-run promote "
            f"(or override with --force)")


def _docs_release_state(cons: Console | None = None) -> dict:
    """Docs readiness for the next stable release (non-destructive).

    Returns {verdict, target, flagged, flag_sha, drift} where verdict is
    one of missing | wrong-version | stale | ready | unknown.
    """
    path = repo_path("devstack")
    target = _stable_version(get_version())
    state: dict = {"verdict": "unknown", "target": target, "flagged": None,
                   "flag_sha": None, "drift": []}
    if not path.is_dir():
        return state
    if cons is not None:
        cons.info("  fetching docs branch …")
    git_auth(path, "fetch", "origin", "--quiet",
             f"+refs/heads/{DOCS_BRANCH}:refs/remotes/origin/{DOCS_BRANCH}",
             "+refs/heads/dev:refs/remotes/origin/dev",
             timeout=180)
    out, _err, code = git(path, "rev-parse", "--verify", "--quiet",
                          f"origin/{DOCS_BRANCH}")
    if code != 0:
        return state
    state["flag_sha"] = out.strip()[:8]
    out, _err, code = git(path, "show", f"origin/{DOCS_BRANCH}:{DOCS_READY_FILE}")
    if code == 0:
        state["flagged"] = out.strip() or None
    state["drift"] = _docs_drift_files(path)
    state["verdict"] = _docs_verdict(state["flagged"], target, state["drift"])
    return state


def _release_repos() -> list[tuple[str, Path, str, str, str]]:
    """All 6 stack repos: (label, path, dev_branch, main_branch, github_repo)."""
    return [
        (name, repo_path(name), dev_branch, main_branch, f"lpb-stack/{name}")
        for name, dev_branch, main_branch in stack_repos()
    ]


def _merge_tree_conflicts(out: str) -> list[str]:
    """Conflicting file names from `git merge-tree --write-tree --name-only`
    output (tree OID line, then conflict paths, then a blank line)."""
    files = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line or line.startswith(("Auto-merging", "CONFLICT", "warning")):
            break
        files.append(line)
    return files


def _repo_release_state(path: Path, dev_branch: str, main_branch: str,
                        cons: Console | None = None) -> dict:
    """Fetch and gather per-repo promotion state (non-destructive)."""
    # Explicit refspecs: some clones (e.g. config) have restricted fetch
    # configs that would not create refs/remotes/origin/<dev>.
    if cons is not None:
        cons.info(f"  fetching {path.name} …")
    git_auth(path, "fetch", "origin", "--quiet",
             f"+refs/heads/{dev_branch}:refs/remotes/origin/{dev_branch}",
             f"+refs/heads/{main_branch}:refs/remotes/origin/{main_branch}",
             timeout=180)
    dirty, _, _ = git(path, "status", "--porcelain")
    state: dict = {
        "dirty": bool(dirty.strip()),
        "local_ahead": 0,        # local stable commits not on origin
        "origin_dev": "?",
        "origin_main": "?",
        "main_behind_by": None,   # commits on dev not on main (None = unknown)
        "diverged": False,        # main has commits not on dev
        "conflicts": [],          # conflicting paths from the test merge
        "feasibility": "unknown", # aligned|ahead|ff|merge|conflict|unrelated
    }
    out, _, code = git(path, "rev-parse", "--verify", f"origin/{dev_branch}")
    if code == 0:
        state["origin_dev"] = out.strip()[:8]
    out, _, code = git(path, "rev-parse", "--verify", f"origin/{main_branch}")
    if code == 0:
        state["origin_main"] = out.strip()[:8]
        out, _, code = git(path, "rev-list", "--count",
                           f"origin/{main_branch}..{main_branch}")
        if code == 0:
            state["local_ahead"] = int(out.strip())
    if state["origin_dev"] != "?" and state["origin_main"] != "?":
        _, _, code = git(path, "merge-base",
                         f"origin/{main_branch}", f"origin/{dev_branch}")
        if code != 0:
            state["feasibility"] = "unrelated"
        else:
            out, _, code = git(path, "rev-list", "--count",
                               f"origin/{main_branch}..origin/{dev_branch}")
            if code == 0:
                state["main_behind_by"] = int(out.strip())
            out, _, code = git(path, "rev-list", "--count",
                               f"origin/{dev_branch}..origin/{main_branch}")
            if code == 0 and int(out.strip()) > 0:
                state["diverged"] = True
            if state["main_behind_by"] == 0:
                state["feasibility"] = "ahead" if state["diverged"] else "aligned"
            elif state["diverged"]:
                out_mt, _err_mt, code_mt = git(path, "merge-tree", "--write-tree",
                                               "--name-only",
                                               f"origin/{main_branch}",
                                               f"origin/{dev_branch}")
                if code_mt == 0:
                    state["feasibility"] = "merge"
                else:
                    state["conflicts"] = _merge_tree_conflicts(out_mt)
                    if state["conflicts"] == ["VERSION"]:
                        # devstack: main's VERSION is stripped of -dev by
                        # design, so it always conflicts with dev's. The
                        # post-merge strip step rewrites the file, so this
                        # is a clean merge.
                        state["feasibility"] = "merge"
                    else:
                        state["feasibility"] = "conflict"
            else:
                state["feasibility"] = "ff"
    return state


def _repo_action(st: dict, rebase: bool) -> tuple[str, str | None]:
    """Decide the promotion action for a repo: (action, skip_reason)."""
    if st["feasibility"] == "unknown":
        return "unknown", "origin refs not found — check repo/remote"
    if st["feasibility"] == "aligned":
        return "no-op", None
    if st["feasibility"] == "ahead":
        return "no-op", ("stable is AHEAD of dev — verify stable's unique "
                         "commits before promoting")
    if st["local_ahead"] > 0:
        return "skip", (f"local stable branch has {st['local_ahead']} unpushed "
                       f"commit(s) — git branch -D <stable> first, then re-run")
    if st["dirty"]:
        return "skip", "local uncommitted changes (commit/stash first)"
    if st["feasibility"] == "conflict":
        return "conflict", "merge conflict — resolve manually"
    if st["feasibility"] == "unrelated":
        if rebase:
            return "rebase", None
        return "unrelated", ("unrelated histories — re-run with --rebase "
                             "(replaces stable with dev history, force-push)")
    return st["feasibility"], None  # ff | merge


def _main_unique_is_version_only(path: Path, dev_b: str, main_b: str) -> bool:
    """True when main's tree differs from dev only in VERSION (the expected
    stable-branch strip after a promotion, until dev advances again)."""
    out, _err, code = git(path, "diff", "--name-only",
                          f"origin/{dev_b}", f"origin/{main_b}")
    return code == 0 and all(f == "VERSION" for f in out.splitlines() if f)


def _set_commit_author() -> None:
    """Force LocalPibox author identity for git commits made by this process."""
    os.environ["GIT_AUTHOR_NAME"] = "localpibox"
    os.environ["GIT_AUTHOR_EMAIL"] = "localpibox@gmail.com"
    os.environ["GIT_COMMITTER_NAME"] = "localpibox"
    os.environ["GIT_COMMITTER_EMAIL"] = "localpibox@gmail.com"


def cmd_release_status(cons: Console) -> int:
    """Show stable-release readiness across all 6 stack repos."""
    version = get_version()
    cons.info(f"devstack VERSION: {version}  (pipeline: "
              f"{'dev' if '-dev' in version else 'main'})")
    cons.info("")
    problems = 0
    for label, path, dev_b, main_b, gh in _release_repos():
        if not path.is_dir():
            cons.error(f"{label:18s} repo missing at {path}")
            problems += 1
            continue
        st = _repo_release_state(path, dev_b, main_b, cons)
        feas = st["feasibility"]
        if feas == "unknown":
            mark, note = "❌", "origin refs not found (fetch failed?)"
            problems += 1
        elif feas == "aligned":
            mark, note = "✅", "aligned"
        elif feas == "ahead":
            if label == "devstack" and _main_unique_is_version_only(path, dev_b, main_b):
                mark, note = "✅", (f"{main_b} ahead only by the stable VERSION "
                                    f"strip (expected after a release, until "
                                    f"dev advances)")
            else:
                mark, note = "⚠️", f"{main_b} is AHEAD of dev — check its unique commits"
                problems += 1
        elif feas == "ff":
            mark, note = "✅", f"{main_b} {st['main_behind_by']} behind → fast-forward"
        elif feas == "merge":
            mark, note = "✅", f"{main_b} {st['main_behind_by']} behind → clean 3-way merge"
        elif feas == "conflict":
            mark, note = "⚠️", "merge conflict — resolve manually"
            problems += 1
        else:  # unrelated
            mark, note = "⚠️", ("unrelated histories → promote --rebase "
                                "(replaces history, force-push)")
            problems += 1
        if st["dirty"]:
            note += "; LOCAL DIRTY (uncommitted changes will not be promoted)"
            problems += 1
        if st["local_ahead"] > 0:
            note += (f"; LOCAL {main_b} AHEAD ({st['local_ahead']} unpushed "
                     f"commit(s) — delete local branch to promote)")
            problems += 1
        cons.info(f"{mark} {label:18s} {gh}")
        cons.info(f"     {dev_b}={st['origin_dev']}  {main_b}={st['origin_main']}  {note}")
    cons.info("")

    docs = _docs_release_state(cons)
    if docs["verdict"] == "ready":
        dmark, dnote = "✅", (f"ready for {docs['target']} "
                              f"({DOCS_READY_FILE}@{docs['flag_sha']})")
    elif docs["verdict"] == "missing":
        dmark, dnote = "❌", (f"no {DOCS_READY_FILE} on {DOCS_BRANCH} — run "
                             "'lpb-devstack release docs-ready'")
        problems += 1
    elif docs["verdict"] == "wrong-version":
        dmark, dnote = "⚠️", (f"flagged for {docs['flagged']}, releasing "
                             f"{docs['target']} — re-run docs-ready")
        problems += 1
    elif docs["verdict"] == "stale":
        files = docs["drift"][:3]
        more = f" (+{len(docs['drift']) - 3} more)" if len(docs["drift"]) > 3 else ""
        dmark, dnote = "⚠️", (f"doc content drifted from dev: "
                             f"{', '.join(files)}{more} — re-run docs-ready")
        problems += 1
    else:
        dmark, dnote = "⚠️", (f"{DOCS_BRANCH} branch not found or fetch failed — "
                             "cannot verify docs")
        problems += 1
    cons.info(f"{dmark} {'docs':18s} lpb-stack/devstack:{DOCS_BRANCH}")
    cons.info(f"     target={docs['target']}  flag={docs['flagged'] or '—'}  {dnote}")
    cons.info("")
    if problems:
        cons.warn(f"{problems} issue(s) — see above before promoting.")
    else:
        cons.done("All repos ready for stable promotion.")
    return 0


def cmd_release_promote(*, assume_yes: bool, dry_run: bool, rebase: bool,
                        force: bool = False, cons: Console) -> int:
    """Promote dev branches to stable branches across all 6 stack repos.

    Per repo (merge ff or clean 3-way): reset local stable branch to origin,
    merge origin/<dev>. Unrelated histories (re-initialized stable branch):
    with --rebase, replace stable with the dev history (force-push) — the
    first-release mode. On conflict: leave the repo untouched, report.
    devstack only: strip the -dev VERSION suffix on the stable branch.
    Then push all successfully promoted repos. CI (main pipeline) then
    builds/tags once VERSION changes on main (manual tagging — CI never
    bumps; if main's VERSION already matches, re-tag with
    `lpb-devstack tag-repos --branch main`).
    """
    _set_commit_author()
    version = get_version()
    if "-dev" not in version:
        cons.warn(f"local devstack VERSION={version} has no -dev suffix — "
                  "run 'git pull --ff-only' on dev first if possible.")

    # ── Pre-flight ──
    entries: list[tuple[str, Path, str, str, str, dict]] = []
    ok = True
    for label, path, dev_b, main_b, gh in _release_repos():
        if not path.is_dir():
            cons.error(f"{label}: repo missing at {path}")
            ok = False
            continue
        st = _repo_release_state(path, dev_b, main_b, cons)
        entries.append((label, path, dev_b, main_b, gh, st))
        if st["origin_main"] == "?":
            cons.error(f"{label}: origin/{main_b} does not exist")
            ok = False
    if not ok:
        cons.error("Pre-flight failed — aborting, nothing was changed.")
        return 1

    cons.info("")
    cons.info("Stable release plan:")
    for label, _p, dev_b, main_b, gh, st in entries:
        action, skip_reason = _repo_action(st, rebase)
        if skip_reason:
            cons.warn(f"  {gh}: SKIP — {skip_reason}")
        else:
            cons.info(f"  {gh}: {action} (origin/{dev_b} → {main_b})")
    cons.info("  devstack: strip -dev from VERSION on main")
    all_noop = all(_repo_action(st, rebase)[0] == "no-op"
                   for _l, _p, _db, _mb, _gh, st in entries)
    docs = _docs_release_state(cons)
    if all_noop:
        cons.info(f"  docs ({DOCS_BRANCH}): not required (no-op release)")
    else:
        dstate = (f"{docs['verdict']} for {docs['target']}"
                  if docs["verdict"] != "ready"
                  else f"ready for {docs['target']} ({DOCS_READY_FILE}@{docs['flag_sha']})")
        cons.info(f"  docs ({DOCS_BRANCH}): {dstate}")
    if dry_run:
        cons.info("")
        cons.info("Dry run — nothing was changed.")
        return 0

    # ── Docs readiness gate (stable releases ship reviewed docs) ──
    gate = _docs_gate_error(docs, all_noop)
    if gate:
        if not force:
            cons.error(gate)
            return 1
        cons.warn(f"{gate.split(' — run ')[0]} — proceeding (--force)")

    cons.info("")
    if rebase:
        rebased_plan = [gh for _l, _p, _db, _mb, gh, st in entries
                        if _repo_action(st, rebase)[0] == "rebase"]
        msg = (f"Promote to stable branches and push? "
               f"(FORCE-PUSH on: {', '.join(rebased_plan)})")
    else:
        msg = "Promote to stable branches and push?"
    if not assume_yes and not confirm(msg):
        cons.info("Aborted.")
        return 1

    # ── Merge ──
    failures: list[str] = []
    skipped: list[str] = []
    rebased: list[str] = []
    for label, path, dev_b, main_b, gh, st in entries:
        cons.info(f"── {label} ──")
        action, skip_reason = _repo_action(st, rebase)
        if skip_reason:
            cons.warn(f"  {label}: {skip_reason}")
            skipped.append(label)
            continue
        if action == "no-op":
            cons.info(f"  {label}: already aligned — nothing to do")
            continue
        if action == "rebase":
            out, err, code = git(path, "checkout", "-q", "-B", main_b,
                                 f"origin/{dev_b}")
            if code != 0:
                cons.error(f"  {label}: rebase checkout failed: "
                           f"{err.strip() or out.strip()}")
                failures.append(label)
                continue
            rebased.append(label)
            cons.info(f"  {label}: {main_b} reset to origin/{dev_b} "
                      f"(first-release mode, will force-push)")
            continue
        # ff / merge
        out, err, code = git(path, "checkout", "-q", "-B", main_b, f"origin/{main_b}")
        if code != 0:
            cons.error(f"  {label}: checkout {main_b} failed: {err.strip() or out.strip()}")
            failures.append(label)
            continue
        merge_args = ["merge", "--no-edit"]
        if st["conflicts"] == ["VERSION"]:
            # main's stripped VERSION conflicts with dev's by design — take
            # dev's; the devstack strip step rewrites it right after.
            merge_args += ["-X", "theirs"]
        out, err, code = git(path, *merge_args, f"origin/{dev_b}")
        if code != 0:
            git(path, "merge", "--abort")
            git(path, "reset", "-q", "--hard", f"origin/{main_b}")
            cons.error(f"  {label}: MERGE CONFLICT — {main_b} left untouched, "
                       "resolve manually")
            failures.append(label)
            continue
        if "up to date" in (out + err):
            cons.info(f"  {label}: already up to date")
        else:
            cons.info(f"  {label}: merged origin/{dev_b} → {main_b} ({action})")

    # ── devstack VERSION: strip -dev on main ──
    for label, path, dev_b, main_b, gh, st in entries:
        if label != "devstack" or label in failures or label in skipped:
            continue
        vf = path / "VERSION"
        current = vf.read_text().strip()
        if current.endswith("-dev"):
            stable = current[: -len("-dev")]
            vf.write_text(stable + "\n")
            git(path, "add", "VERSION")
            cons.info(f"  devstack: committing VERSION {current} → {stable} "
                      f"on {main_b} …")
            cons.info("  (pre-commit hook runs the test suite — may take a "
                      "while)")
            out, err, code = git(path, "commit", "-m",
                                 f"release: {stable} — stable branch promoted "
                                 f"from dev",
                                 timeout=600)
            if code != 0:
                detail = err.strip() or out.strip() or "unknown error"
                cons.error(f"  devstack: VERSION commit failed: {detail}")
                cons.error("  State: main has the merged dev content; the "
                           "VERSION change is STAGED (not committed).")
                cons.error(
                    f"  Recover: cd {path} && "
                    f"git commit -m 'release: {stable} — stable branch promoted "
                    f"from dev' && git push origin {main_b}"
                )
                failures.append("devstack")
            else:
                cons.info(f"  devstack: VERSION {current} → {stable} (on {main_b})")

    # ── Push ──
    cons.info("")
    cons.info("Pushing stable branches...")
    for label, path, dev_b, main_b, gh, st in entries:
        if label in failures or label in skipped:
            continue
        action, _ = _repo_action(st, rebase)
        if action == "no-op":
            continue
        push_args = ["push", "origin", main_b]
        if label in rebased:
            push_args = ["push", "--force-with-lease", "origin", main_b]
        cons.info(f"  pushing {gh}:{main_b} …")
        out, err, code = git_auth(path, *push_args, timeout=180)
        if code != 0:
            cons.error(f"  {label}: push failed: {err.strip() or out.strip()}")
            failures.append(label)
        else:
            force = " (force)" if label in rebased else ""
            cons.info(f"  pushed {gh}:{main_b}{force}")

    # ── Summary ──
    cons.info("")
    cons.info("Result:")
    for label, path, dev_b, main_b, gh, st in entries:
        if label in failures:
            cons.error(f"  ❌ {gh:35s} failed")
        elif label in skipped:
            cons.warn(f"  ⏭ {gh:35s} skipped")
        elif _repo_action(st, rebase)[0] == "no-op":
            cons.info(f"  ✅ {gh:35s} aligned (no change)")
        else:
            force = " (force)" if label in rebased else ""
            cons.info(f"  ✅ {gh:35s} promoted{force}")
    if skipped:
        cons.warn(f"Skipped: {', '.join(skipped)} — see notes above")
    if failures:
        cons.error(f"Failed: {', '.join(failures)}")
        cons.error("Stable release INCOMPLETE — complete the failing repo "
                   "(recovery steps above), then re-run "
                   "'lpb-devstack release promote' (already-promoted repos "
                   "fast-forward or no-op).")
        return 1
    stable_version = (version[:-len("-dev")] if version.endswith("-dev") else version)
    cons.done(f"Promoted all 6 repos. main's VERSION is now {stable_version}.")
    cons.info(f"CI (main pipeline) now builds :{stable_version}-* / :main-* / :latest-*")
    cons.info("and tags the 5 repos at the stable branches.")
    cons.info("After CI passes:")
    cons.info("  1. lpb-devstack --tag main workspace sync-pins")
    cons.info("  2. pi update --extensions")
    cons.info(f"  (If CI's tag-repos didn't run: lpb-devstack tag-repos --branch main --version {stable_version})")
    cons.info(f"Docs: the main pipeline publishes the stable docs version "
              f"(https://lpb-stack.github.io/devstack/{stable_version}/).")
    return 0


def cmd_release_docs_ready(*, assume_yes: bool, cons: Console) -> int:
    """Flag the docs branch as reviewed for the next stable release.

    Merges origin/dev into the docs branch (dedicated worktree), regenerates
    and builds the site for local review, then — after confirmation — commits
    DOCS_READY=<stable-version> on the docs branch and pushes it.
    `release promote` refuses to run unless this flag is current.
    """
    _set_commit_author()
    path = repo_path("devstack")
    if not path.is_dir():
        cons.error(f"devstack repo missing at {path}")
        return 1
    version = get_version()
    if "-dev" not in version:
        cons.warn(f"local devstack VERSION={version} has no -dev suffix — "
                  "the target stable version may be wrong; run "
                  "'git pull --ff-only' on dev first if possible.")
    target = _stable_version(version)

    out, _err, code = git_auth(path, "fetch", "origin", "--quiet",
                               f"+refs/heads/{DOCS_BRANCH}:refs/remotes/origin/{DOCS_BRANCH}",
                               "+refs/heads/dev:refs/remotes/origin/dev", timeout=180)
    if code != 0:
        cons.error(f"fetch failed: {_err.strip() or out.strip()}")
        return 1
    out, _err, code = git(path, "rev-parse", "--verify", "--quiet",
                          f"origin/{DOCS_BRANCH}")
    if code != 0:
        cons.error(f"origin/{DOCS_BRANCH} not found — nothing to flag")
        return 1

    st = _docs_release_state(None)
    if st["verdict"] == "ready":
        cons.done(f"docs already flagged ready for {target} "
                  f"({DOCS_READY_FILE}@{st['flag_sha']}) — nothing to do")
        return 0

    work = _docs_preview_dir()
    cons.info(f"Preparing docs worktree: {work}")
    git(path, "worktree", "remove", "--force", str(work))  # stale from a prior run
    git(path, "worktree", "prune")
    out, err, code = git(path, "worktree", "add", "--detach", str(work),
                         f"origin/{DOCS_BRANCH}")
    if code != 0:
        cons.error(f"worktree add failed: {err.strip() or out.strip()}")
        return 1

    out, err, code = git(work, "merge", "--no-edit", "origin/dev")
    if code != 0:
        git(work, "merge", "--abort")
        cons.error(f"MERGE CONFLICT merging origin/dev → {DOCS_BRANCH} — "
                  f"resolve manually in {work}")
        return 1
    if "up to date" in (out + err):
        cons.info("docs content already up to date with origin/dev")
    else:
        cons.info(f"merged origin/dev → {DOCS_BRANCH}")

    gen = work / "scripts" / "generate.py"
    if not gen.is_file():
        cons.error(f"{gen} missing — {DOCS_BRANCH} branch out of date?")
        return 1
    out, err, code = run_cmd([sys.executable, str(gen), "--tag", target],
                             cwd=str(work))
    if code != 0:
        cons.error(f"generate.py failed: {err.strip() or out.strip()}")
        return 1
    out, err, code = run_cmd([sys.executable, "-m", "mkdocs", "build"],
                             cwd=str(work))
    if code != 0:
        cons.error(f"mkdocs build failed: {err.strip() or out.strip()}")
        cons.error("Install docs tooling: "
                   "python3 -m pip install --user --break-system-packages "
                   "'mkdocs-material==9.7.7' mike")
        return 1
    cons.info("Site built — note: repo-map/versions pages are re-stamped by "
              "CI after the release tags exist.")
    cons.info(f"  preview:  cd {work} && mike serve   # http://localhost:8000")
    if not assume_yes and not confirm(
            f"Review the site, then flag docs as ready for {target}?"):
        cons.info("Aborted — docs branch not flagged. Re-run when ready.")
        return 1

    flag_file = work / DOCS_READY_FILE
    if flag_file.is_file() and flag_file.read_text().strip() == target:
        cons.info(f"{DOCS_READY_FILE} already = {target} — pushing merged content only")
    else:
        flag_file.write_text(target + "\n")
        git(work, "add", DOCS_READY_FILE)
        out, err, code = git(work, "commit", "-m", f"docs: ready for {target}")
        if code != 0:
            cons.error(f"flag commit failed: {err.strip() or out.strip()}")
            return 1
    out, err, code = git_auth(work, "push", "origin",
                              f"HEAD:refs/heads/{DOCS_BRANCH}", timeout=180)
    if code != 0:
        cons.error(f"push {DOCS_BRANCH} failed: {err.strip() or out.strip()}")
        return 1
    cons.done(f"docs flagged ready for {target} — pushed {DOCS_BRANCH} branch")
    cons.info(f"Preview stays at {work} until the next docs-ready run.")
    return 0

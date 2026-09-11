---
name: lpb-stack-repo-workflow
description: Manage the LocalPibox stack repos — manual versioning (lpb-devstack bump), stable releases, image builds, lpb.py.
---
# LocalPibox Repository Workflow

**Reference documentation, not a task list.** This skill is loaded as
project context so you know how the stack works — loading it is NOT a
request to ship anything. Only run `bump` / `release status` / `promote` /
`docs-ready` commands when the user explicitly asks for a release. All
version numbers in this file are illustrative placeholders (`0.0.N`); the
actual current version always comes from `devstack/VERSION`.

Versioning model: **single-source** (devstack/VERSION), **manual tagging**.
The developer bumps the version with `lpb-devstack bump` (commit + push).
CI builds and tags **only when VERSION changed in the pushed commit** —
code-only pushes run tests only. Git hooks validate only — they never write
VERSION or cross-repo state.

## When to Use

- Onboarding new developers to the LocalPibox stack
- Setting up or debugging CI/CD (build-and-publish.yml)
- Shipping a dev image (VERSION bump → CI build + tag)
- Creating the stable (main) release from dev
- Debugging version/tag/pin alignment issues
- Adding/removing repos from the stack
- Validating Docker image builds for `dev` or `main` targets
- Using `lpb --version`, `lpb --tag`, `lpb.py`, `lpb-config`, `lpb-devstack`

## Versioning Model (manual tagging)

```
Single source: devstack/VERSION
Developer: lpb-devstack bump → commit → push (the release trigger)
CI: VERSION changed in pushed commit → tests → build + publish → tag 4 repos
```

- **devstack/VERSION** — the only VERSION file in the stack (e.g. `0.0.N-lpb-dev`)
- **Format:** dev pipeline `0.x.y-lpb-dev`, main pipeline `0.x.y-lpb`
- **`lpb-devstack bump`** is the **dev release trigger**: before committing a
  VERSION change it gates on every tagged stack repo (all except devstack)
  being on its dev branch, fully pushed, and committed (CI tags the *remote*
  dev heads — unpushed or uncommitted local work would ship invisibly).
  `bump --force` bypasses.
  The docs gate is main-pipeline only (`release promote`).
- **`lpb-devstack bump`** preserves major.minor, increments patch (or
  `--minor` / `--major` / `--set`), keeps the current suffix, and commits
  (`--push` also pushes, triggering CI)
- **CI never writes VERSION** — the `VERSION_CHECK` job gates build/tag on a
  VERSION change in the pushed commit; cron and manual dispatch always build
- **Tags** — created by CI (or `lpb-devstack tag-repos`) on the **other 4
  repos only** (devstack is tracked by its VERSION file, never tagged; the
  retired pi fork is not tagged),
  pointing at the pipeline's branch HEAD
- **`lpb.stack.env`** — `LPB_PI_VERSION` is the mainstream pi **npm version**
  (e.g. `0.84.4`); `LPB_CONFIG_REF` is a **branch name** (`dev`/`main`)
- **Pipeline profiles** — `lpb.stack.dev.env` / `lpb.stack.main.env` override
  the refs per pipeline (`lpb --tag dev|main`)
- **Docker images** — `ghcr.io/lpb-stack/devstack` tagged per pipeline (see CI/CD)
- **package.json** — keeps original fork versions, CI never touches it

## Repository Map

| Repo | Type | dev branch | stable branch |
|---|---|---|---|
| **devstack** | workspace (single source) | `dev` | `main` |
| **config** | workspace (agent preset) | `dev` | `main` |
| **lpb-memory** | workspace + extension | `dev` | `main` |
| **pi** | reference clone (de-forked — image installs pi from npm at `LPB_PI_VERSION`) | — | — |
| **pi-subagents** | extension (upstream npm — de-forked 2026-09-10, not a git repo) | — | — |
| **lemonade-pi-plugin** | extension | `lpb-dev` | `lpb` |

Org: all repos live under **`github.com/lpb-stack`** (migrated from
`localpibox` in Aug 2026). Note: **`localpibox` remains the project name and
the Python package name** (`scripts/localpibox/`, `import localpibox`); only
the GitHub org and GHCR paths use `lpb-stack`.

## Repository Layout

```
Workspace:
  /home/lpb/workspace/devstack            (real clone, single source)
  /home/lpb/workspace/pi                  (reference clone of the retired fork)
  /home/lpb/workspace/lemonade-pi-plugin  (symlink → agent git clone)
  /home/lpb/workspace/lpb-memory          (symlink → agent git clone)

Agent config (cloned from lpb-stack/config by start.sh at container start):
  /home/lpb/.pi/agent/                    (settings.json, AGENTS.md, skills/, agents/)

Extension clones (pi loads these per settings.json pins):
  /home/lpb/.pi/agent/git/github.com/lpb-stack/
      lemonade-pi-plugin, lpb-memory

  pi-subagents is NOT a git clone — it installs from the upstream npm
  package (@tintinweb/pi-subagents) per the settings.json pin.

⚠️ Extensions update at runtime via `pi update --extensions`.
   They are NOT baked into Docker images.
   The image installs mainstream pi from the npm registry (LPB_PI_VERSION);
   workspace/pi is a reference clone of the retired fork (tag pre-defork-0.0.71).
```

## Branch Strategy

- **`dev`** (devstack, config, lpb-memory): primary development. Default on GitHub.
- **`main`** (devstack, config, lpb-memory): stable release branch.
- **`lpb-dev`** (lemonade-pi-plugin): active development from
  upstream + LPB patches. Default on GitHub.
- **`lpb`** (lemonade-pi-plugin): stable branch — receives
  clean merges from `lpb-dev` via the release procedure. Divergence from
  `lpb-dev` is normal during active development.

**Rule:** always work on the default branch (`dev` or `lpb-dev`).

## Commit Author Convention

The only available identity is:

```
localpibox <localpibox@gmail.com>
```

Bump/release commits made by `lpb-devstack` use that identity. CI no longer
commits to the repo (manual tagging).

## Stable Release Procedure (dev → main)

**Docs are gated into the release** — `promote` refuses until the docs
branch is flagged ready for the version being released (`--force`
overrides). Doc content changes on `dev` in place as usual; the one-shot
docs sync + review happens at release time via `docs-ready`.

**`lpb-devstack release` is the tool** (there is no other local version path).

```bash
# 0. Flag docs as reviewed for the release (merge dev→docs, build site,
#    review the local build with
#    `cd ~/.lpb-stack/docs-preview && python3 -m http.server 8000 -d site` →
#    http://localhost:8000, confirm →
#    commits DOCS_READY=<stable-version> on the docs branch + pushes)
lpb-devstack release docs-ready

# 1. Readiness check (all tagged repos + docs verdict, non-destructive, fetches first)
lpb-devstack release status

# 2. Inspect the exact plan without changing anything
lpb-devstack release promote --dry-run

# 3. Promote (interactive confirmation; blocked unless docs are READY)
lpb-devstack release promote
```

What promote does per repo:
- **ff / clean 3-way:** resets local stable branch to `origin/<stable>`,
  merges `origin/<dev>`, pushes
- **unrelated histories** (re-initialized stable branch, first release):
  requires explicit `--rebase` — replaces the stable branch with the dev
  history and force-pushes (`git push --force-with-lease`)
- **conflict:** leaves the repo untouched, reports it
- **dirty local repo:** skipped, reported
- **local stable branch ahead of origin** (unpushed commits): skipped with
  guidance — delete the local branch (`git branch -D <stable>`, only with
  explicit user confirmation) and re-run
- **devstack only:** strips the `-dev` VERSION suffix on `main` and commits
  it (e.g. `0.0.N-lpb-dev` → `0.0.N-lpb`)

After promote, CI (main pipeline) finishes the release — the VERSION change
on `main` is the trigger:
1. Builds `:{v}-cli/web`, `:main-cli/web`, `:latest-cli/web`, `:{sha}-cli/web`
2. Tags the 4 repos on their stable branches (`lpb`/`main`)

Then align the runtime to the stable pipeline:
```bash
lpb-config --tag main sync-pins   # pins → stable version
pi update --extensions
lpb-devstack --tag main validate
```

Flags: `--yes` (skip confirmation), `--dry-run` (plan only), `--rebase`
(first-release mode for unrelated histories), `--force` (promote even if
docs are not flagged ready). Re-runs are safe: promoted
repos fast-forward or no-op.

Docs readiness verdicts (`release status`, checked by `promote`):
- `READY` — `DOCS_READY` on the `docs` branch matches the release version
  and doc content matches `dev`
- `MISSING` — no flag yet → run `lpb-devstack release docs-ready`
- `STALE` — flag for another version, or doc content changed on `dev`
  after flagging → re-run `docs-ready`

## Shipping a Dev Image (the common case)

```bash
# Work happens on dev as usual — CI runs tests on every push (no build).
# When ready to ship: first push/commit any pending work in the other
# 4 repos — bump refuses (dev release gate) until they're all on their
# dev branch, pushed, and committed.
lpb-devstack bump                # current VERSION patch+1 (+ commit)
git push origin dev              # CI sees VERSION change → build + tag
# Or one step (commit + push):
lpb-devstack bump --push
# Verify afterwards:
lpb-devstack workspace status
lpb-devstack validate
```

**The bump must be the TIP of the push.** CI's `VERSION_CHECK` diffs the
pushed tip commit only — commits made after the bump (follow-up fixes,
docs) make the push look tests-only and the build/tag is skipped. If that
happens, run `bump` again so a VERSION change lands on the new tip, and
push. `bump` (without `--push`) warns about this.

## Stack Tools

**`lpb-config`** — config repo manager (in-container, `~/.local/bin/lpb-config`):

```bash
lpb-config status | update | reset [--force] | merge   # config repo
lpb-config render [--force]                             # regen runtime config from templates
lpb-config align                                        # pins → latest GitHub tags
lpb-config sync-pins [--tag dev|main]                   # pins → pipeline's stack VERSION
lpb-config setup                                        # first-run setup wizard
lpb-config memory show | setup                          # lpb-memory config
```

`render` recreates the gitignored runtime files (`settings.json`,
`lpb-memory-config.json`) from the repo's templates. lpb-config auto-renders
after `reset` (force) / `update` / `merge` (non-forcing): without it the
rendered config was unrecoverable after `reset` (start.sh only renders on
first boot, gated by `~/.pi/.initialized`). Non-forcing render never
overwrites — it creates missing files and warns on stale pins; `--force`
merges (user keys/packages preserved, template pins win in settings.json,
local keys win in the memory config).

**`lpb-devstack`** — DevOps workspace tool (container + host):

```bash
lpb-devstack bump [--minor|--major] [--set V] [--no-commit] [--push]
lpb-devstack tag-repos [--branch dev|main] [--version V] [--dry-run]
lpb-devstack workspace status | sync
lpb-devstack validate
lpb-devstack release status | docs-ready | promote [--yes] [--dry-run] [--rebase] [--force]
lpb-devstack validate-hooks     # full pre-commit checks (tests included)

# Pipeline override (dev vs main) on any command:
lpb-devstack --tag main validate
```

Both tools are thin CLIs over the shared `scripts/localpibox/stack/` library
(`gitutil` / `repos` / `version` / `workspace` / `validate` / `release`).

## Settings.json Lifecycle

**Template-driven generation**, not git-tracked:

1. Config repo ships `settings.json.template` with `__LPB_VERSION__` placeholders
2. First boot: `start.sh` generates `settings.json` (replaces placeholders)
3. `lpb-config render` regenerates it on demand (auto after reset/update/
   merge) — this is the recovery path when the rendered file is lost or
   its pins are stale after a stack version move
4. No model/provider preconfigured — user runs `/login lemonade`
5. Pin sync: `lpb-config sync-pins`
   (main pipeline reads the stable version from devstack `origin/main`)
6. `lpb-devstack validate` checks pins match the current stack version
7. Persistent on the host volume — survives container rebuilds

Pins look like:
- `git:github.com/lpb-stack/<repo>@<VERSION>` — stack-versioned fork
  extensions (lemonade-pi-plugin, lpb-memory), e.g. `...lpb-memory@0.0.N-lpb-dev`
- `npm:@tintinweb/pi-subagents@<upstream-version>` — pi-subagents is pinned
  to the **upstream npm package** (fork retired); its version follows
  upstream releases, NOT the stack VERSION. Pin sync leaves it alone;
  `lpb-devstack validate` treats it as a presence-only check.

## lpb-memory Config Lifecycle

Same pattern — template in config repo, user config on host volume:

1. First boot: `start.sh` copies `lpb-memory-config.json.template` → config
2. No model override — uses the main model until the user configures
3. Tune: `lpb-config memory setup` (interactive wizard)
4. Review: `lpb-config memory show`

## Hooks (devstack only, `core.hooksPath=.githooks`)

**pre-commit** — validates BEFORE commit (exit non-zero aborts):
1. devstack's tracked changes are fully staged (unstaged edits would
   silently miss the commit)
2. `python3 scripts/lpb-devstack validate` — the full stack alignment
   check (VERSION, `LPB_PI_VERSION`, extension pins, branch alignment,
   pipeline consistency, config worktree clean + extension-repo WIP
   report). Runs the WORKSPACE copy on purpose: the PATH `lpb-devstack`
   is the image-baked one and stays stale until the next rebuild, so the
   hook always validates with the current logic.
3. `scripts/test_lpb.py` passes (skip with `SKIP_TESTS=1`)

The full test suite in pre-commit is intentional — it guards against
low-quality changes reaching the repo. `lpb-devstack validate-hooks` runs
the same checks on demand.

**commit-msg** — **no-op.** Version bumping is manual (`lpb-devstack bump`).
Git hooks never write VERSION or cross-repo state.

## CI/CD Workflow

`.github/workflows/build-and-publish.yml` (org: `lpb-stack`):

Triggers (no tag triggers — push-to-branch only):
- push to `dev` or `main` (paths: Dockerfile, **VERSION**, support/**,
  scripts/**, workflow) — VERSION changes DO trigger: bump = release
- pull_request to `main` (tests only, no builds/pushes)
- weekly cron (Monday 03:00 UTC) → `:weekly-cli/web` (always builds)
- manual dispatch (`publish_latest`, `no_cache` inputs) (always builds)

Jobs:
1. **VERSION_CHECK** — did the pushed commit change `devstack/VERSION`?
   (cron / manual dispatch are always treated as changed)
2. **test-lpb** — always runs: `scripts/test_lpb.py` +
   `scripts/test_localpibox.py` (each entry point runs the per-target
   sub-suites in `test_*.py`; shared mocks/plumbing in `testharness.py`)
3. **build-cli / build-web** — only if VERSION changed. Reads the VERSION
   file directly (CI never bumps). Publish per pipeline:
   - dev push: `:{v}-cli/web`, `:dev-cli/web`, `:{sha}-cli/web`
   - main push: `:{v}-cli/web`, `:main-cli/web`, `:latest-cli/web`, `:{sha}-cli/web`
   - manual with `publish_latest`: `:latest-cli/web`
   - cron: `:weekly-cli/web`
4. **tag-repos** — after successful builds, only if VERSION changed: tags
   the 4 repos on the pipeline's branches (dev: `lpb-dev`/`dev`, main:
   `lpb`/`main`) using `LPB_STACK_PAT`. Retries transient 5xx with backoff
   and **fails the run if any repo's tag fails** (a partially-tagged stack
   is a release bug — re-running the job is idempotent, 422 = already
   tagged). A missing branch aborts immediately.
5. **docs-publish** — main pipeline only, after tag-repos, only if VERSION
   changed: re-verifies the `DOCS_READY` flag on the `docs` branch matches
   the released VERSION (catches `--force` promotions), then
   `mike deploy <version> latest` + `set-default latest` → `gh-pages`
   branch. Served at `lpb-stack.github.io/devstack/<version>/`.
6. **status** — always runs; passes when builds were skipped (no VERSION
   change), fails otherwise only on build failure

Images: `ghcr.io/lpb-stack/devstack` in two flavours per tag — `…-cli`
(base dev env + Pi CLI) and `…-web` (extends cli + VSCodium server). The bare
`:cli`/`:web` tags do NOT exist — CI only publishes versioned plus
`:dev-*`/`:main-*`/`:latest-*`/`:sha-*` (pulling a bare tag fails with
`manifest unknown`).

The image does NOT use `workspace/pi` — it installs mainstream pi from the
npm registry at `LPB_PI_VERSION` (de-forked 2026-08-31; the fork's last state
is tag `pre-defork-0.0.71` in `lpb-stack/pi`).

## lpb Launcher

- `scripts/lpb` (wrapper) → `scripts/lpb.py` (engine, stdlib-only)
- Shared helpers: `scripts/localpibox/` Python package
  (env/log/run/cli + `stack/` for stack operations)
- `scripts/lpb-config` + `scripts/lpb-devstack` (thin CLIs over
  `localpibox.stack`; installed to `/opt/pi-support/` in the image,
  to `~/.local/bin` on the host)
- `support/build.py` — local image builder (`build.py [cli|web] [--push]`);
  CI does the same inline, this is for local/fork builds
- Installed via `scripts/install.sh` (fetches from the `main` branch —
  so `main` must stay a working stable tree)
- `lpb --tag dev|main|{version}` selects the image tag; `LPB_*` vars from
  `~/.lpb-stack/devstack/` config + `lpb.stack.env`/`lpb.conf.env`

## Creating lpb-dev with an upstream base

```bash
cd /path/to/repo
git remote add upstream <upstream-url> 2>/dev/null; git fetch upstream
git checkout -b lpb-dev <upstream-tag>      # e.g. v0.84.2
git cherry-pick <lpb-commits>...            # or merge, keeping history clean
git branch -f lpb lpb-dev                   # (or keep lpb behind until stable)
git push origin lpb-dev
git push origin lpb --force-with-lease      # explicit user confirmation required
gh api repos/lpb-stack/<repo> --method PATCH -f default_branch=lpb-dev
```

## Cleanup Checklist

Before declaring a repo clean:
- [ ] Default branch set correctly (`dev` or `lpb-dev`)
- [ ] Working on the default branch
- [ ] All commits authored `localpibox <localpibox@gmail.com>`
- [ ] Stale branches deleted (explicit user confirmation for main/dev)
- [ ] Remote under `github.com/lpb-stack` (org migrated Aug 2026)
- [ ] `lpb` stable branch exists (receives the release promote when stable)
- [ ] pre-commit hook active (devstack: `core.hooksPath=.githooks`)
- [ ] No stale org references (`github.com/localpibox`, `ghcr.io/localpibox`)
- [ ] `lpb-devstack validate` passes

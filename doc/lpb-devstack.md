# lpb-devstack Reference

`lpb-devstack` is the **DevOps workspace tool** for the LocalPibox stack —
for developers working on the stack (workspace + GitHub). It covers
VERSION bumping, repo tagging, workspace maintenance, full-stack
validation, and stable-release promotion.

Installed in the devstack container as `~/.local/bin/lpb-devstack`
(`podman exec -it lpb-stack lpb-devstack …`) and via `install.sh` on the
host (see [lpb CLI reference](lpb-cli.md)).

Config repo management inside the container (`status` / `update` / `reset` /
`merge` / `align` / `memory`) lives in **`lpb-config`** — see
[lpb-config reference](lpb-config.md).

## Commands

### VERSION Bumping

| Command | Description |
|---|---|
| `lpb-devstack bump` | Bump patch (`0.0.N-lpb-dev` → `0.0.N+1-lpb-dev`), commit |
| `lpb-devstack bump --minor` | Bump minor (`0.0.9` → `0.1.0`), commit |
| `lpb-devstack bump --major` | Bump major, commit |
| `lpb-devstack bump --set 0.1.0-lpb-dev` | Explicit version |
| `lpb-devstack bump --no-commit` | Write VERSION but don't commit |
| `lpb-devstack bump --push` | Commit **and** push (triggers the CI build + tag) |

The suffix is preserved: on `dev` you bump `0.0.x-lpb-dev`, on `main`
`0.0.x-lpb`. CI builds and tags **only when VERSION changed in the pushed
commit** — the bump commit is the release trigger.

### Repo Tagging

**Tagging is CI's job.** Every push that changes `VERSION` runs the
`tag-repos` job, which tags the 4 repos on the pipeline's branches — on dev
as well as on main. Do not tag locally as part of the normal flow.
`lpb-devstack tag-repos` exists only to complete a release whose CI job
failed (it is idempotent, so re-running is safe) — and unlike CI it needs a
local clone of every repo, so it is rarely the right tool.

| Command | Description |
|---|---|
| `lpb-devstack tag-repos` | Tag the 4 stack repos (excl. devstack) at the committed VERSION (CI-failure recovery only) |
| `lpb-devstack tag-repos --branch dev` | Tag on dev branches (`lpb-dev`/`dev`) |
| `lpb-devstack tag-repos --branch main` | Tag on stable branches (`lpb`/`main`) |
| `lpb-devstack tag-repos --version V` | Explicit version (default: devstack VERSION file) |
| `lpb-devstack tag-repos --dry-run` | Show the plan, change nothing |

Tags are created through the local workspace clones (fetch branch →
push tag ref), so normal git auth applies. Already-existing tags are
skipped (re-runs are safe). A missing branch aborts immediately — a
partially-tagged stack is a release bug.

### Workspace Management

| Command | Description |
|---|---|
| `lpb-devstack workspace status` | Show branches + alignment for all repos |
| `lpb-devstack workspace sync` | Clone missing repos, create symlinks, align branches, pull latest (the single write path) |
| `lpb-config sync-pins` | Sync settings.json pins to the pipeline's stack version |

**Locating the devstack checkout.** The workspace repos live under
`$LPB_WORKSPACE_ROOT` (default `/home/lpb/workspace`), but devstack is found
by *content* (`VERSION` + `scripts/localpibox/stack/`), not by directory
name — a git worktree checked out as `~/workspace/molly` is the devstack repo
just as much as `~/workspace/devstack`. With more than one checkout present
the commands ask which to use; pin one explicitly with
`lpb-devstack --devstack-dir PATH` or `LPB_DEVSTACK_DIR=PATH` (needed for
non-interactive runs). When no checkout is found, `workspace sync` clones one
at the conventional `$LPB_WORKSPACE_ROOT/devstack`.

### Stack Validation

| Command | Description |
|---|---|
| `lpb-devstack validate` | Full stack alignment check (repos, branches, pins, env) |
| `lpb-devstack validate-hooks` | Run the full devstack pre-commit checks (tests included) |

**Host vs container runs.** `validate` is also the pre-commit check, so it runs
wherever the commit happens. Outside a stack container (no `/opt/pi-support`,
no `LPB_CONTAINER_NAME`) the checks that only exist in the container — the
target is the mounted `~/.pi/agent` config repo, the extension clones,
settings.json pins and the container-local model server — are reported as
*skipped*, not as failures. Override with `LPB_STACK_IN_CONTAINER=0|1`.
A devstack **linked worktree** on a feature branch is likewise informational
(`ℹ … dev is in the main clone`): git cannot check the pipeline branch out
twice, and `bump`/`release` enforce alignment where it applies. An unreachable
model server is one skip, never a stack failure.

### Release Promotion

| Command | Description |
|---|---|
| `lpb-devstack release status` | Pre-flight check: all tagged repos + docs flag, non-destructive |
| `lpb-devstack release docs-ready [--yes]` | Flag the docs branch as reviewed for the next stable release |
| `lpb-devstack release promote --dry-run` | Inspect plan without making changes |
| `lpb-devstack release promote` | Promote dev → main (interactive confirmation, blocked until docs are ready) |

**Docs are gated into the release.** The docs site (MkDocs + mike) lives on
the `docs` branch; stable releases ship the docs version too. Before
promoting:

1. `lpb-devstack release docs-ready` — merges `origin/dev` → `docs`, builds
   the site, and (after your local review via
   `cd ~/.lpb-stack/docs-preview && python3 -m http.server 8000 -d site` →
   http://localhost:8000) commits
   `DOCS_READY=<stable-version>` on the `docs` branch and pushes it
2. `release status` shows the docs verdict: `READY` / `MISSING` /
   `WRONG-VERSION` (flag for another version) / `STALE` (doc content changed
   on dev after flagging)
3. `release promote` **refuses** unless docs are `READY` for the version
   being released — `--force` overrides with a warning
4. The main pipeline re-verifies the flag, then publishes the immutable
   docs version (`mike deploy <version> latest`) to the `gh-pages` branch:
   `https://lpb-stack.github.io/devstack/<version>/`

`promote` does per repo (dev branch → stable branch: `dev` → `main` for
devstack/config/lpb-memory, `lpb-dev` → `lpb` for pi-subagents/
lemonade-pi-plugin):
- **Fast-forward / clean merge**: resets the local stable branch to
  `origin/<stable>`, merges `origin/<dev>`, pushes
- **Unrelated histories** (first release): requires `--rebase` — replaces
  the stable branch with the dev history and force-pushes
  (`git push --force-with-lease`)
- **Conflict**: leaves the repo untouched, reports it
- **Dirty local repo**: skipped, reported
- **Local stable branch ahead of origin** (unpushed commits): skipped with
  guidance — delete the local branch (`git branch -D <stable>`, only with
  explicit confirmation) and re-run
- **devstack only**: strips the `-dev` VERSION suffix (e.g.
  `0.0.N-lpb-dev` → `0.0.N-lpb`) and commits it

Flags: `--yes` (skip confirmation), `--dry-run` (plan only), `--rebase`
(first-release mode), `--force` (promote even if docs are not flagged
ready). `docs-ready` accepts `--yes` (skip the review confirmation).

### Pipeline Override

Every command accepts a pipeline flag:

```bash
lpb-devstack --tag main validate          # validate against stable pipeline
lpb-devstack --tag dev workspace sync     # sync dev pipeline repos
```

## Shipping a Dev Image (dev pipeline)

```bash
# 1. Work happens on dev as usual (CI runs tests on every push)
# 2. When ready to ship:
lpb-devstack bump                 # 0.0.N-lpb-dev → 0.0.N+1-lpb-dev (+ commit)
git push origin dev               # CI sees the VERSION change → build + tag
# 3. Verify:
lpb-devstack workspace status
lpb-devstack validate
```

## Stable Release Flow (dev → main)

```bash
lpb-devstack release docs-ready                # merge dev→docs, review site, flag DOCS_READY
lpb-devstack release status                    # readiness check (repos + docs)
lpb-devstack release promote --dry-run         # inspect plan
lpb-devstack release promote                   # dev → stable + push (blocked until docs ready)
# CI (main pipeline) then builds images, TAGS the 4 repos on their stable
# branches (the tag-repos job), and publishes the stable docs version
# (https://lpb-stack.github.io/devstack/<version>/)
# Only if that CI job failed, complete the tagging by hand:
#   lpb-devstack tag-repos --branch main        # idempotent — existing tags are skipped
lpb-config --tag main sync-pins   # pins → stable tag
pi update --extensions
```

## Getting a stack-CLI change into the container

`lpb-devstack` and `lpb-config` on PATH are the **image-baked copies** of
`scripts/` (installed to `/opt/pi-support/`), not the mounted workspace —
edits in `devstack/scripts/` do not change the commands you run until the
image is rebuilt. Two ways, in the normal order of preference:

1. **Commit + bump (CI builds the image).** `lpb-devstack bump` then push —
   CI rebuilds and publishes for the new VERSION (`scripts/**` and `support/**`
   are in the workflow's `paths` filter, so a code-only push runs tests only;
   the VERSION change is what triggers the build — see *Shipping a Dev Image*).
2. **Build locally on the host.** `support/build.py [cli|web]` builds the dev
   image from the working tree (add `--push` to publish; it needs the host's
   podman/docker — there is no build engine inside the container), then
   `lpb --tag dev …` runs it. Use this to test before bumping.

To check behaviour **inside** the container before any rebuild, run the
workspace copy directly — `python3 scripts/lpb-devstack …` — which is exactly
what the devstack pre-commit hook does, on purpose, so validation always uses
the current logic rather than the stale image copy.

## Quick Reference

```bash
# Ship a new dev image
lpb-devstack bump --push

# Where do the repos stand?
lpb-devstack workspace status
lpb-devstack validate

# Full pre-commit gate (stack validation via workspace validate + test suite)
lpb-devstack validate-hooks
```

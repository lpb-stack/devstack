# Design: Manual Tagging & lpb-config Split

> ⚠️ **Point-in-time design record** (2026-08-25). The design it describes is
> implemented and current, but the command lists and details below are a
> snapshot — for the up-to-date references see
> [lpb-devstack](lpb-devstack.md) and [lpb-config](lpb-config.md).

> **Status:** Implemented and running (since 2026-08-20)
> **Last verified:** 2026-08-25 — tools deployed, CI active

---

## What's Implemented

### 1. Tool Split — `lpb-config` + `lpb-devstack`

Two CLI tools ship separately from the same `scripts/` source:

| Tool | Location | Purpose | Ships In Image |
|---|---|---|---|
| `lpb-config` | `scripts/lpb-config` | Config repo management + memory config | ✅ `/opt/pi-support/lpb-config` |
| `lpb-devstack` | `scripts/lpb-devstack` | VERSION bumping, repo tagging, workspace sync, release | ✅ `/opt/pi-support/lpb-devstack` |

**`lpb-config` commands:**
```bash
lpb-config status          # Config repo HEAD, remote, local changes
lpb-config update          # Fetch + fast-forward config repo (safe)
lpb-config reset [--force] # Re-clone config repo (destructive)
lpb-config merge           # Interactive git merge for config repo
lpb-config align           # Sync extension pins to latest GitHub tags
lpb-config sync-pins       # Sync settings.json extension pins to stack VERSION
lpb-config setup           # First-run setup wizard (provider, model, memory)
lpb-config memory show     # Show lpb-memory config
lpb-config memory setup    # Interactive memory config wizard
lpb-config render          # Render config from templates
lpb-config check           # Validate the installation (read-only checklist)
```

**`lpb-devstack` commands:**
```bash
lpb-devstack bump                    # Bump VERSION (patch/minor/major/set)
lpb-devstack tag-repos               # Tag 4 repos with committed VERSION
lpb-devstack workspace status        # Branches + alignment
lpb-devstack workspace sync          # Clone/symlink/align/pull
lpb-devstack validate                # Full stack alignment check
lpb-devstack release status          # Dev→stable readiness
lpb-devstack release docs-ready      # Flag docs branch reviewed for the release
lpb-devstack release promote         # Dev→stable merge + push
lpb-devstack validate-hooks          # Pre-commit validation (tests + checks)
```

**Shared library:** `scripts/localpibox/stack/` — `gitutil.py`, `repos.py`, `version.py`, `workspace.py`, `validate.py`, `release.py`

### 2. CI — VERSION-Driven Pipeline

CI runs on every push to dev/main (path-filtered to code changes).

```
Phase 0: VERSION_CHECK  — Did this commit change VERSION?
Phase 1: test-lpb      — Always runs (fast validation)
Phase 2: build-cli      — Only if VERSION changed
Phase 3: build-web      — Only if VERSION changed
Phase 4: tag-repos      — Only if VERSION changed
Phase 5: status         — Always runs (depends on build* results)
```

**How `VERSION_CHECK` works:**
- Reads commit diff with `git diff-tree --name-only -r` + fallback for merges
- **VERSION changed** → triggers `build-cli`, `build-web`, `tag-repos`
- **No VERSION change** → skips build/tag, only runs tests
- **VERSION-only push** → IS the release trigger (VERSION is in `paths` filter)

**Version reading:** Build jobs read `VERSION` file directly (not from bump-version outputs).

### Key Invariants

- **CI never writes VERSION.** The developer's `lpb-devstack bump` commit is
  the only writer; build jobs read the file, they don't modify it.
- **The bump must be the TIP of the push.** `VERSION_CHECK` diffs only the
  pushed tip commit — commits landed after the bump make the push look
  tests-only and the build/tag is skipped (re-bump on the new tip to recover).
- **Devstack is tracked by VERSION, never tagged.** `tag-repos` covers the
  other 4 repos only.
- **Stable releases are docs-gated.** `release promote` refuses until the
  `docs` branch carries `DOCS_READY=<stable-version>` (set by `release
docs-ready`); the main pipeline re-verifies the flag before publishing.

### 3. Pre-commit — Full Check Intact

The pre-commit hook runs the full `test_lpb.py` suite. No changes — this is intentional
as a safeguard against AI-generated garbage reaching the repository.

---

## Design Decisions (Implemented)

| Decision | What | Why |
|---|---|---|
| VERSION in `paths` filter | VERSION-only push triggers CI | Plain `lpb-devstack bump` + push is the release trigger |
| Cron/manual always build | `VERSION_CHECK` outputs `changed=true` | Preserves weekly-cron and `:latest` dispatch behavior |
| `_stack_lib.py` → `localpibox/stack/` | Full stack operations in package | Both CLIs stay thin (~500/~400 lines) |
| Extensionless CLI names | `scripts/lpb-config` not `.py` | Test harness loads via `SourceFileLoader` |
| `tag-repos` via local workspace | Uses workspace clones, not GitHub API | No raw-SHA push without local ODB objects |
| Merge commit handling | `git diff-tree` → first-parent fallback | Merges show no diff by default |

---

## Architecture

```
devstack/
├── scripts/                          ← single source for CLIs + shared package
│   ├── lpb-config                    ← config repo manager (~500 lines)
│   ├── lpb-devstack                  ← DevOps workspace tool (~400 lines)
│   ├── install.sh                    ← installs both CLIs
│   └── localpibox/stack/             ← shared library package
│       ├── gitutil.py
│       ├── repos.py
│       ├── version.py
│       ├── workspace.py
│       ├── validate.py
│       └── release.py
└── .githooks/
    └── pre-commit                    ← full test suite (unchanged)
```

### Scope Decision Matrix

| Feature | `lpb-config` | `lpb-devstack` | Reason |
|---|:---:|:---:|---|
| Config repo management | ✅ | ❌ | Ships in image, needed inside container |
| Extension pin alignment | ✅ | ❌ | Cross-repo, image-usable |
| Extension pin sync | ✅ | ❌ | Operates on settings.json (config) |
| Memory config | ✅ | ❌ | Image-usable, needed inside container |
| VERSION bumping | ❌ | ✅ | Devstack-specific, workspace tool |
| Tag repos | ❌ | ✅ | Dev-time operation |
| Workspace sync/status | ❌ | ✅ | Developer workspace maintenance |
| Stack validation | ❌ | ✅ | Dev-time operation |
| Release promote | ❌ | ✅ | Dev→stable promotion |
| Pre-commit validation | ❌ | ✅ | Dev-time operation |

# OpenSpec Setup (install-openspec)

> Installed: `support/install-openspec.py`  
> Purpose: Bootstrap OpenSpec spec-driven development in a project  
> Platform: Any (opt-in, runs in current workspace)

---

## What It Does

`install-openspec` bootstraps OpenSpec (by Fission AI) in a target project, enabling spec-driven development workflows:

1. **Installs OpenSpec CLI** — installs `@fission-ai/openspec` globally (idempotent, 3 retries)
2. **Notes expected defaults** — assumes OpenSpec's built-in defaults (delivery=both, profile=core); no config is written
3. **Initializes the project** — runs `openspec init --tools pi` (or updates if already initialized)
4. **Verifies installation** — checks generated files and Pi command discovery

This enables the spec-driven development workflow:
- `/opsx:explore` — Think through an idea before coding
- `/opsx:propose "name"` — Create a change plan with specs
- `/opsx:apply` — Implement from tasks.md
- `/opsx:archive` — Merge specs, file away

---

## Installation Process

### Step 1: OpenSpec CLI Install

```
Package:  @fission-ai/openspec
Target:   ~/.npm-global/bin/openspec (via npm config set prefix)
```

- Checks if `openspec` is already installed (idempotent)
- If not, runs `npm install -g @fission-ai/openspec@latest`
- Retries up to 3 times with 5s delay between failures
- Sets npm prefix to `~/.npm-global` (Dockerfile sets this but .npmrc gets overwritten)

### Step 2: Defaults (assumed, not written)

```
Deliverables:  both (specs + changes)
Profile:       core
```

The script does not write OpenSpec configuration — it assumes OpenSpec's
built-in defaults:
- `delivery: both` — both spec files and change proposals are maintained
- `profile: core` — standard spec-driven development profile

If your OpenSpec install uses different defaults, adjust them with
`openspec config` after installation.

### Step 3: Project Initialization

```
Runs: openspec init --tools pi  (in target directory)
```

- If `openspec/` directory doesn't exist, runs full initialization
- If `openspec/` already exists, runs `openspec update` instead
- Generates spec templates, config, and command files for Pi integration

### Step 4: Verification

The script verifies:
- `openspec/` directory exists with specs + changes
- `config.yaml` exists (project config)
- `.pi/prompts/` contains OpenSpec command files (opsx-*.md)
- `.pi/skills/` has skill directories
- Pi can discover the propose command

---

## Usage

`install-openspec` is installed in the PATH (user-space, no root/sudo needed).

### Install in Current Directory

```bash
install-openspec
```

### Install in Specific Directory

```bash
install-openspec /path/to/project
```

### Source Files

| File | Purpose |
|---|---|
| `support/install-openspec.py` | Python script — main logic |
| `~/.local/bin/install-openspec` | User-space CLI wrapper (in PATH) |

### What's Generated

| Path | Purpose |
|---|---|
| `openspec/` | Spec files and change tracking |
| `openspec/config.yaml` | Project configuration |
| `.pi/prompts/opsx-*.md` | Pi command files for OpenSpec |
| `.pi/skills/` | OpenSpec skill directories |

### Post-Install Commands

After installation, these Pi commands become available:

| Command | Purpose |
|---|---|
| `/opsx:explore` | Think through an idea before coding |
| `/opsx:propose "name"` | Create a change plan with specs |
| `/opsx:apply` | Implement from tasks.md |
| `/opsx:archive` | Merge specs, file away |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  install-openspec.py                                               │
│                                                                     │
│  1. resolve_target_dir(target)                                     │
│     → Resolve to absolute path                                     │
│                                                                     │
│  2. install_openspec(cons)                                         │
│     → Check if already installed                                   │
│     → npm install -g @fission-ai/openspec@latest (3 retries)       │
│                                                                     │
│  3. configure_openspec(cons)                                       │
│     → Notes assumed defaults: delivery=both, profile=core          │
│                                                                     │
│  4. init_openspec(target, cons)                                    │
│     → If openspec/ missing: init --tools pi                        │
│     → If openspec/ exists: update                                  │
│                                                                     │
│  5. verify_installation(target, cons)                              │
│     → Check openspec/ directory                                    │
│     → Check config.yaml                                            │
│     → Check .pi/prompts/ command files                             │
│     → Check .pi/skills/ directories                                │
│     → Verify Pi can discover propose command                       │
└─────────────────────────────────────────────────────────────────────┘
```

---

## OpenSpec Workflow

OpenSpec is a spec-driven development tool that helps structure change proposals before implementation:

### 1. Explore

```
/opsx:explore
```

Think through an idea before committing to a change. Documents:
- Problem statement
- Proposed solution
- Alternatives considered
- Risks and trade-offs

### 2. Propose

```
/opsx:propose "my-change"
```

Create a formal change plan:
- Generates `openspec/changes/my-change/` directory
- Creates spec diffs (what changes)
- Creates tasks.md (implementation steps)
- Creates plan.md (high-level approach)

### 3. Apply

```
/opsx:apply
```

Implement the change:
- Reads tasks.md from the proposal
- Implements tasks one by one
- Updates specs as implementation progresses

### 4. Archive

```
/opsx:archive
```

Merge the change:
- Updates spec files with final state
- Files away the change directory
- Removes the proposal from active tracking

---

## Current Status

| Component | Status | Version |
|---|---|---|
| OpenSpec CLI | ✅ Available | @fission-ai/openspec@latest |
| Project Defaults | ℹ️ Assumed | delivery=both, profile=core (OpenSpec built-ins) |
| Pi Commands | ✅ Discovered | opsx:explore, propose, apply, archive |
| Specs Directory | ✅ Ready | openspec/ |

**Last verified:** 2026-08-25

---

## Troubleshooting

### OpenSpec Already Installed

The script is idempotent — if OpenSpec is already installed, it skips:
```
OpenSpec @fission-ai/openspec already installed, skipping
```

### Installation Fails After 3 Retries

**Symptom:** `Failed to install OpenSpec after 3 attempts`

**Causes:**
- Network issues
- npm registry unreachable
- ~/.npm-global not writable

**Fix:**
```bash
# Check network
curl -I https://registry.npmjs.org/openspec
# Check npm config
npm config get prefix
# Manually install
npm install -g @fission-ai/openspec
```

### ~/.config Ownership Issue (Container)

**Symptom:** `EACCES: permission denied` when OpenSpec writes to ~/.config/openspec/

**Fix:** The script automatically fixes this:
```bash
sudo chown $(id -u) ~/.config
```

### Pi Doesn't See OpenSpec Commands

**Symptom:** `/opsx:*` commands not available in Pi

**Check:**
```bash
ls -la ~/.pi/prompts/opsx-*.md
ls -la ~/.pi/skills/
```

If missing, re-run:
```bash
install-openspec
```

---

## Why This Matters

OpenSpec enables spec-driven development for the LocalPibox stack:
- Structured change proposals before implementation
- Clear spec diffs showing what changes
- Task-based implementation tracking
- Audit trail of all changes

This is particularly valuable for:
- Managing the 6-repo stack (each repo has specs)
- Tracking breaking changes across upstream updates
- Ensuring consistency between fork patches and upstream
- Documenting rationale for design decisions

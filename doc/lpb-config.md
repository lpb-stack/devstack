# lpb-config Reference

`lpb-config` is the **config repo manager** for the LocalPibox stack. It
manages the config repo (`~/.pi/agent/`) that ships inside the devstack
image and lives on the host volume. It is installed in the container as
`~/.local/bin/lpb-config` (run it via
`podman exec -it lpb-stack lpb-config …` from the host).

Dev-time stack operations (VERSION bumping, repo tagging, workspace
maintenance, validation, stable releases) live in **`lpb-devstack`** —
see [lpb-devstack reference](lpb-devstack.md).

## Commands

### Config Repo Management

| Command | Description |
|---|---|
| `lpb-config status` | Show config repo HEAD, remote, local changes |
| `lpb-config update` | Fetch + fast-forward config repo (safe: refuses on local changes) |
| `lpb-config reset [--force]` | Re-clone config repo, destroy local changes (with confirmation) |
| `lpb-config merge` | Open git merge flow for advanced users (conflict resolution) |

### Align & Pin Sync

| Command | Description |
|---|---|
| `lpb-config align` | Update extension pins in settings.json to latest GitHub tags |
| `lpb-config sync-pins` | Sync extension pins to the pipeline's stack VERSION (`--tag main` for the stable version) |

### Memory Management

| Command | Description |
|---|---|
| `lpb-config memory show` | Display current lpb-memory configuration |
| `lpb-config memory setup` | Interactive wizard to configure lpb-memory |

### First-Run Setup

| Command | Description |
|---|---|
| `lpb-config setup` | The **same wizard** as `lpb setup` on the host: config repo, lemonade provider (auth.json), default model (settings.json), lpb-memory config. Every step is validated live (HTTP probes); failures loop with the error visible. Re-run any time to correct a bad configuration — current values are pre-filled. `--reconfigure` forces a re-run |
| `lpb-config setup --non-interactive` | Same, no prompts — `LEMONADE_BASE_URL` / `LEMONADE_API_KEY` env + defaults. First failure aborts with a precise error and writes nothing. This is the `start.sh` fallback for boots where the host wizard did not run |
| `lpb-config check` | Validate the installation (read-only checklist: config repo, settings, credentials, live server probe) — the in-container twin of `lpb doctor` |

### Pipeline Override

`sync-pins` is the only pipeline-sensitive command: `lpb-config --tag main
sync-pins` syncs pins to the stable stack version. Without `--tag`, the
pipeline is detected via `detect_pipeline()` — `LPB_IMAGE_TAG` env, then the
devstack `VERSION` file, then `LPB_VERSION` — the same resolution
`lpb-devstack` uses. All other commands operate on the config repo only.

## Settings.json Lifecycle

Settings.json is **template-driven**, not git-tracked:

1. Config repo ships `settings.json.template` with `__LPB_VERSION__` placeholders
2. Before the first start, the setup wizard (`lpb setup` on the host, or
   `lpb-config setup` in a shell) renders `settings.json` and writes the
   lemonade provider + default model — `start.sh` re-renders on first boot
   as a fallback (`lpb-config render`)
3. Model/provider are configured interactively with live validation — see
   the First-Run Setup section above (no `/login` needed for lemonade)
4. Pin sync: `lpb-config sync-pins` (main pipeline reads
   stable version from devstack `origin/main`)
5. `lpb-devstack validate` checks pins match the current stack version
6. Settings.json persists on the host volume — survives container rebuilds

Example pin: `git:github.com/lpb-stack/pi-subagents@0.0.N-lpb-dev`

## lpb-memory Config Lifecycle

Same template-driven pattern:

1. The setup wizard configures it (mode, transport, background model,
   context limits) — pre-filling the model override with the selected
   default model; `start.sh` falls back to a plain template copy when the
   wizard did not run
2. Non-interactive mode uses the template + the default-model override
3. Tune any time: `lpb-config memory setup` (same wizard step, standalone)
4. Review: `lpb-config memory show`

## Quick Reference

```bash
# Check config repo state
lpb-config status

# Pull latest config changes (safe fast-forward)
lpb-config update

# Discard local config changes (destructive)
lpb-config reset

# Resolve a config repo merge conflict
lpb-config merge

# Update extension pins to latest GitHub tags
lpb-config align

# Check / configure the memory extension
lpb-config memory show
lpb-config memory setup

# Run / validate the initial setup
lpb-config setup
lpb-config check
```

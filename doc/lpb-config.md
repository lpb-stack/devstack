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

### Align

| Command | Description |
|---|---|
| `lpb-config align` | Update extension pins in settings.json to latest GitHub tags |

### Memory Management

| Command | Description |
|---|---|
| `lpb-config memory show` | Display current lpb-memory configuration |
| `lpb-config memory setup` | Interactive wizard to configure lpb-memory |

### First-Run Setup

| Command | Description |
|---|---|
| `lpb-config setup` | Interactive first-run setup: lemonade provider (auth.json), default model (settings.json), lpb-memory config. Runs automatically on first boot (start.sh); idempotent — re-run with `--reconfigure` to change server/model |
| `lpb-config setup --non-interactive` | Same, no prompts — `LEMONADE_BASE_URL` / `LEMONADE_API_KEY` env + defaults. On TTY-less first boots the `lpb` launcher passes them in (first-boot prompt in `--ssh`/`--web` modes, see `lpb-cli.md`) |

### Pipeline Override

`--tag dev|main` is accepted on any command (validates the value) for
compatibility; the remaining commands operate on the config repo
regardless of pipeline.

## Settings.json Lifecycle

Settings.json is **template-driven**, not git-tracked:

1. Config repo ships `settings.json.template` with `__LPB_VERSION__` placeholders
2. First boot: `start.sh` generates `settings.json` (replaces placeholders)
3. No model/provider preconfigured — user runs `/login lemonade`
4. Pin sync: `lpb-devstack workspace sync-pins` (main pipeline reads
   stable version from devstack `origin/main`)
5. `lpb-devstack validate` checks pins match the current stack version
6. Settings.json persists on the host volume — survives container rebuilds

Example pin: `git:github.com/lpb-stack/pi-subagents@0.0.57-lpb-dev`

## lpb-memory Config Lifecycle

Same template-driven pattern:

1. First boot: `start.sh` copies `lpb-memory-config.json.template` → config
   (or `lpb-config setup` configures it interactively during the first-run
   wizard, pre-filling the model override with the selected default model)
2. No model override — uses the main model until user configures
3. Tune: `lpb-config memory setup` (interactive wizard)
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
```

# lpb CLI Reference

`lpb` is the user-facing launcher for the LocalPibox devstack. It wraps
**podman** (or docker) to manage the container lifecycle, resolve image
tags, and self-update. It is stdlib-only Python — no dependencies to install.

## Installation

```bash
curl -fsSL https://raw.githubusercontent.com/lpb-stack/devstack/main/scripts/install.sh | bash
```

Installs (no sudo required) into `~/.local/bin`:

- `lpb` — bash wrapper
- `lpb.py` — Python engine
- `lpb-config`, `lpb-devstack` + the shared `localpibox` package
  (the setup wizard behind `lpb setup` / `lpb doctor`)

…and copies the stack config files (`lpb.stack.env`, `lpb.conf.env`,
`VERSION`) to `~/.lpb-stack/devstack/`. Make sure `~/.local/bin` is on your
`PATH`.

## Commands

| Command | Description |
|---|---|
| `lpb [/path/to/project]` | Start a **Pi CLI session** (foreground). No path → last project, or `~` if none yet |
| `lpb --web [/path]` | Start **VSCodium** (background), prints the connection URL |
| `lpb --shell [/path]` | Interactive bash shell inside the container |
| `lpb --ssh [pubkey\|path] [project]` | Start an sshd server in the container for remote login (key auto-detected from `~/.ssh` when omitted) |
| `lpb --ssh --ssh-password [pw]` | SSH password login (random if omitted, shown once; can combine with key auth) |
| `lpb --stop` | Stop the container |
| `lpb --remove` | Stop + remove container + state dirs |
| `lpb --logs` | Stream container logs |
| `lpb --update` | Self-update the launcher + stack tools + pull the latest image(s) |
| `lpb setup` | Run the **initial setup wizard** (server, key, model, memory) — any time, all modes |
| `lpb doctor` | Validate the installation (read-only checklist) |
| `lpb --config` | Show config file location + model provider status |
| `lpb --version` | Show installed launcher version |
| `lpb --help` | Full usage |

Positional aliases (no `--` needed): `lpb logs`, `lpb stop`, `lpb update`,
`lpb remove`, `lpb config`, `lpb setup`, `lpb doctor`, `lpb version`, `lpb help`.

### Pi passthrough

Everything after `--` goes to Pi:

```bash
lpb /myproject -- -p "summarize this repo"   # non-interactive run
lpb /myproject -- --continue                 # continue the last session
lpb /myproject -- --thinking high            # pass any pi flag
```

### VSCodium options (`--web` mode)

```
--host <HOST>          listen host (default: from .env or localhost)
--port <PORT>          port (default: from .env or 3000)
--token <TOKEN>        connection token (default: auto-generated)
--new-token            generate a fresh token
--without-token        hide the token in the printed URL
```

### SSH mode (`--ssh`)

- **Key auto-detection**: `lpb --ssh` with no argument scans the *host* user
  profile (`~/.ssh/*.pub`). One key → used (confirmed on a TTY); several →
  numbered menu; none → error with a hint. Non-interactive: one key is used
  automatically, several → explicit selection required.
- **Explicit key still wins**: `lpb --ssh <pubkey|path>` — a literal inline
  key or a path to a `.pub` file (tilde expanded). A value that is an
  existing *directory* is treated as the project dir (the key menu still
  appears); anything else is an error — a nonexistent path is never silently
  accepted as a key.
- **First-run wait**: on the first boot the container bootstraps (config-repo
  clone, volume chown, provider setup) *before* sshd starts, which can take a
  few minutes. `lpb` detects first run (no `.initialized` in the state dir),
  waits up to 5 minutes with progress notes (30 s on warm starts), and only
  then reports a failure — with the last container log lines and a
  first-run-aware hint (`lpb --logs` shows the bootstrap finishing; sshd
  starts automatically once it completes).
- **Password login**: `lpb --ssh --ssh-password` (random, printed once) or
  `lpb --ssh --ssh-password <pw>` (user-chosen). The container user's password
  is set at start (`chpasswd`) and `PasswordAuthentication` is enabled in the
  generated `sshd_config`. Key auth remains the default — password auth is
  opt-in (the container runs `network=host`, so a password is reachable from
  the LAN).
- **authorized_keys is append+dedup**: keys added manually (or in a previous
  session) survive container recreation.
- **`LPB_SSH_PORT`** (default `2222`): forwarded to the container; the
  connect line (`ssh -p <port> lpb@<host>`) is printed when the server starts.

### Initial setup — one wizard for all modes

Before the **first container start — in every mode (cli, shell, ssh, web)** —
`lpb` validates the model-provider configuration. The provider state is
checked live (stored credentials in `LPB_STATE_DIR/agent/auth.json` + a real
HTTP probe of the Lemonade server), not a one-shot first-boot flag:

- **healthy** → silent; the stored URL/key are passed to the container.
- **missing or broken + TTY** → the **setup wizard** runs (config repo →
  server URL → API key → default model → lpb-memory config). Every step is
  validated live (real HTTP probes) and failures loop back with the raw
  error visible; `a` continues anyway (flagged), `q` aborts (nothing is
  written, no container starts). The wizard writes `auth.json`,
  `settings.json` and `lpb-memory-config.json` directly into the state dir,
  so the configuration is **persisted before the first use**.
- **missing or broken + non-TTY** (or `lpb --non-interactive`) → env
  passthrough (`LPB_LEMONADE_BASE_URL` / `LPB_LEMONADE_API_KEY` / `lpb.conf.env`)
  with a visible note; the container runs a non-interactive fallback.

Because validation is live, a server that moves or a key that changes is
detected on the next start and re-offers the wizard — configuration is never
silently stale. The launch summary always ends with a model status line
(✓ provider / ⚠ fix with `lpb setup`).

On-demand commands (any mode, any time):

- `lpb setup` — run the wizard interactively (the way to correct a bad
  configuration; current values are pre-filled).
- `lpb doctor` — read-only checklist: config repo, rendered config, provider
  credentials, live server probe, container runtime, image.

Inside the container the same wizard is available as `lpb-config setup`
(and `lpb-config check` for the checklist); `start.sh` runs it
non-interactively as a fallback on boots where the host wizard did not run,
and a missing provider is reported in the container logs on **every boot**
until fixed — not swallowed. Reconfigure any time with
`lpb-config setup --reconfigure`.

## Image Selection

Two image flavours are published to `ghcr.io/lpb-stack/devstack`:

- **`…-cli`** — dev environment + Pi CLI
- **`…-web`** — extends `-cli` with the VSCodium server

### Selecting a pipeline

| Selector | Image |
|---|---|
| `--tag dev` (or `--dev`) | latest dev-pipeline version (`0.0.x-lpb-dev-…`) |
| `--tag main` (or `--main`) | latest stable version (`0.0.x-lpb-…`) |
| `--tag latest` | same as `main` |
| `--tag 0.0.N-lpb-dev` | pin to an exact version |
| `LPB_IMAGE_TAG=…` | persistent env-var override |
| *(no tag)* | last-used version (pinned in `~/.lpb-stack/devstack/last-version`), else the stable pipeline |

### Resolution rules

1. With a versioned tag, `lpb` pulls `ghcr.io/lpb-stack/devstack:<tag>-cli`
   and `:<tag>-web` (only what the selected mode needs).
2. For `--tag dev|main`, it reads the remote `VERSION` file of that branch
   to get the exact version; offline, it falls back to the cached
   last-version, then to the floating tags `:dev-*` / `:main-*` /
   `:latest-*`.
3. The **bare `:cli`/`:web`/`:latest` tags are never used** — CI does not
   publish them, so pulling them always fails with `manifest unknown`.

Tags CI publishes: `:{v}-cli/web`, `:dev-cli/web`, `:main-cli/web`,
`:latest-cli/web`, `:{sha}-cli/web`.

### Resolution example

```
User runs:  lpb --main /project

1. resolve_web_image("main") → reads remote main-branch VERSION (0.0.N-lpb)
2. image = ghcr.io/lpb-stack/devstack:0.0.N-lpb-web
3. podman pull (if not cached)
4. podman run --name lpb-stack --network host \
     -v /project:/home/lpb/workspace/project \
     -v ~/.lpb-stack/state:/home/lpb/.pi \
     -v ~/.lpb-stack/agent-browser:/home/lpb/.agent-browser \
     ghcr.io/lpb-stack/devstack:0.0.N-lpb-web
5. pins 0.0.N-lpb in ~/.lpb-stack/devstack/last-version
```

## Configuration

`lpb` reads its own config from `~/.lpb-stack/devstack/`:

| File | Purpose |
|---|---|
| `config` | User overrides (shell-syntax env assignments) |
| `VERSION` | Launcher/stack version (kept in sync by `lpb --update`) |
| `last-version` | Last used stack version (pin for tag-less runs) |
| `last-project` | Last project dir (for bare `lpb`) |
| `token` | Persisted VSCodium token |
| `projects/` | Per-project state |

Environment priority (highest → lowest):

1. Shell environment (`export LPB_…`)
2. `~/.lpb-stack/devstack/config`
3. `lpb.stack.env` / pipeline profile (`lpb.stack.dev.env` / `lpb.stack.main.env`)
4. `lpb.conf.env` (baked into the image)
5. Hardcoded fallbacks

## Environment Variables

Launcher-level variables (see [Environment variables](env-vars.md) for the
full reference including the `LPB_` bridge and in-container vars):

| Variable | Default | Purpose |
|---|---|---|
| `LPB_IMAGE_TAG` | — | Persistent pipeline/version override (`dev`, `main`, `latest`, or a version) |
| `LPB_STATE_DIR` | `~/.lpb-stack/state` | Host dir mounted to `/home/lpb/.pi` |
| `LPB_BROWSER_DIR` | `~/.lpb-stack/agent-browser` | Host dir for browser sessions |
| `LPB_CONTAINER_NAME` | `lpb-stack` | Container name |
| `LPB_IMAGE_CLI` / `LPB_IMAGE_WEB` | lpb-stack images | Last-resort fallback image names (forks) |
| `GHCR_USERNAME` | `lpb-stack` | Registry user for pulls |

## Self-Update

`lpb --update` re-fetches `lpb` + `lpb.py` from GitHub and pulls the latest
image(s) for the selected pipeline. The source branch follows the tag:
`*-dev` tags pull from the `dev` branch, everything else from `main` — so a
launcher installed from `main` can track `dev` simply by choosing the tag.
(Install from `main` via `install.sh` — that's why `main` must stay a
working stable tree.)

## Examples

```bash
lpb ~/projects/myapp                        # Pi CLI (stable pipeline, or last pin; --dev for dev)
lpb --main ~/projects/myapp                 # stable pipeline
lpb --tag 0.0.N-lpb-dev ~/projects/myapp   # pin to an exact version
lpb --web --port 8080 ~/projects/myapp      # VSCodium on port 8080
lpb --shell ~/projects/myapp                # bash inside the container
lpb ~/projects/myapp -- -p "fix the bug"    # one-shot pi run
lpb update                                  # self-update + pull images
lpb logs                                    # stream logs
```

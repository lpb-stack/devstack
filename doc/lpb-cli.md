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

…and copies the stack config files (`lpb.stack.env`, `lpb.conf.env`,
`VERSION`) to `~/.lpb-stack/devstack/`. Make sure `~/.local/bin` is on your
`PATH`.

## Commands

| Command | Description |
|---|---|
| `lpb [/path/to/project]` | Start a **Pi CLI session** (foreground). No path → last project, or `~` if none yet |
| `lpb --web [/path]` | Start **VSCodium** (background), prints the connection URL |
| `lpb --shell [/path]` | Interactive bash shell inside the container |
| `lpb --ssh [pubkey\|path] [/path]` | Start an sshd server in the container for remote login (key auto-detected from `~/.ssh` when omitted) |
| `lpb --ssh --ssh-password [pw]` | SSH password login (random if omitted, shown once; can combine with key auth) |
| `lpb --stop` | Stop the container |
| `lpb --remove` | Stop + remove container + state dirs |
| `lpb --logs` | Stream container logs |
| `lpb --update` | Self-update the launcher + pull the latest image(s) |
| `lpb --config` | Show config file location |
| `lpb --version` | Show installed launcher version |
| `lpb --help` | Full usage |

Positional aliases (no `--` needed): `lpb logs`, `lpb stop`, `lpb update`,
`lpb remove`, `lpb config`, `lpb version`, `lpb help`.

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
- **Explicit key still wins**: `lpb --ssh <pubkey|path>` (literal key or file).
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

### First-boot Lemonade prompt (`--ssh` / `--web`)

On the **first boot of a fresh state volume** (no `~/.pi/.initialized` in
`LPB_STATE_DIR`), detached-server modes run the container's setup
non-interactively — so `lpb` resolves your Lemonade server on the host side
first:

1. URL from `LPB_LEMONADE_BASE_URL` / `LEMONADE_BASE_URL` env or
   `lpb.conf.env` (default `http://127.0.0.1:13305` when unset).
2. Health probe (`GET <url>/api/v1/models`, 3 s). Unreachable on a TTY →
   you're offered to type the real host (re-probed until it answers or you
   press Enter to keep the current one); reachable → silent.
3. API key is asked with the configured value (or `lemonade`) as default.

The resolved URL + key are passed to the container
(`LPB_LEMONADE_BASE_URL` / `LPB_LEMONADE_API_KEY`), where the first-boot
hook (`lpb-config setup --non-interactive`) writes `auth.json` + default
model. Foreground Pi/shell modes skip the host prompt — the in-container
interactive wizard handles it, pre-filled from the same env. Non-TTY
(hosts/CI) and already-initialized volumes: no prompt, static passthrough
only. Reconfigure any time inside the container with
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
| `--tag 0.0.55-lpb-dev` | pin to an exact version |
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

1. resolve_web_image("main") → reads remote main-branch VERSION (0.0.55-lpb)
2. image = ghcr.io/lpb-stack/devstack:0.0.55-lpb-web
3. podman pull (if not cached)
4. podman run --name lpb-stack --network host \
     -v /project:/home/lpb/workspace/project \
     -v ~/.lpb-stack/state:/home/lpb/.pi \
     -v ~/.lpb-stack/agent-browser:/home/lpb/.agent-browser \
     ghcr.io/lpb-stack/devstack:0.0.55-lpb-web
5. pins 0.0.55-lpb in ~/.lpb-stack/devstack/last-version
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
lpb --tag 0.0.55-lpb-dev ~/projects/myapp   # pin to an exact version
lpb --web --port 8080 ~/projects/myapp      # VSCodium on port 8080
lpb --shell ~/projects/myapp                # bash inside the container
lpb ~/projects/myapp -- -p "fix the bug"    # one-shot pi run
lpb update                                  # self-update + pull images
lpb logs                                    # stream logs
```

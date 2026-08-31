# Environment Variables Reference

Environment variables use the `LPB_` (LocalPibox) prefix to avoid colliding
with user environment variables. They flow through two layers:

1. **Launcher** (`lpb` on the host) — selects images, ports, and paths.
2. **Container** (`start.sh` at boot) — reads an optional project `.env`
   and the baked-in `lpb.conf.env`, then promotes selected `LPB_*` vars to
   the bare names third-party tools expect.

## Priority Chain (highest → lowest)

1. Shell environment (`export LPB_ED_PORT=…`)
2. Project `.env` file (optional — next to the project dir, or in the
   workspace root; `LPB_*` vars only)
3. `lpb.conf.env` (runtime defaults, baked into the image)
4. Hardcoded fallbacks

If a value from `.env` doesn't seem to apply, check shell env first:
`printenv LPB_ED_PORT`.

## Launcher Variables (host, read by `lpb`)

| Variable | Default | Purpose |
|---|---|---|
| `LPB_IMAGE_TAG` | — | Persistent pipeline/version override: `dev`, `main`, `latest`, or an exact version tag |
| `LPB_STATE_DIR` | `~/.lpb-stack/state` | Host dir mounted to `/home/lpb/.pi` |
| `LPB_BROWSER_DIR` | `~/.lpb-stack/agent-browser` | Host dir for browser profiles & sessions |
| `LPB_CONTAINER_NAME` | `lpb-stack` | Container name (set in `lpb.stack.env`) |
| `LPB_IMAGE_CLI` / `LPB_IMAGE_WEB` | `ghcr.io/lpb-stack/devstack:dev-{cli,web}` | Last-resort fallback images (forks repoint these) |
| `LPB_LEMONADE_BASE_URL` | `http://127.0.0.1:13305/v1` | Lemonade server URL — passed to the container; on a first boot (`--ssh`/`--web`) the launcher health-checks it and prompts for the real host if unreachable |
| `LPB_LEMONADE_API_KEY` | `lemonade` | Lemonade API key — passed to the container for the first-boot setup wizard |
| `GHCR_USERNAME` | `lpb-stack` | Registry user for pulls |

(The bare `LEMONADE_BASE_URL` / `LEMONADE_API_KEY` names are also accepted on the host; the `LPB_` form wins.)

## Runtime Defaults (baked into the image via `lpb.conf.env`)

| Variable | Default | Purpose |
|---|---|---|
| `LPB_ED_PORT` | `3000` | VSCodium port |
| `LPB_EDITOR_HOST` | `0.0.0.0` | Editor listen host (`127.0.0.1` for local-only) |
| `LPB_CONNECTION_TOKEN` | *(random per start)* | OpenVSCode token — set for a stable token |
| `LPB_LEMONADE_BASE_URL` | `http://127.0.0.1:13305/v1` | Local model server endpoint |
| `LPB_MAX_TOKENS_CONTEXT_RATIO` | `0.06` | max_tokens as fraction of context window (Qwen thinking headroom) |
| `LPB_AGENT_BROWSER_SESSION` | `$PI_WORKTREE_ID` | Browser session isolation id |
| `LPB_AGENT_BROWSER_MAX_OUTPUT` | `4000` | Max chars for browser snapshot output |
| `LPB_AGENT_BROWSER_ARGS` | `--no-sandbox,…` | Chrome launch args |
| `LPB_AGENT_BROWSER_CONTENT_BOUNDARIES` | `true` | LLM safety markers in content extraction |
| `LPB_AGENT_BROWSER_CONFIRM_ACTIONS` | `delete,download,…` | Action categories requiring approval |
| `LPB_AGENT_BROWSER_IDLE_TIMEOUT_MS` | `300000` | Browser daemon idle timeout |
| `LPB_PERSIST_GH_CONFIG` | `true` | Persist gh CLI auth into the state volume |
| `LPB_OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | Optional overflow provider (not needed with Lemonade) |
| `GHCR_TOKEN` | *(empty)* | Read-only PAT for GHCR pulls (falls back to `GITHUB_TOKEN`/`LPB_GITHUB_TOKEN`) |

> **Browser var bridging:** agent-browser (CLI / MCP server) reads the
> `AGENT_BROWSER_*` names, not `LPB_*`. `start.sh` bridges each
> `LPB_AGENT_BROWSER_*` to `AGENT_BROWSER_*` (shell env > LPB_ > container-safe
> fallback), so the container-safe Chrome args — notably `--no-sandbox`,
> required to launch Chrome inside a container — always take effect.

## API Keys

| Variable | Used by |
|---|---|
| `LPB_EXA_API_KEY` | Exa MCP server (web search) |
| `LPB_CONTEXT7_API_KEY` | Context7 MCP server (higher rate limits) |
| `LPB_LEMONADE_API_KEY` | Lemonade server API key — first-run setup wizard (`lpb-config setup`); a placeholder is fine for local servers |

## The `LPB_` Bridge (container boot)

`start.sh` at container boot promotes every `LPB_<name>` var to the bare
name third-party tools expect (single list, single loop in `start.sh`;
priority: shell env > `LPB_` > container-safe fallback):

| LPB_ Prefix | Bare Name | Used By |
|---|---|---|
| `LPB_EXA_API_KEY` | `EXA_API_KEY` | Exa MCP server |
| `LPB_CONTEXT7_API_KEY` | `CONTEXT7_API_KEY` | Context7 MCP server |
| `LPB_LEMONADE_BASE_URL` | `LEMONADE_BASE_URL` | Lemonade provider + setup wizard |
| `LPB_LEMONADE_API_KEY` | `LEMONADE_API_KEY` | Lemonade provider + setup wizard |
| `LPB_OPENROUTER_BASE_URL` | `OPENROUTER_BASE_URL` | Optional overflow provider |
| `LPB_MAX_TOKENS_CONTEXT_RATIO` | `MAX_TOKENS_CONTEXT_RATIO` | lemonade-pi-plugin (max_tokens ratio) |
| `LPB_CONNECTION_TOKEN` | `CONNECTION_TOKEN` | OpenVSCode |
| `LPB_EDITOR_HOST` | `HOST` | OpenVSCode |
| `LPB_ED_PORT` | `ED_PORT` | OpenVSCode |
| `LPB_DEVCONTAINER_WORKSPACE_DIR` | `DEVCONTAINER_WORKSPACE_DIR` | Workspace dir |
| `LPB_GITHUB_TOOLSETS` | `GITHUB_TOOLSETS` | gh CLI toolsets |
| `LPB_AGENT_BROWSER_*` (6 vars) | `AGENT_BROWSER_*` | agent-browser (session, args, output limits, confirm actions, timeout) |

So setting `LPB_EXA_API_KEY` in your project `.env` is sufficient —
`start.sh` promotes it to `EXA_API_KEY` for the MCP server.

## Per-Pipeline Stack Profiles

`lpb.stack.env` defines stack-wide values; `lpb.stack.dev.env` /
`lpb.stack.main.env` override the branch refs per pipeline:

| Variable | dev profile | main profile | Meaning |
|---|---|---|---|
| `LPB_PI_VERSION` | `0.84.4` | `0.84.4` | Mainstream pi npm version installed in the image (`@earendil-works/pi-coding-agent`) |
| `LPB_CONFIG_REF` | `dev` | `main` | Config preset branch baked into the image |

These select **what goes into the image** (build time) and which pipeline
`lpb-devstack validate` checks against. At runtime you pick the pipeline with
`lpb --tag dev|main`, not with these vars.

## In-Container Variables (read by Pi, extensions, and tooling)

| Variable | Purpose |
|---|---|
| `LPB_VERSION` | Stack version baked at build time — stamps `settings.json` pins and selects the `lpb-config` / `lpb-devstack` pipeline |
| `AGENT_DIR` | Config repo directory (`~/.pi/agent`) |
| `PI_CODING_AGENT_DIR` | Same dir, as Pi expects it |
| `LPB_AGENT_GIT` | Extension clones dir (default `$AGENT_DIR/git/github.com/lpb-stack`) |
| `LEMONADE_BASE_URL` | Model API endpoint (`http://127.0.0.1:13305/v1`) |
| `LEMONADE_API_KEY` | Lemonade server API key (bridged from `LPB_LEMONADE_API_KEY`) |
| `MAX_TOKENS_CONTEXT_RATIO` | max_tokens ratio for the lemonade provider (bridged) |
| `AGENT_BROWSER_SESSION` | Browser session isolation id |
| `AGENT_BROWSER_MAX_OUTPUT` | Max chars for snapshot output |

## Configuration File Reference

| File | Purpose |
|---|---|
| `.env.example` (repo root) | Template for the project `.env` |
| `.env` (project dir / workspace) | Per-project `LPB_*` overrides (optional, gitignored) |
| `lpb.conf.env` (repo root) | Runtime defaults, baked into the image |
| `lpb.stack.env` (repo root) | Stack identity: forks, images, container name |
| `lpb.stack.dev.env` / `lpb.stack.main.env` | Per-pipeline branch-ref overrides |

## Quick Reference

```bash
# Run the stable pipeline
lpb --main /project            # or: lpb --tag main /project

# Persistent pipeline override
export LPB_IMAGE_TAG=main
lpb /project

# Pin an exact version
lpb --tag 0.0.N-lpb-dev /project

# Custom editor port (shell env wins over .env and defaults)
export LPB_ED_PORT=8080
lpb --web /project
```

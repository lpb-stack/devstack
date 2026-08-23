# Config Repo Reference

The **config repo** (`lpb-stack/config`, cloned to `/home/lpb/.pi/agent/`)
is the single source of truth for agent configuration, settings, skills,
and subagent definitions. It is installed into the devstack container at
boot by `start.sh`.

## Directory Structure

```
~/.pi/agent/                       (git clone of lpb-stack/config)
├── settings.json.template    → template (generated at boot → settings.json)
├── settings.json             → runtime config (NOT git-tracked, on host volume)
├── lpb-memory-config.json.template → memory config template (→ lpb-memory-config.json)
├── AGENTS.md                 → agent instructions (model config, MCP servers, skills)
├── README.md / CONTRIBUTING.md / VALIDATION.md
├── pi-defaults.json          → local-first defaults (e.g. subagent model)
├── mcp.json                  → MCP server configuration
├── .env.example              → template for bare-name env vars (EXA_API_KEY, …)
├── install.sh                → host install helper
├── VERSION                   → config repo version
├── skills/
│   ├── agent-browser-mcp-integration/
│   ├── browser-validation/
│   └── mcp-vision-analysis/
├── agents/
│   ├── vision-analysis.md    → visual analysis subagent
│   ├── browser-automation.md → browser automation subagent
│   ├── exa-search.md         → web research subagent
│   ├── researcher.md         → researcher subagent
│   ├── README.md             → subagent registry
│   └── _template.md          → template for new subagents
└── (runtime, NOT git-tracked)
    ├── git/github.com/lpb-stack/   → extension clones (lpb-memory, pi-subagents, lemonade-pi-plugin)
    ├── npm/                        → npm package installs (pi-mcp-adapter, …)
    ├── lpb-memory/                 → memory store (USER.md, MEMORY.md, failures.md)
    ├── sessions/ · projects-memory/ → session history
    └── models-store.json · auth.json · trust.json
```

## settings.json Lifecycle

The settings file is **template-driven**, not git-tracked:

1. **Template**: `settings.json.template` ships in the config repo with
   `__LPB_VERSION__` placeholders
2. **Boot**: the setup wizard (`lpb setup` / `lpb-config setup`) renders
   `settings.json` by replacing placeholders (fallback: `start.sh` via
   `lpb-config render`)
3. **Model configured interactively**: the setup wizard writes
   `defaultProvider` + `defaultModel` with live validation (no `/login`
   needed for lemonade)
4. **Persistence**: `settings.json` lives on the host volume — it survives
   container rebuilds
5. **Validation**: `lpb-devstack validate` checks settings.json pins match
   the current stack version

### Example pin format

Extensions are pinned in the `packages` array of `settings.json` as
`git:<owner>/<repo>@<stack-tag>` strings:

```json
{
  "packages": [
    "git:github.com/lpb-stack/lemonade-pi-plugin@0.0.55-lpb-dev",
    "git:github.com/lpb-stack/lpb-memory@0.0.55-lpb-dev",
    "npm:pi-mcp-adapter",
    "git:github.com/lpb-stack/pi-subagents@0.0.55-lpb-dev",
    "npm:pi-powerline-footer",
    "@upstash/context7-mcp"
  ]
}
```

The `__LPB_VERSION__` placeholder in the template is replaced with the
stack version at boot. Pins are synced to a new stack version by
`lpb-devstack workspace sync-pins`.

## Extension Clones

The `git/github.com/lpb-stack/` directory contains runtime clones of the
Pi extensions. They are **not baked into Docker images** — they update at
runtime via `pi update --extensions`.

| Directory | Repo | Role |
|---|---|---|
| `lpb-memory/` | `lpb-stack/lpb-memory` | Persistent memory extension |
| `pi-subagents/` | `lpb-stack/pi-subagents` | Subagent model registry |
| `lemonade-pi-plugin/` | `lpb-stack/lemonade-pi-plugin` | Lemonade provider plugin |

## Runtime State: lpb-memory Dir

The directory `~/.pi/agent/lpb-memory/` holds **runtime state** for the
memory extension — it is NOT in the config repo and NOT git-tracked:

| File | Purpose |
|---|---|
| `USER.md` | User preferences extracted from sessions (by NPU review) |
| `MEMORY.md` | Technical insights extracted from sessions |
| `failures.md` | Lessons learned from mistakes (failure detection) |

This data is created by the lpb-memory extension's subprocess review
system and persists across sessions. It is backed up with the host volume.

## Agents Directory

The `agents/` directory defines **subagent model configurations**. Each
`.md` file specifies:
- The `agent_type` and `model` to use
- The system prompt and tools available
- How the subagent should behave

For example, `vision-analysis.md` defines a subagent that uses the
local Qwen3.6 vision model to analyze browser screenshots.

## Skills Directory

The `skills/` directory contains **reusable procedures** (Pi skills). Each
skill is a directory with a `SKILL.md` defining when to use it, the steps,
and the pitfalls. The config repo ships:

- `agent-browser-mcp-integration` — browser automation with agent-browser MCP
- `browser-validation` — automated browser validation pipeline with JSON reports
- `mcp-vision-analysis` — visual page analysis with the local vision model

(Workspace-level skills, such as `localpibox-repo-workflow`, live in the
host workspace's `.pi/skills/`, not in the config repo.)

## MCP Configuration

`mcp.json` configures MCP servers available to the agent. It references
servers like `exa`, `agent-browser`, and `context7-mcp` with their
respective connection details and API key sources.

## Quick Reference

```bash
# Check config repo state
lpb-config status

# Update config repo
lpb-config update

# Validate settings.json pins
lpb-devstack validate

# Sync extension pins to stack version
lpb-devstack workspace sync-pins

# Reset config repo
lpb-config reset
```

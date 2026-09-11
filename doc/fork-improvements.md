# LocalPibox Stack: Repositories & Customizations

> Last updated: 2026-09-11
> Stack: mainstream pi from npm + 4 repos under `github.com/lpb-stack`;
> the only fork is `lemonade-pi-plugin`.

---

## Repository Map

| Repo | Type | Upstream | Purpose |
|---|---|---|---|
| **`lpb-stack/lemonade-pi-plugin`** | Fork | `lemonade-sdk/lemonade-pi-plugin` | Lemonade provider — Qwen thinking protocol, vision, model catalog |
| **`lpb-stack/config`** | Original | — | User settings, skills, agents |
| **`lpb-stack/devstack`** | Original | — | Docker dev environment + lpb launcher |
| **`lpb-stack/lpb-memory`** | Original | — | Persistent memory extension |

**Pi itself is not a repo.** It is installed from the npm registry at
`LPB_PI_VERSION` (`@earendil-works/pi-coding-agent`). The subagent registry
(`@tintinweb/pi-subagents`) is likewise installed from upstream npm — version
tracked in `settings.json`.

> ⚠️ Two earlier forks (`lpb-stack/pi`, `lpb-stack/pi-subagents`) were retired
> in 2026 when upstream absorbed what they provided. A local `workspace/pi`
> reference clone may remain at the retired tag — it is not built or used.
> Re-introducing a forked pi is only needed for changes upstream hasn't picked
> up; see `doc/forking.md` for the procedure.

## Why only one fork

The stack's rule: keep LocalPibox changes as **clean commits on top of upstream
tags** — the delta is always visible as the diff between upstream and the
`lpb-dev` branch. Forks are introduced only when upstream cannot cover a need,
and retired as soon as upstream catches up. Today that leaves exactly one fork:
the lemonade-pi-plugin, which carries the Qwen-on-Lemonade protocol that no
upstream project owns.

## Lemonade Pi Plugin (`lpb-stack/lemonade-pi-plugin`)

Fork of `lemonade-sdk/lemonade-pi-plugin`, adding Qwen reasoning support,
vision detection, and a per-model parameter catalog for the Lemonade local
provider.

### Qwen reasoning model support

| Feature | Implementation |
|---|---|
| **Qwen detection** | `isQwenReasoningModel()` — detects Qwen3.x, QwQ, Qwen2.5-thinking via regex |
| **MTP detection** | `isMtpModel()` — detects Multi-Token Prediction models |
| **FLM detection** | `flmTemplateRejectsDeveloperRole()` — disables reasoning for FLM backends |
| **Dynamic maxTokens** | Per-model `maxTokens` in the model-params catalog — prevents context overflow (see Configuration below) |
| **Thinking protocol** | Adds `enable_thinking`, budget fields, `thinkingFormat: "qwen-chat-template"`; per-request payload tuning (P2 budgets, effortMap, P3 sampling, P5 off params) — see `doc/thinking-support.md` |
| **Heuristic detection** | `isReasoningByHeuristic()` — catches models without `recipe` field |

### Vision capability

| Feature | Implementation |
|---|---|
| **Label-based detection** | `detectVision()` — checks for `"vision"` in model labels |
| **Auto image input** | Vision models auto-get `input: ["text", "image"]` |

### Sync model store

Keeps `~/.pi/agent/models-store.json` in sync with the Lemonade API.
Subprocesses and subagents resolve models with correct `contextWindow` and
`maxTokens` without network calls. Triggered on login, refresh, and
`/lemonade change-ctx`.

### Configuration

The response ceiling (`max_completion_tokens`) is the **per-model `maxTokens`
catalog field** in the model-params catalog (`~/.pi/agent/model-params.json`,
user tier, overrides the plugin-shipped tier) — an exact token value per wire
model id, applied at model sync, editable via `/lemonade tune`.

Exact values are used deliberately instead of a context-ratio formula: a small
set of explicit numbers is more auditable than an implicit calculation.

| Model | maxTokens | Why |
|---|---|---|
| Qwen thinking models (27B/35B) | `16384` | Thinking headroom at the 262k window |
| Small / non-Qwen models | `4096` | Default response cap |

## Subagents: local-first by configuration

Upstream `@tintinweb/pi-subagents` is used unmodified. "Local-first"
(subagents run on the local session model, no cloud default) is achieved
purely through configuration:

- Agent `.md` files **omit the `model:` field** — so subagents inherit the
  session model instead of a hardcoded cloud default.
- `globalDefaultModel: null` in the extension settings — nothing to fall back
  to but the parent session model. **Zero Anthropic dependency.**

**Model resolution chain:**

1. Explicit `model` param in the `Agent()` call
2. `model` field in the agent `.md` frontmatter
3. `globalDefaultModel` from the extension settings (`null` here)
4. Parent session model (inherit)

```json
{
  "extensions": {
    "@tintinweb/pi-subagents": {
      "globalDefaultModel": null,
      "disableDefaultAgents": true
    }
  }
}
```

## Backend matrix: FLM vs MTP

| Backend | Reasoning Support | Why |
|---|---|---|
| **MTP** (e.g. `Qwen3.6-35B-A3B-MTP-GGUF`) | ✅ Yes | Uses newer chat template that accepts `developer` role |
| **FLM** (e.g. `qwen3.5-9b-FLM`) | ❌ No | Chat template only accepts `system/user/assistant/tool` roles |

## Known Issues & Mitigations

### Qwen thinking overflow

Qwen with thinking enabled throws "context size exceeded" when
`prompt + max_tokens` exceeds the window.

**Current mitigations:**
- Per-model `maxTokens` ceilings in the model-params catalog (16384 for Qwen
  thinking models — exact values, no formula)
- `reserveTokens` raised — compaction fires before the window overflows
- Thinking disabled during compaction — prevents meta-thinking waste

### agent-browser-chat

Requires `AI_GATEWAY_API_KEY` (Vercel AI SDK gateway). Not configurable
per-call. Cannot point at the local Lemonade server. Not usable with this stack.

---

## Quick Reference

### Environment Variables

| Variable | Value | Purpose |
|---|---|---|
| `LEMONADE_BASE_URL` | `http://127.0.0.1:13305/v1` | Model API endpoint |
| `VISION_MODEL` | *(configured)* | Vision model ID — see `lpb-config show` |
| `AGENT_BROWSER_MAX_OUTPUT` | `4000` | Max chars for snapshot output |

### Admin Commands

| Command | Purpose |
|---|---|
| `/lemonade health` | Check server health |
| `/lemonade models` | List detected models |
| `/lemonade refresh` | Re-sync models |
| `/lemonade change-ctx` | Change context window for active model |

### Support Files

Paths match the Dockerfile `COPY` lines (source of truth):

| Path | Purpose |
|---|---|
| `/opt/devstack/install-browser.py` | Install Chrome-for-Testing + agent-browser |
| `/opt/devstack/validate.py` | Stack validation helper |
| `/opt/pi-support/browser-state-cleanup.py` | Cleanup browser state volumes |
| `/opt/pi-support/browser-validate.ts` | Browser validation entry point |
| `/opt/pi-support/install-openspec.py` | Bootstrap OpenSpec in a project |
| `/opt/devstack/start.sh` | Container start script |
| `/opt/pi-support/config/agent-browser-action-policy.json` | Agent action policies |
| `/opt/pi-support/validate-subagent-output.ts` | Subagent output validation |

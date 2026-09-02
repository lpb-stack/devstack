# LocalPibox Fork Improvements

> Last updated: 2026-09-03
> Status: de-forked (2026-08-31) — mainstream pi from npm; Qwen thinking via
> lemonade-pi-plugin with per-model `maxTokens` ceilings

---

## Repository Map

This stack uses 5 repositories under `github.com/lpb-stack`. Two are forks
of upstream repos; pi itself is no longer a stack repo (de-forked
2026-08-31, see the retired-fork note below):

| Repo | Type | Upstream | Purpose |
|---|---|---|---|
| **`lpb-stack/lemonade-pi-plugin`** | Fork | `lemonade-sdk/lemonade-pi-plugin` | Lemonade provider — Qwen thinking protocol, vision, model catalog |
| **`lpb-stack/pi-subagents`** | Fork | `tintinweb/pi-subagents` | Subagent model registry (local-first) |
| **`lpb-stack/config`** | Original | — | User settings, skills, agents |
| **`lpb-stack/devstack`** | Original | — | Docker dev environment + lpb launcher |
| **`lpb-stack/lpb-memory`** | Original | — | Persistent memory extension |

Pi (the coding agent itself) is installed from the npm registry at
`LPB_PI_VERSION` (`@earendil-works/pi-coding-agent`) — no repo, no fork.

## The Pi Fork (`lpb-stack/pi`) — retired 2026-08-31

The `lpb-stack/pi` fork (based on `earendil-works/pi`, last state: tag
`pre-defork-0.0.71`) is **retired**. It was based on v0.84.3 with 8 lbp
commits adding Qwen/Lemonade support: the `reasoning_effort` →
`chat_template_kwargs` mapping, a `reasoning_budget_tokens` soft cap, Case 4
reasoning overflow detection, `allowScripts` declarations for native addons,
and fork versioning. Upstream pi has since picked up `reasoning_effort`
natively, which made the fork unnecessary.

| Fork change | Where it lives now |
|---|---|
| `reasoning_effort` support for Qwen | Mainstream pi ≥0.84.4 sends it natively; the plugin handles the `enable_thinking` protocol |
| `reasoning_budget_tokens` soft cap | Replaced by per-model `maxTokens` ceilings in the plugin's model-params catalog |
| Case 4 overflow detection | Replaced by raised `reserveTokens` (early compaction) + per-model ceilings |
| `allowScripts` for native addons | Re-implemented by devstack: `npm config set allow-scripts …` (start.sh) + `.npmrc` (Dockerfile) |
| `LOCALPIB_VERSION` env read | Replaced by the `LPB_VERSION` banner baked by the Dockerfile |
| Last patch (Case 4 overflow) | Kept as `patches/pi-case4-overflow.patch` for reference |

Re-introducing a forked pi is only needed for changes upstream hasn't
picked up — see `doc/forking.md` for the procedure. A local `workspace/pi`
reference clone may remain at the retired tag.

---

## Lemonade Pi Plugin (`lpb-stack/lemonade-pi-plugin`)

Forked from `lemonade-sdk/lemonade-pi-plugin`. Adds +334 lines across 13 files
to support Qwen reasoning models on the Lemonade local provider.

### Qwen Reasoning Model Support

| Feature | Implementation |
|---|---|
| **Qwen detection** | `isQwenReasoningModel()` — detects Qwen3.x, QwQ, Qwen2.5-thinking via regex |
| **MTP detection** | `isMtpModel()` — detects Multi-Token Prediction models |
| **FLM detection** | `flmTemplateRejectsDeveloperRole()` — disables reasoning for FLM backends |
| **Dynamic maxTokens** | Per-model `maxTokens` in the model-params catalog — prevents context overflow (see Configuration below) |
| **Thinking protocol** | Adds `enable_thinking`, `reasoning_budget_tokens`, `thinkingFormat: "qwen-chat-template"` |
| **Heuristic detection** | `isReasoningByHeuristic()` — catches models without `recipe` field |

### Vision Capability

| Feature | Implementation |
|---|---|
| **Label-based detection** | `detectVision()` — checks for `"vision"` in model labels |
| **Auto image input** | Vision models auto-get `input: ["text", "image"]` |

### Sync Model Store

Keeps `~/.pi/agent/models-store.json` in sync with the Lemonade API. Subprocesses
and subagents resolve models with correct `contextWindow` and `maxTokens` without
network calls. Triggered on login, refresh, and `/lemonade change-ctx`.

### Configuration

The response ceiling (`max_completion_tokens`) is the **per-model
`maxTokens` catalog field** in the lemonade-pi-plugin model-params catalog
(`~/.pi/agent/model-params.json` + the plugin-shipped tier) — an exact token
value per wire model id, applied at model sync. The former ctx-ratio design
(`DEFAULT_MAX_TOKENS_CONTEXT_RATIO` 0.125, `QWEN_REASONING_MAX_TOKENS_CONTEXT_RATIO`
0.06, the 16384 clamp, and the `LPB_MAX_TOKENS_CONTEXT_RATIO` env chain)
was retired 2026-09-02: the plugin's env read was dead (set by nothing), the
protective work was done by the clamp anyway, and six explicit numbers are
more auditable than a formula.

| Model | maxTokens | Why |
|---|---|---|
| Qwen thinking models (27B/35B) | `16384` | Thinking headroom at the 262k window (old formula + clamp landed here) |
| Small / non-Qwen models | `4096` | Default response cap (matches the former fallback) |

---

## Pi Subagents (`lpb-stack/pi-subagents`)

Fork of `tintinweb/pi-subagents` (on top of upstream v0.16.1, merged into
`lpb-dev`). Provides subagent model registry that removes
Anthropic defaults and makes the stack fully local-first.

### Key Change: `globalDefaultModel`

Removes hardcoded `anthropic/claude-haiku-4-5` defaults from subagent
definitions. Introduces `globalDefaultModel` in settings as the centralized
model source of truth:

**Model resolution chain (after patch):**
1. Explicit `model` param in `Agent()` call
2. `model` field in agent `.md` frontmatter
3. **`globalDefaultModel`** from `pi-defaults.json`
4. Parent session model (inherit)

**Configuration for local-first:**
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

`globalDefaultModel: null` means subagents inherit whatever model the
parent session uses — **zero Anthropic dependency**.

---

## Key Design Decisions

### Patch Model

All LocalPibox changes are kept as clean commits on top of upstream tags.
The delta is always visible as the diff between upstream and `lpb-dev`.

### FLM vs MTP Backend

| Backend | Reasoning Support | Why |
|---|---|---|
| **MTP** (`Qwen3.6-35B-A3B-MTP-GGUF`) | ✅ Yes | Uses newer chat template that accepts `developer` role |
| **FLM** (`qwen3.5-9b-FLM`, `qwen3.6-moe-35b-a3b-FLM`) | ❌ No | Chat template only accepts `system/user/assistant/tool` roles |

---

## Known Issues & Mitigations

### Qwen Thinking Overflow (2026-08-02)

Qwen with thinking enabled throws "context size exceeded" when
`prompt + max_tokens` exceeds the window.

**Mitigations (current):**
- Per-model `maxTokens` ceilings in the lemonade-pi-plugin model-params
  catalog (16384 for Qwen thinking models — exact values, no formula)
- `reserveTokens` raised — compaction fires before the window overflows
- Thinking disabled during compaction — prevents meta-thinking waste

The original ratio-based mitigation (`LPB_MAX_TOKENS_CONTEXT_RATIO=0.06`
in `start.sh` / `.env.example`) was retired 2026-09-02 — see Configuration
above.

### agent-browser-chat

Requires `AI_GATEWAY_API_KEY` (Vercel AI SDK gateway). Not configurable
per-call. Cannot point at local Lemonade server. Not usable with this stack.

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

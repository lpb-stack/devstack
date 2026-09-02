# LocalPibox Fork Improvements

> Last updated: 2026-08-26
> Status: Qwen3.6 reasoning + vision fully operational

---

## Repository Map

This stack uses 6 repositories under `github.com/lpb-stack`. The two forked
repos and their upstream origins:

| Repo | Type | Upstream | Purpose |
|---|---|---|---|
| **`lpb-stack/pi`** | Fork | `earendil-works/pi` | Pi monorepo — Qwen reasoning + overflow detection |
| **`lpb-stack/lemonade-pi-plugin`** | Fork | `lemonade-sdk/lemonade-pi-plugin` | Lemonade provider — Qwen model detection, vision |
| **`lpb-stack/pi-subagents`** | Fork | `tintinweb/pi-subagents` | Subagent model registry (local-first) |
| **`lpb-stack/config`** | Original | — | User settings, skills, agents |
| **`lpb-stack/devstack`** | Original | — | Docker dev environment + lpb launcher |
| **`lpb-stack/lpb-memory`** | Original | — | Persistent memory extension |

## The Pi Fork (`lpb-stack/pi`)

### Upstream Baseline

**Based on:** `earendil-works/pi` v0.84.3 (merged into `lpb-dev` branch)

The original upstream repo is a **TypeScript monorepo** with 11 packages:

| Package | Description |
|---|---|
| `@earendil-works/pi-ai` | Unified multi-provider LLM API (OpenAI, Anthropic, Google, etc.) |
| `@earendil-works/pi-agent-core` | Agent runtime with tool calling and state management |
| `@earendil-works/pi-coding-agent` | Interactive coding agent CLI |
| `@earendil-works/pi-tui` | Terminal UI library with differential rendering |
| `@earendil-works/pi-client` | Client library |
| `@earendil-works/pi-protocol` | Protocol definitions |
| `@earendil-works/pi-server` | Server component |
| `@earendil-works/pi-session-backends` | Session storage backends |
| `@earendil-works/pi-telemetry` | Vendor-neutral telemetry contracts |
| `@earendil-works/pi-evals` | Evaluation harness |

For chat/workflows, see the companion project: [earendil-works/pi-chat](https://github.com/earendil-works/pi-chat).

### Fork Patches (original set, introduced on v0.84.1/v0.84.2)

The fork adds **6 lbp-specific commits** on top of the upstream merge:

| Commit | What changed | Purpose |
|---|---|---|
| `53c1dc2` | **Critical**: Qwen/Lemonade-compatible patches on v0.84.1 | See details below |
| `3340960` | `docs(lbp)`: document what v0.84.1 provides vs lbp additions | Docs |
| `2a3e9bc` | `feat(coding-agent)`: declare `allowScripts` for native addons | Allow native addons |
| `346947d` | `hooks`: sync to latest (validation-only, skip when not in devstack) | Hook management |
| `8449290` | `chore`: install husky pre-commit hook, remove stale githooks wrapper | Dev tooling |
| `3fc4978` | `Merge tag 'v0.84.2' into lbp-dev` | Upstream merge |

Since the v0.84.3 merge (`13c6d72`): **2 additional commits** —
`3c307d0` (fix: cloudflare gateway type, include workers) and
`6db652e` (style: numeric literal normalization in overflow.ts, biome).

The **critical commit** (`53c1dc2`) adds these surgical changes:

| File | Change | Purpose |
|---|---|---|
| `packages/ai/src/api/openai-completions.ts` | Maps `reasoning_effort` to `chat_template_kwargs` | Sends `reasoning_effort: "high"\|"medium"\|"low"` to Qwen models |
| `packages/ai/src/api/openai-completions.ts` | Adds `reasoning_budget_tokens` param | Sends soft-cap (0) to prevent runaway thinking blocks |
| `packages/ai/src/types.ts` | Adds `reasoningBudgetTokens` compat field | New compat flag for Qwen reasoning budget |
| `packages/ai/src/utils/overflow.ts` | Adds **Case 4** reasoning overflow detection | Detects when thinking blocks consume output token budget |
| `packages/coding-agent/src/config.ts` | Adds `LOCALPIB_VERSION` env | Reads `LPB_VERSION` for fork identification |
| `VERSION` | New file: `0.0.1-lbp` | Fork version marker |
| `package.json` | Version → `0.0.1-lbp` | Fork version |

### Branch Strategy

| Branch | Source | Content |
|---|---|---|
| `lpb-dev` (default) | Upstream + patches | Active development, contains lbp patches |
| `lpb` (stable) | Derived from `lpb-dev` | Stable branch, receives clean merges |

To update:
```bash
git fetch https://github.com/earendil-works/pi.git
git checkout lbp-dev
git rebase <upstream-tag>    # e.g. v0.84.2
# apply lbp patches
git push --force-with-lease origin lbp-dev
```

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
| **Dynamic maxTokens** | Reasoning: `0.06 × contextWindow`. Non-reasoning: `0.125` — prevents context overflow |
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

### Qwen3 Thinking Overflow (2026-08-02)

Qwen3.6 with thinking enabled throws "context size exceeded" when
`prompt + max_tokens` exceeds the 262k window.

**Mitigations:**
- `maxTokens` reduced to ~15k (`ratio 0.06`) — leaves room for 10-20k thinking blocks
- `reserveTokens` doubled to 32k — compaction fires at ~88% (230k) instead of ~94% (246k)
- Thinking disabled during compaction — prevents meta-thinking waste
- `LPB_MAX_TOKENS_CONTEXT_RATIO=0.06` set in `start.sh` and `.env.example`

### agent-browser-chat

Requires `AI_GATEWAY_API_KEY` (Vercel AI SDK gateway). Not configurable
per-call. Cannot point at local Lemonade server. Not usable with this stack.

---

## Quick Reference

### Environment Variables

| Variable | Value | Purpose |
|---|---|---|
| `LEMONADE_BASE_URL` | `http://127.0.0.1:13305/v1` | Model API endpoint |
| `VISION_MODEL` | `Qwen3.6-35B-A3B-MTP-GGUF` | Vision model ID |
| `LPB_MAX_TOKENS_CONTEXT_RATIO` | `0.06` | Max tokens ratio for Qwen reasoning |
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

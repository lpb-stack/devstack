# Thinking Support

> Last updated: 2026-09-11
> Status: **Implemented & validated** — all wire fields honored by the current
> server build (b10865). Per-level behavior is covered by the reproducible
> benchmark in [Thinking Benchmark](thinking-benchmark.md).

Pi's `/thinking` levels (off | minimal | low | medium | high) control reasoning
depth on the lemonade-served Qwen models. The stack implements this with 100%
upstream mechanisms — no pi fork, no server patches — so it survives pi and
llama.cpp upgrades.

---

## 1. Architecture: How Thinking Works

```
┌──────────────────────────────────────────────────────────────────┐
│  Pi (pi.dev) — 0.85.1 (mainstream, from npm)                     │
│                                                                  │
│  User selects thinking level: /thinking →                        │
│  off | minimal | low | medium | high                             │
│  Pi sends (top-level request fields):                            │
│  {                                                               │
│    reasoning_effort: "minimal" | "low" | "medium" | "high"       │
│    thinking_budget_tokens: 2048 | 3072 | 8192 | 16384            │
│    max_completion_tokens: 16384        (per-model maxTokens)     │
│  }                                                               │
└──────────────────────────────┬───────────────────────────────────┘
                               │  HTTP POST /v1/chat/completions
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│  lemonade-pi-plugin — per-request payload tuning                 │
│  (per-model catalog: model-params.json, user tier over plugin)   │
│                                                                  │
│  • P2 budget: min(catalog level, maxTokens − 1024)               │
│  • effortMap: pi level → reasoning_effort the template accepts   │
│    (Qwen3.8: minimal→low, high→xhigh)                            │
│  • P3 sampling row: thinking (on) / nonThinking (off)            │
│  • P5 off level: offParams { enable_thinking: false }            │
└──────────────────────────────┬───────────────────────────────────┘
                               │  tuned request
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│  Lemonade server (llama.cpp b10865)                              │
│                                                                  │
│  • enable_thinking → per-request on/off (honored per-request)    │
│  • thinking_budget_tokens → hard cap on thinking tokens          │
│  • reasoning_effort → consumed by the Qwen chat template         │
│    (names outside its vocabulary are rejected — effortMap        │
│    exists so the wire always carries an accepted value)          │
└──────────────────────────────────────────────────────────────────┘
```

## 2. Wire format: what each field does

| Field | Set by | Server behavior (b10865) |
|---|---|---|
| `enable_thinking` (in `chat_template_kwargs`) | Pi `qwen-chat-template` format; plugin `offParams` at the off level | Toggles the thinking block. **Honored per-request** — one server instance serves both thinking and non-thinking modes. |
| `thinking_budget_tokens` (top-level) | Pi computes a per-level default; the plugin re-clamps it from the per-model catalog (P2) | **Hard cap** on thinking tokens — the only per-level knob that changes how long the model thinks. When reached, the model transitions to the answer. |
| `reasoning_effort` (top-level) | Pi sends its level; the plugin remaps it via the model's `effortMap` | Consumed by the Qwen chat template. Effort names outside a model's vocabulary are **rejected** by the server, which is why the plugin maintains a per-model mapping instead of passing pi's strings through. |
| `max_completion_tokens` (top-level) | Per-model `maxTokens` (exact value from the vendor model card) | Total completion ceiling. P2 re-clamps the budget to `maxTokens − 1024` so the answer always has headroom. |

At the **off** level the plugin sends no budget and no effort, and adds
`offParams` (currently `{ enable_thinking: false }`) — the benchmark asserts
zero reasoning tokens in every off cell.

## 3. Why it's implemented this way

- **Only upstream mechanisms.** Pi's `qwen-chat-template` thinking format plus
  the plugin's per-request tuning cover everything — no pi fork patch, no
  server patch. Upgrading pi or llama.cpp never breaks thinking.
- **Budget is the control, effort is the hint.** The budget cap produces the
  measurable per-level difference in thinking depth; `reasoning_effort`
  shapes *how* the model reasons within that cap. Both are honored; neither
  alone is sufficient.
- **Per-model catalog, not code.** Budgets, effort mappings, vendor-recommended
  sampling rows, and off-level params live in `model-params.json` (user tier
  overrides plugin tier, edited via `/lemonade tune`). Adding or tuning a model
  changes data, not code.
- **Answer headroom guaranteed.** The budget is re-clamped to
  `maxTokens − 1024`, so thinking can never consume the entire completion
  window and truncate the answer.

## 4. Current behavior (validated)

All wire fields above are honored per-request on the current build (b10865),
validated 2026-09-10 on all three catalogued models via the benchmark:

| Level | Typical outcome | Use for |
|---|---|---|
| off | zero thinking tokens (P5) | Lookups, formatting, simple Q&A |
| minimal / low | short thinking, fast wall time | quick answers, small edits |
| medium | balanced reasoning (default) | code changes, analysis |
| high | deep deliberation, longest wall time | multi-step problems, hard debugging |

Thinking length scales monotonically with the budget and every level produces a
complete answer (`finish_reason: stop`). The [Thinking Benchmark](thinking-benchmark.md)
documents the test set, the wire-fidelity assertions, and the per-model
results that are regenerated with every release tag.

## 5. Server-side configuration

- **Per-request `enable_thinking`** — honored in both directions on all
  catalogued models (re-validated 2026-09-10). No duplicate server process
  needed for non-thinking mode.
- **`--reasoning-budget-message`** — configured on the current server; tells
  the model to answer once the budget is exhausted (meaningful quality
  difference vs. raw truncation). Probed live by `lpb-devstack validate`
  (warning-only).
- **`--reasoning-format none`** — available workaround if the default
  (DeepSeek-oriented) reasoning parser mis-handles Qwen's inline XML tags
  (silent tag stripping, stream corruption). Not enabled; keep in mind for
  Qwen3.6 reliability issues.

## 6. Open items

| Item | Status |
|---|---|
| Upstream PR #22336 — first-class per-request `reasoning` boolean in the OpenAI API | Tracking; would make the `enable_thinking` keyword extra first-class. Not blocking — per-request `enable_thinking` already works. |
| `--reasoning-format none` | Consider enabling for Qwen3.6 if parser-related corruption appears. Trade-off: client-side thinking/answer splitting. |

---

## Appendix: Reference

| Item | Value |
|---|---|
| pi version | 0.85.1 (mainstream, installed from npm at `LPB_PI_VERSION`) |
| llama.cpp build | b10865 (live-verified 2026-09-10) |
| Pi default budgets (fallback) | minimal: 1024, low: 2048, medium: 8192, high: 16384 |
| Budget re-clamp | `min(catalog level, maxTokens − 1024)` |
| maxTokens | exact per-model value from the vendor model card (Qwen: 16384) |
| Per-model catalog | `lemonade-pi-plugin/lib/model-params.json` (user tier in `~/.pi/agent`) |
| Tuning | `/lemonade tune` in Pi |

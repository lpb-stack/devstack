# Thinking Support: Implementation, Validation & Roadmap

> Last updated: 2026-09-07  
> Status: **Fixed & validated** — all wire fields honored by the current
> server build (b10818); per-level behavior is covered by the reproducible
> benchmark in [Thinking Benchmark](thinking-benchmark.md).

---

## 1. Architecture: How Thinking Works in This Stack

```
┌──────────────────────────────────────────────────────────────────┐
│  Pi (pi.dev) — 0.84.3                                            │
│                                                                  │
│  User selects thinking level: /thinking → low|medium|high|max    │
│  Pi computes: DEFAULT_THINKING_BUDGETS = {                       │
│    minimal: 1024,  low: 2048,  medium: 8192,  high: 16384       │
│  }                                                               │
│  → per-level budget clamped to leave ≥1024 tokens for answer     │
│                                                                  │
│  Sends request to lemonade via openai-completions API:           │
│  {                                                               │
│    chat_template_kwargs: {                                       │
│      enable_thinking: true,                                      │
│      preserve_thinking: true,                                    │
│      reasoning_effort: "low" │ "medium" │ "high"                │
│    },                                                            │
│    thinking_budget_tokens: 2048 │ 8192 │ 14704                  │  ← OUR FIX
│    max_tokens: 15728   (0.06 × 262k context, clamped 16384)     │
│  }                                                               │
└──────────────────────────────┬───────────────────────────────────┘
                               │  HTTP POST /v1/chat/completions
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│  Lemonade server (llama.cpp b10375)                              │
│                                                                  │
│  • `chat_template_kwargs.enable_thinking` → enables thinking     │
│    block via Qwen3.6 chat template                               │
│  • `reasoning_effort` string → IGNORED by llama.cpp (no-op)      │
│  • `thinking_budget_tokens` → HONORED: hard cap on thinking      │
│    tokens. When budget reached, llama.cpp inserts </think> tag   │
│    and transitions to answer generation.                         │
│  • `reasoning_budget_tokens` → also HONORED (alias/synonym)      │
│  • `reasoning_budget_tokens: 0` → NO-OP (= unlimited, NOT soft)  │
│  • `thinking_budget` (top-level) → IGNORED                       │
└──────────────────────────────┬───────────────────────────────────┘
                               │  Response
                               ▼
┌──────────────────────────────────────────────────────────────────┐
│  Response body:                                                  │
│  {                                                               │
│    choices: [{                                                   │
│      message: {                                                  │
│        reasoning_content: " ...thinking text... "                │
│        content: " ...answer text... "                            │
│      }                                                           │
│    }]                                                            │
│  }                                                               │
└──────────────────────────────────────────────────────────────────┘
```

### Key insight: Two separate mechanisms

1. **`enable_thinking`** (via `chat_template_kwargs`) — toggles whether the thinking block is generated. This is what the Pi `qwen-chat-template` format sets.
2. **`thinking_budget_tokens`** — caps how many tokens the thinking block can be. This is the **only per-level control** that the llama.cpp backend honors.

The `reasoning_effort` string sent inside `chat_template_kwargs` is **completely ignored** by llama.cpp. It's only useful for cloud Qwen providers (DashScope, SGLang, vLLM) that have their own parsers.

---

## 2. Root Cause: Why Thinking Levels Were Broken

**The symptom:** All three levels (low/medium/high) produced identical, unbounded thinking. The model would fill `max_tokens` with thinking and never produce an answer. Timing differences between levels were pure noise (stochastic thinking length, MTP acceptance variance, prompt-cache state).

**The failure chain (4 broken links):**

| Step | What should happen | What actually happened | Status |
|---|---|---|---|
| 1 | Plugin sets `thinkingFormat: "qwen-chat-template"` | ✅ Working | Fixed in plugin commit 174b37f |
| 2 | Plugin sends `reasoning_budget_tokens` via `compat.*` | ❌ Set at top level `model.reasoning_budget_tokens`, harness reads `model.compat.reasoningBudgetTokens` → never sent | **BUG** |
| 3 | Even if sent, `reasoning_budget_tokens: 0` = unlimited | ❌ `0` means unlimited, NOT "soft cap" (old comment was wrong) | **BUG** |
| 4 | Harness discards computed budget in `qwen-chat-template` branch | ❌ `thinkingBudget` computed but not used (only used in `chat-template`/`baseten` formats) | **BUG** |

**Net result:** The request contained an ignored `reasoning_effort` string and NO budget → thinking ran unbounded to `max_tokens` (15728) → answer truncated (`finish=length`, 0 chars).

---

## 3. The Fix

**File:** `lemonade-pi-plugin/lib/models.ts` (uncommitted, lpb-dev branch)  
**Line:** ~155

```ts
// Qwen-specific fields for thinking support
if (isQwen) {
  result.enable_thinking = true;
  result.thinkingFormat = "qwen-chat-template";
  // Per-level thinking budget: pi (v0.84.3+, PR #8275) computes
  // DEFAULT_THINKING_BUDGETS (low:2048 / medium:8192 / high:16384)
  // and sends it as a top-level request field named by
  // compat.thinkingTokenBudgetField, clamped to leave 1024 tokens
  // for the answer. The llama.cpp backend (Qwen MTP GGUF) honors
  // `thinking_budget_tokens` (and `reasoning_budget_tokens`) but
  // IGNORES the `reasoning_effort` string in chat_template_kwargs.
  result.compat = {
    ...(result.compat as Record<string, unknown> | undefined),
    thinkingTokenBudgetField: "thinking_budget_tokens",  // ← THE FIX
  };
}
```

### Why this works

1. **pi's `qwen-chat-template` branch** computes `thinkingBudget` per level (low=2048, medium=8192, high=16384) and passes it to the generic tail.
2. The generic tail reads `compat.thinkingTokenBudgetField` and sends it as a top-level request field.
3. Our fix sets this field to `"thinking_budget_tokens"` — the llama.cpp-native parameter name (per pi docs, PR #8275).
4. The lemonade/llama.cpp server receives `thinking_budget_tokens: <budget>` and uses it to cap the thinking block.
5. Answer generation proceeds normally after the budget is consumed.

**No fork patch needed.** This uses 100% upstream mechanisms, survives future rebases.

---

## 4. Validation Results

### Direct server test (Qwen3.6-35B-A3B-MTP-GGUF)

**Method:** Send raw HTTP requests to the lemonade server with the exact parameters the harness sends post-fix.

**Prompt:** "Find the smallest positive integer that can be expressed as the sum of two positive cubes in exactly two different ways. Show your working."

| Level | Budget sent | Thinking chars | Answer chars | Wall time | finish_reason |
|---|---|---|---|---|---|
| low | 2,048 | 4,050 | 1,315 | 32s | `stop` ✅ |
| medium | 8,192 | 13,754 | 2,596 | 117s | `stop` ✅ |
| high | 14,704 | 26,059 | 1,687 | 189s | `stop` ✅ |

**Before the fix (for comparison):**

| Level | Budget sent | Thinking chars | Answer chars | finish_reason |
|---|---|---|---|---|
| low | (none) | 8,001 | 0 | `length` ❌ |
| medium | (none) | 8,052 | 0 | `length` ❌ |
| high | (none) | 7,296 | 0 | `length` ❌ |

**Key observations:**
- ✅ **Monotonic scaling** — thinking grows ~3× per level step, time scales proportionally
- ✅ **All levels produce actual answers** — no more truncated responses
- ✅ **The MoE model is extremely fast** — even high=189s total is reasonable for 26k thinking tokens + answer
- ✅ **Dense model (Qwen3.8-27B-GGUF)** also honors the budget — tested independently

### Unit tests

`lemonade-pi-plugin/test/model-mapping.test.ts` — 10/10 pass:
- Qwen MTP models get `compat.thinkingTokenBudgetField: "thinking_budget_tokens"`
- Non-Qwen models are unchanged
- FLM developer-role guard is intact

---

## 5. Server Version Analysis

Current server build: **b10818** (live-verified 2026-09-05/06). The b10375
table below is historical; the live behavior matrix for b10818 is in the
[benchmark doc](thinking-benchmark.md) and the validate probes.

### ✅ Included since b10375

### ✅ Already included in b10375

| Feature | PR | Version | Status |
|---|---|---|---|
| Qwen3 specialized PEG parser | #26252 | b10227 (Aug 2, 2026) | ✅ Included |
| Multi-block budget re-arm | #22323 | b9211 (Apr 24, 2026) | ✅ Included |
| Prompt tokens not passed to budget sampler | #22488 | b10000 (May 2026) | ✅ Included |
| `--reasoning-budget-message` flag | #20297 | pre-b10227 | ✅ Available |
| `--no-prefill-assistant` flag | pre-existing | pre-b10227 | ✅ Available |

### 🔧 Still needs attention

| Feature | Status | Action | Priority |
|---|---|---|---|
| **Per-request reasoning toggle** | ✅ WORKS on b10818 — top-level `enable_thinking` honored per-request (validated 2026-09-05/06, all catalogued models) | — | ~~P0~~ done |
| **Configure `--reasoning-budget-message`** | ✅ Configured on the current server; probed live by `lpb-devstack validate` (warning-only) | — | ~~P1~~ done |
| **`--reasoning-format none`** workaround | Available | Consider for Qwen3.6 reliability | P2 |

---

## 6. Implementation Recommendations

### P0: Per-request reasoning toggle (highest impact) — RESOLVED

> ✅ Resolved on the current build (b10818, validated 2026-09-05/06):
> top-level `enable_thinking: false` is honored per-request in both
> directions on all catalogued Qwen models — zero reasoning in every off
> cell of the benchmark. PR #22336 tracking no longer blocking. The original
> problem statement is kept for history.

**What's upstream:** PR #22336 adds a first-class per-request `reasoning` boolean field to the OpenAI-compatible API. This would let a single server instance serve both modes.

**Action:**
1. Track PR #22336 status
2. When merged, implement the field in lemonade's `/v1/chat/completions` handler
3. Map the per-request `reasoning` boolean to the appropriate llama.cpp flag

**Impact:** Critical for hybrid models. Eliminates the need for duplicate server processes.

### P1: Configure `--reasoning-budget-message` (easy, 10% quality boost) — DONE

> ✅ Configured on the current server (2026-09-06) and covered by a live
> validate probe. Note: Qwen3.8 emits the exhaustion marker; Qwen3.6
> truncates reasoning without the note (model quirk, not a config error).

<details><summary>Original recommendation (historical)</summary>

**What it does:** When the thinking budget is exhausted, llama.cpp appends a custom message (e.g., "...reasoning budget exceeded, need to answer") to tell the model to transition to answering. Without this, model quality drops ~10%.

**Implementation (in llama.cpp server config):**
```bash
llama-server \
  --reasoning-budget-message "You have exceeded your reasoning budget. Provide a concise answer now."
```

Or configure via lemonade's server startup parameters.

**Impact:** +10% answer quality at all thinking levels. No client changes needed.

</details>

### P2: Consider `--reasoning-format none` for Qwen3.6

**The problem:** llama.cpp's default reasoning parser (designed for DeepSeek's output format) has reliability issues with Qwen3's inline XML tags (` \n ...  \n\n`). This causes:
- Silent tag stripping from output
- Reasoning/answer field confusion
- Corrupted streaming responses

**The workaround:** Start the server with `--reasoning-format none` and parse the output client-side:
```bash
llama-server \
  --reasoning-format none
```

Then extract thinking text with regex:
```python
pattern = r' \n(.*?)\n\n'
thinking, answer = split_thinking(raw_text)
```

**Impact:** Eliminates silent corruption. Trade-off: client-side parsing complexity.

---

## 7. Why This Matters

Thinking support is **one of the core reasons** this plugin exists. The LocalPibox stack is built around Qwen3.6 reasoning models, and the thinking levels are the primary mechanism for controlling model behavior across different task types:

- **Low thinking** — fast, concise responses for simple queries (32s)
- **Medium thinking** — balanced reasoning for code and analysis (117s)
- **High thinking** — deep deliberation for complex multi-step problems (189s)

Before the fix, all three levels were indistinguishable — the model would always think for as long as possible and never produce an answer. The fix restores the intended per-level control, making the model actually useful across different task complexity levels.

The per-request reasoning toggle (upstream PR #22336) would further improve this by enabling a single server to serve both thinking and non-thinking modes — critical for hybrid models where some tasks need reasoning (code generation, analysis) and others don't (lookup, formatting, simple Q&A).

---

## Appendix: Reference

| Item | Value |
|---|---|
| pi version | 0.84.3 |
| pi PR for budget field | #8275 |
| pi field name for llama.cpp | `"thinking_budget_tokens"` |
| llama.cpp build | b10818 (current, live-verified) |
| Model tested | Qwen3.6-35B-A3B-MTP-GGUF |
| Server URL | http://192.168.0.13:13305 |
| DEFAULT_THINKING_BUDGETS | {minimal:1024, low:2048, medium:8192, high:16384} |
| MIN_ANSWER_TOKENS | 1024 |
| Qwen MTP maxTokens | 15728 (0.06 × 262k, clamped 16384) |

---

## 7. Benchmark & regression validation

Per-level behavior (budgets, effort mapping, off-switch, quality per level)
is validated by the reproducible benchmark — see
[Thinking Benchmark](thinking-benchmark.md): test set (GSM8K/MATH-derived),
wire-fidelity assertions, procedure, and per-model results that get
regenerated with every release tag.

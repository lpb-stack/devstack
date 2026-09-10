# Thinking Benchmark — Test Set, Procedure & Results

> ⚠️ Point-in-time results (2026-09-10). The tables below are a snapshot from
> the stack versions listed in each run; re-run after every pi / plugin /
> server bump and replace the tables (see [Procedure](#procedure)).

Validates the full thinking chain — Pi level → lemonade-pi-plugin payload
tuning (P2 budget, effortMap, P3 sampling, P5 off-switch) → llama.cpp
server → response — with a small set of battle-tested reasoning problems.

- **Harness:** `support/thinking-benchmark.py` (devstack)
- **Related:** [Thinking support](thinking-support.md) for the wire-format
  background and server flags.

## Test set

12 single-shot problems with deterministic, machine-verifiable answers,
adapted from public battle-tested benchmarks:

| Tier | Count | Source | Expectation |
|---|---|---|---|
| Easy | 3 | GSM8K-style 2-step word problems (openai/grade-school-math) | pass in both modes |
| Medium | 4 | GSM8K multi-step + MATH L3-style (hendrycks/math) | non-thinking misses some |
| Hard | 5 | MATH L4-5 style: inclusion-exclusion, stars-and-bars, coin probability, exact-value log equation, recurrence | require thinking |

All answers were machine-verified before inclusion. Scoring is keyword
match with digit-boundary guards (no substring false-positives like "10"
inside "100"). Prompts and keywords live in `support/thinking-benchmark.py`
(`PROMPTS` / `ANSWER_KEYWORDS`).

## What the harness checks per cell

For every model × level combination the harness asserts **wire fidelity** —
that each plugin-tuned parameter actually reached the backend and had its
expected effect:

- **P2:** `thinking_budget_tokens` equals the catalog budget re-clamped to
  `maxTokens − 1024`
- **effortMap:** the on-wire `reasoning_effort` is the model-mapped value
  (e.g. Qwen3.8: minimal→low, high→xhigh)
- **P3:** sampling row applied (thinking row for on-levels, nonThinking row
  at off)
- **P5:** at off, `enable_thinking: false` is on the wire **and** the
  response contains zero reasoning chars

Server health flags (`--cache-ram`, `--reasoning-budget-message`) are probed
separately by `lpb-devstack validate` (see `scripts/localpibox/stack/serverhealth.py`).

## Procedure

```bash
# one model, all levels, with a version-stamped markdown report
python3 support/thinking-benchmark.py \
  --models Qwen3.8-27B-GGUF \
  --levels off,minimal,low,medium,high \
  --report ~/thinking-reports/qwen3.8-27b-gguf.md

# repeat per model; then update the tables below and commit with docs:
```

The report stamps pi version, plugin git rev, and server build fingerprint —
keep those in the tables when updating. Runs are sequential (single-GPU
server); a full model takes ~10-20 min.

## Results (2026-09-10)

Server: llama.cpp `b10865` (`d4389a4dd`) · pi 0.85.1 · lemonade-pi-plugin
`54973b2` · 1 run per cell · wire fidelity 19/19 (Qwen3.8), 19/19 (Qwen3.6,
with effortMap) and 15/15 (Gemma, no effortMap)

### Qwen3.8-27B-GGUF (dense)

| Level | Score | Avg reasoning chars | Avg time |
|---|---|---|---|
| off | 10/12 (83%) | 0 | 12.5s |
| minimal | **12/12 (100%)** | 357 | 10.7s |
| low | **12/12 (100%)** | 390 | 10.8s |
| medium | **12/12 (100%)** | 385 | 12.3s |
| high | 11/12 (92%) | 384 | 8.8s |

**Thinking lift:** `math_coin_prob` and `math_log_eq` fail at off and pass
with thinking on (high again misses `math_log_eq`). The only model in this
set where the level dial measurably moves quality.

### Qwen3.6-35B-A3B-MTP-GGUF (MoE, MTP draft)

| Level | Score | Avg reasoning chars | Avg time |
|---|---|---|---|
| off | 11/12 (92%) | 0 | 4.7s |
| minimal | 11/12 (92%) | 3329 | 19.1s |
| low | 11/12 (92%) | 3485 | 18.9s |
| medium | 11/12 (92%) | 4159 | 22.4s |
| high | 11/12 (92%) | 3646 | 19.1s |

**Flat band:** reasoning stays ~3-4k chars regardless of level — the model
thinks its natural length and stops before any budget is hit (effortMap
maps high→xhigh on the wire, verified). `math_log_eq` fails at **all**
levels (capability gap, not a thinking effect). Thinking adds latency
without accuracy gain on this set.

### Gemma-4-26B-A4B-it-MTP-GGUF (MoE, MTP draft)

| Level | Score | Avg reasoning chars | Avg time |
|---|---|---|---|
| off | 11/12 (92%) | 0 | 3.4s |
| minimal | 11/12 (92%) | 1178 | 9.7s |
| low | 11/12 (92%) | 1184 | 9.5s |
| medium | 11/12 (92%) | 1421 | 11.0s |
| high | 11/12 (92%) | 1255 | 9.8s |

Same pattern as Qwen3.6: flat reasoning band (~1.2-1.4k chars),
`math_log_eq` fails at all levels, no thinking lift on this set. No
`effortMap` in the catalog — levels pass through unchanged (all accepted
by its template). User-tier `maxTokens: 16384` re-clamps the high budget
to 15360.

### Cross-model reading

- **Off switch works everywhere:** zero reasoning at off on all three
  models (`enable_thinking: false` honored per-request).
- **`math_log_eq` (x = 1+√17)** is the set's sharpest discriminator: only
  Qwen3.8 solves it, and only with thinking.
- **Token caveat:** `completion_tokens` includes MTP draft tokens on
  \*MTP models — compare reasoning chars and wall time across models, not
  token totals.

## Releasing results with a tag

The tables above are the published record. When cutting a stable release:

1. Run the benchmark for each supported model (Procedure above).
2. Replace the result tables + version line in this file.
3. Commit on `dev` with a `docs:` prefix — the release docs gate
   (`lpb-devstack release docs-ready`) picks it up like any doc change.

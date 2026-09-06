# Thinking Benchmark — Test Set, Procedure & Results

> ⚠️ Point-in-time results (2026-09-07). The tables below are a snapshot from
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

## Results (2026-09-07)

Server: llama.cpp `b10818` · pi 0.85.1 · lemonade-pi-plugin `81b8346`
· 1 run per cell · wire fidelity 19/19 (Qwen) and 14/14 (Gemma, no effortMap)

### Qwen3.8-27B-GGUF (dense)

| Level | Score | Avg reasoning chars | Avg time |
|---|---|---|---|
| off | 10/12 (83%) | 0 | 11.8s |
| minimal | **12/12 (100%)** | 379 | 10.9s |
| low | **12/12 (100%)** | 378 | 10.3s |
| medium | **12/12 (100%)** | 442 | 12.4s |
| high | 11/12 (92%) | 596 | 11.7s |

**Thinking lift:** `math_coin_prob` and (usually) `math_log_eq` fail at off
and pass with thinking on. The only model in this set where the level dial
measurably moves quality.

### Qwen3.6-35B-A3B-MTP-GGUF (MoE, MTP draft)

| Level | Score | Avg reasoning chars | Avg time |
|---|---|---|---|
| off | 11/12 (92%) | 0 | 4.0s |
| minimal | 11/12 (92%) | 3112 | 19.7s |
| low | 11/12 (92%) | 2921 | 17.7s |
| medium | 11/12 (92%) | 5910 | 33.1s |
| high | 11/12 (92%) | 3697 | 22.3s |

**Flat band:** reasoning stays ~3-6k chars regardless of level — the model
thinks its natural length and stops before any budget is hit. `math_log_eq`
fails at **all** levels (capability gap, not a thinking effect). Thinking
adds latency without accuracy gain on this set.

### Gemma-4-26B-A4B-it-MTP-GGUF (MoE, MTP draft)

| Level | Score | Avg reasoning chars | Avg time |
|---|---|---|---|
| off | 11/12 (92%) | 0 | 3.0s |
| minimal | 11/12 (92%) | 1239 | 9.7s |
| low | 11/12 (92%) | 1494 | 11.3s |
| medium | 11/12 (92%) | 1373 | 10.3s |
| high | 11/12 (92%) | 1149 | 8.9s |

Same pattern as Qwen3.6: flat reasoning band, `math_log_eq` fails at all
levels, no thinking lift on this set. No effortMap in the catalog — levels
pass through unchanged (all accepted by its template). User-tier
`maxTokens: 8192` correctly re-clamps medium/high budgets to 7168.

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

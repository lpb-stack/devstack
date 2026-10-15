# Strixite Integration — pi-native provider, no plugin

Strixite exposes an OpenAI-compatible endpoint and reports its context window
in `/v1/models`, so **Pi talks to it directly through `models.json`** — no
provider extension, unlike Lemonade:

| | Lemonade | Strixite |
|---|---|---|
| registered in | `auth.json` + `lemonade-pi-plugin` | `models.json` (pi-native custom provider) |
| model catalog | plugin refreshes the Lemonade API | read once from `GET /v1/models` |
| per-model tuning | plugin catalog + `~/.pi/agent/model-params.json` | `MODEL_CAPABILITIES` in `scripts/localpibox/setup.py` |
| thinking wire shape | plugin rewrites the payload | Pi's own OpenAI-completions path |

The whole integration is therefore **metadata Pi reads**, not code.
`MODEL_CAPABILITIES` (setup.py) holds the per-provider defaults the wizard
writes into `~/.pi/agent/models.json`; the live server is the source of truth
for the context window.

## The entry the wizard writes

```json
{
  "providers": {
    "strixite": {
      "baseUrl": "http://127.0.0.1:8000/v1",
      "api": "openai-completions",
      "apiKey": "strixite",
      "models": [{
        "id": "qwen3.8-flash-next",
        "reasoning": true,
        "input": ["text", "image"],
        "contextWindow": 262144,
        "maxTokens": 16384,
        "thinkingLevelMap": { "off": "none", "max": "xhigh" },
        "compat": { "thinkingFormat": "openai", "supportsDeveloperRole": false }
      }]
    }
  }
}
```

Each field exists because of a measured server behaviour — see
[Wire facts](#wire-facts-validated-live). Without them Pi shows the model as
thinking-off (and never sends an opt-out, so it thinks anyway), guesses a
128k context, and sends `reasoning_effort: "max"`, which the server rejects.

## Wire facts (validated live)

Re-run `support/strixite-probe.py` to check them all after a server upgrade.

| Fact | Consequence for `models.json` |
|---|---|
| `/v1/models` reports `context_length` (262144 on the current build) | `contextWindow`, refreshed from the server on every setup run — the operator sets it with `-c/--ctx-size`, so a stale value would overflow at runtime |
| `reasoning_effort` accepts `none`, `minimal`, `low`, `medium`, `high`, `xhigh`; **400** on `max` and `auto` | `thinkingLevelMap.max = "xhigh"` |
| Strixite's own aliasing: `minimal = low`, `high = xhigh` | Pi's 7 levels collapse to 4 real behaviours — `low`≈`minimal`, `xhigh`≈`high`≈`max` |
| No thinking field at all = server default = **thinking on** | `off` must be an explicit wire value: `thinkingLevelMap.off = "none"` |
| Two spellings that disagree → 400 (`thinking on / off disagree`) | exactly one spelling per request → `compat.thinkingFormat = "openai"` (never `enable_thinking` alongside) |
| ChatML renderer rejects `messages[0].role == "developer"` → 400 | `compat.supportsDeveloperRole = false` — Pi otherwise uses that role as soon as `reasoning: true` |
| `input: ["text","image"]` works; the vision projector is required at launch | `input: ["text", "image"]` |
| `max_completion_tokens` (and `max_tokens`) honoured; if the cap leaves no room for the window, thinking is **skipped** with a warning | `maxTokens` must stay well under the context: thinking and the answer share one budget |
| Budget fields accepted (`max_thinking_tokens`, `thinking.budget_tokens`, …); disagreeing values → 400 | not used — Pi sends no budget for strixite, level only |
| `store` / `include` / `stream_options` are ignored, not rejected | no compat flags needed for Pi's extras |
| Streaming terminal chunk carries `usage` + `strix`; `strix` also on the last content chunk, with `n_thinking_tokens` for billing | usage tracking works unchanged |

## Thinking level — measured

6 classic reasoning problems (snail/well, bat+ball, …), single shot, thinking
on vs off, on the current single-user build:

| Pi level | Sent as | Correct | Avg wall time | Avg completion tokens |
|---|---|---|---|---|
| `off` | `none` | 6/6 | 0.2 s | 2 |
| `minimal` | `minimal` (= low) | 6/6 | 5.5 s | 308 |
| `low` | `low` | 6/6 | 3.4 s | 182 |
| `medium` | `medium` | 6/6 | 8.3 s | 465 |
| `high` | `high` (= xhigh) | 6/6 | 9.5 s | 423 |
| `xhigh` | `xhigh` | 5/6 | 9.9 s | 434 |

Reading of it, with the caveats stated:

- **`low` is the practical default.** Same accuracy as `medium` here at ~40 %
  of the time, and thinking costs real time on this build (tens of tokens per
  second, so a 450-token chain of thought is seconds per turn).
- **`xhigh` is not worth it** — it was the only level to get one answer wrong,
  and `high`/`max` alias to the same behaviour anyway.
- **`off` answered everything here**, on problems a non-thinking model can
  pattern-match. Do not read that as "thinking is unnecessary": the sample is
  6 small puzzles, not multi-file coding work. Keep `off` for cheap bulk turns.
- One sample per level, small tasks: treat this as a cost/benefit signal, not a
  capability ranking. `support/thinking-benchmark.py` (12 graded problems,
  several tiers) is the instrument for a real ranking.

Set it with `lpb-config set thinking <level>` (or `/settings` inside a session);
`lpb-config show` prints what is active. Nothing in the stack env pins it — the
value lives in `settings.json` (`defaultThinkingLevel`).

## Re-validating

```bash
# wire contract vs. MODEL_CAPABILITIES — exits non-zero on drift
python3 support/strixite-probe.py --server http://127.0.0.1:8000 \
    --model qwen3.8-flash-next --api-key strixite

# health check (the models.json line reports missing capabilities)
lpb-config check
```

Run the probe after a strixite bump, after a Pi bump (Pi decides the request
shape, not us), and whenever `MODEL_CAPABILITIES` changes.

## Hand-tuning

Edit `~/.pi/agent/models.json` directly and restart Pi (or `/new`). Re-running
setup **keeps hand-tuned keys** — the one exception is `contextWindow`, which is
refreshed from the live server because it changes with every `-c` restart.
`~/.pi/agent/model-params.json` is read by the Lemonade plugin only; it has no
effect on a strixite-served model.

## Related

- [lpb-config reference](lpb-config.md) — provider setup, `set base-url`, `check`
- [Thinking support](thinking-support.md) — wire-format background and Lemonade's plugin path
- [Thinking benchmark](thinking-benchmark.md) — the graded test set

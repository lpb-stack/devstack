# lpb-memory Extension — Current Status

> **Last updated:** 2026-08-25
> **Status:** Active — memory writes and reads working
> **Extension:** lpb-memory (subprocess transport, NPU model)

---

## Current Configuration

The extension is configured in `~/.pi/agent/lpb-memory-config.json`:

```json
{
  "memoryMode": "legacy-inject",
  "memoryPolicyStyle": "none",
  "reviewTransport": "subprocess",
  "llmModelOverride": "qwen3.5-9b-FLM",
  "failureInjectionEnabled": true,
  "failureInjectionMaxEntries": 3,
  "failureInjectionMaxAgeDays": 3
}
```

### What This Means

| Setting | Value | Effect |
|---|---|---|
| `memoryMode` | `legacy-inject` | Memory (MEMORY.md, USER.md, failures.md) injected into system prompt every turn |
| `memoryPolicyStyle` | `none` | No policy prompt — saves context tokens |
| `reviewTransport` | `subprocess` | Reviews run in subprocess (NPU model), offloaded from main session |
| `llmModelOverride` | `qwen3.5-9b-FLM` | NPU model used for reviews (FLM backend — no thinking) |
| `failureInjectionEnabled` | `true` | Recent failures injected into context |
| `failureInjectionMaxEntries` | `3` | Last 3 failures injected |
| `failureInjectionMaxAgeDays` | `3` | Only failures from last 3 days |

---

## Memory Flow (Current Architecture)

```
┌─────────────────────────────────────────────────────────────────┐
│                      Main Session                               │
│                                                                 │
│  System prompt includes:                                        │
│  • MEMORY.md (technical insights)                             │
│  • failures.md (last 3 failures, if < 3 days old)             │
│  • USER.md (preferences — created once saved)                │
│                                                                 │
│  Memory SEARCH tool available                                   │
│  Memory INJECTION automatic (legacy-inject mode)                │
└─────────────────────────────────────────────────────────────────┘
         ▲
         │ subprocess (NPU model)
         │
┌────────┴──────────────────────────────────────────────────────┐
│  Background Operations (offloaded to NPU)                     │
│                                                               │
│  review   ──► every 10 turns, extracts memories              │
│  flush    ──► session end, saves memories                    │
│  correct  ──► detects user corrections                       │
│  consolidate ──► merges similar entries                     │
└───────────────────────────────────────────────────────────────┘
```

### Memory Content (Current State)

Counts reflect `~/.pi/agent/lpb-memory/` as of 2026-08-25 — they grow and
shrink with normal use; re-check rather than quote these numbers.

| File | Entries | Notes |
|---|---|---|
| `MEMORY.md` | 3 | Technical insights — actionable |
| `USER.md` | — (not yet created) | Created when user preferences are saved |
| `failures.md` | 1 | Lessons learned (injected when < 3 days old) |

---

## What's Working

✅ **Memory writes** — `review` subprocess extracts memories every 10 turns
✅ **Memory injection** — `legacy-inject` mode injects memory into system prompt
✅ **Subprocess transport** — Reviews run on NPU model, offloaded from main session
✅ **Failure injection** — Recent failures injected into context
✅ **Memory search** — `memory_search` tool available for proactive search

---

## Known Issues

### 1. NPU Model Routing

The `llmModelOverride: "qwen3.5-9b-FLM"` sometimes doesn't route correctly to
the NPU subprocess.

**Symptom:** Reviews run on the main model instead of the NPU model.

**Note:** the override pins a model name the LLM server may not currently
serve (the main server hosts Qwen3.6-35B) — a missing model, not just a
routing bug, produces the same symptom.

**Debug path:** check `pi-child-process.ts` — verify `llmModelOverride`
survives to the child CLI args, and confirm the model name against the
server's `/v1/models`.

---

## Current Config History

| Date | Change | Reason |
|---|---|---|
| 2026-08-16 | Analysis created — `policy-only` mode | Memory not visible in main session |
| 2026-08-17 | Switched to `legacy-inject` | Phase 1 fix — memory now injected |
| 2026-08-17 | Set `memoryPolicyStyle: none` | Phase 1 fix — save context tokens |
| 2026-08-25 | Current config — all Phase 1 applied | Memory working in injection mode |

---

## History — the 2026-08-16 Diagnostic

The original point-in-time analysis (2026-08-16) found memory writes working
but reads missing: the default `policy-only` mode injects a long
`<memory-policy>` prompt instead of the memory itself, and the model did not
reliably call `memory_search`. Secondary issues were the 40-line policy
prompt, and unreliable routing of the NPU review model.

Applied fixes (see Config History above): `legacy-inject` mode (memory in
the system prompt every turn), `memoryPolicyStyle: none` (no policy prompt),
subprocess reviews on the NPU model, failure injection. The long-tail
suggestions from that analysis (target-filtered injection, topic-based
smart injection, memory summarization) remain open ideas for the
lpb-memory extension, not current behavior.

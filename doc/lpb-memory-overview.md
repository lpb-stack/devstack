# lpb-memory Overview

The **lpb-memory** extension gives the Pi coding agent persistent memory
across sessions: a searchable store of lessons, preferences, and technical
insights (`memory_search`), a search index over past sessions
(`session_search`), and procedural skills the agent saves as it works
(`skill_manage`).

## What It Does

The extension performs four background operations. Reviews run in a
**subprocess with a separate review model**: by default that's the main
session model (so nothing extra to configure), or a small dedicated model
(e.g. a local NPU model) if you set `llmModelOverride`.

| Operation | When | What it does |
|---|---|---|
| **`review`** | Every 10 turns (configurable) | Scans recent conversation, extracts actionable memories |
| **`flush`** | Session end / compaction | Final review pass, saves extracted memories to store |
| **`correct`** | On user correction | Detects user corrections to AI behavior, records as lessons |
| **`consolidate`** | Periodic | Merges similar entries (reduces duplication) |

Agent-facing capabilities (beyond the background operations):

| Capability | How |
|---|---|
| Memory search | `memory_search` tool — full-text (SQLite FTS5) search over the memory store |
| Session search | `session_search` tool — searches indexed past sessions (one-time index: `/memory-index-sessions`) |
| Procedural skills | `skill_manage` tool — the agent saves *how* it solved problems, reusable across sessions (`/memory-skills` lists them) |
| Secret scanning | API keys and tokens are blocked from being saved |

## Origin & Status

Forked from [Pi Hermes Memory](https://github.com/chandra447/pi-hermes-memory)
and extensively refactored to work fully locally: subprocess review transport
that offloads background reviews to a small local model (NPU-friendly),
model/thinking overrides for the review pass, serialized review spawning with
backoff, and centralized configuration under `~/.pi/agent/`. It no longer
tracks upstream and is developed as part of the
[LocalPibox stack](https://github.com/lpb-stack/lpb-memory) — **still a work
in progress**.

## Architecture

```
Main Session (main model)
  │
  ├─ AI calls memory_search → SQLite FTS5 search
  │
  └─ System prompt contains memory policy (tells AI to search)

Subprocess (review model — main session model unless overridden)
  │
  ├─ review    → extracts memories every N turns
  ├─ flush     → final pass at session end
  ├─ correct   → captures user corrections
  └─ consolidate → merges similar entries
  │
  └─ Writes to: ~/.pi/agent/lpb-memory/{USER,MEMORY,failures}.md
```

### Memory Modes

| Mode | Behaviour |
|---|---|
| `legacy-inject` (ships in the devstack template) | Injects memory content (MEMORY.md, USER.md, failures.md, within char limits) into the system prompt. AI sees memory automatically. |
| `policy-only` (extension default) | Injects a `<memory-policy>` block instructing the AI to call `memory_search` when needed. More context-efficient, but relies on the AI following the policy. |

**Policy-only** is more context-efficient but requires the AI to trust the
policy and call `memory_search` proactively. **legacy-inject** guarantees
visibility at the cost of ~2-3KB context per turn.

### Transport

| Transport | Behaviour |
|---|---|
| `subprocess` (default) | Runs review as a separate process using the NPU model. Offloaded from main session. |
| `direct` | Uses the main model directly (no NPU). Higher latency, no isolation. |

## Data Locations

Memory data lives on the **host volume** at `~/.pi/agent/lpb-memory/`:

| File | Content | Entries (typical) |
|---|---|---|
| `USER.md` | User preferences, workflows, constraints | 10-20 entries |
| `MEMORY.md` | Technical insights, tool discoveries | 5-15 entries |
| `failures.md` | Lessons from mistakes, corrections | 10-30 entries |

These files are written by the subprocess review and persisted across
container rebuilds.

## Configuration

Configuration lives in `~/.pi/agent/lpb-memory-config.json` (generated from
the template at boot, managed by `lpb-config memory setup`):

| Setting | Devstack template | Extension default | Description |
|---|---|---|---|
| `memoryMode` | `legacy-inject` | `policy-only` | How memory reaches the AI (see above) |
| `memoryPolicyStyle` | `none` | `full` | Policy verbosity in `policy-only` mode: `full` or `compact` |
| `reviewTransport` | `subprocess` | `subprocess` | Offload reviews to a subprocess, or `direct` (main session) |
| `llmModelOverride` | *(unset)* | *(unset)* | Review model — **unset = main session model**; set e.g. a small local NPU model |
| `memoryCharLimit` | `3000` | `5000` | Max chars of MEMORY.md injected per turn |
| `userCharLimit` | `3000` | `5000` | Max chars of USER.md injected per turn |
| `projectCharLimit` | `2000` | `5000` | Max chars of project-scoped memory |
| `failureInjectionEnabled` | `true` | `true` | Inject recent failure lessons into the prompt |
| `failureInjectionMaxEntries` | `3` | `5` | Max failure entries injected |
| `failureInjectionMaxAgeDays` | `3` | `7` | Only inject failures newer than this |
| `nudgeInterval` | *(unset)* | `10` | Review every N turns |
| `autoConsolidate` | *(unset)* | `true` | Merge similar entries automatically |
| `reviewTimeoutMs` / `consolidationTimeoutMs` | `300000` | `300000` | Max time per operation (5 min) |

### Configuring

```bash
# Show current config
lpb-config memory show

# Interactive wizard
lpb-config memory setup

# Edit config directly
nano ~/.pi/agent/lpb-memory-config.json
```

## Backup & Privacy

- Memory files are plain text on the host volume — they survive container
  rebuilds but can be backed up manually:
  ```bash
  tar czf ~/lpb-memory-backup.tar.gz ~/.pi/agent/lpb-memory/
  ```
- Memory data is processed by the review model (a local model by default).
  No memory data is sent to external services.
- To reset memory: delete the `lpb-memory/` directory contents and run
  `lpb-config memory setup` to reset the config.

## Quick Reference

```bash
# See what's in memory
cat ~/.pi/agent/lpb-memory/USER.md
cat ~/.pi/agent/lpb-memory/MEMORY.md
cat ~/.pi/agent/lpb-memory/failures.md

# In Pi: index your past sessions once, then use the session_search tool
/memory-index-sessions

# Show current configuration
lpb-config memory show

# Configure interactively
lpb-config memory setup

# Backup
tar czf ~/lpb-memory-backup.tar.gz ~/.pi/agent/lpb-memory/

# Reset (careful: deletes all memory)
rm ~/.pi/agent/lpb-memory/*
lpb-config memory setup
```

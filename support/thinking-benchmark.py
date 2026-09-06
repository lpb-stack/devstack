#!/usr/bin/env python3
"""
Thinking Level Integration Benchmark — lemonade-pi-plugin / llama.cpp
=====================================================================

Validates the FULL chain after any version bump (pi, plugin, server):
user-facing Pi thinking level → plugin payload tuning (P2/P3/P5) → wire
payload → Lemonade server → response.

SOURCE OF TRUTH for expected wire params is the plugin's own catalog
(`lemonade-pi-plugin/lib/model-params.json`, user tier merged over plugin
tier), so this benchmark asserts that every param the plugin would put on
the wire actually reaches the backend and produces the expected behavior:

  P2  — per-level `thinking_budget_tokens` from the catalog `budgets` table,
        re-clamped to (max_completion_tokens − 1024) exactly like
        payload-tuning.ts does
  effortMap — pi level → model-specific `reasoning_effort` value (e.g.
        Qwen3.8: high→xhigh; minimal is rejected by its template → skipped)
  P3  — sampling rows: `thinking` row for on-levels, `nonThinking` row for
        off (fill-missing semantics — explicit values win)
  P5  — off level: catalog `offParams` (`enable_thinking: false`) must be
        honored → ZERO reasoning_content in the response

Server health probes (KV-cache persistence, --reasoning-budget-message)
live in scripts/localpibox/stack/serverhealth.py and run via
`lpb-devstack validate` — this benchmark focuses on per-level behavior.

Usage:
  # Single cell (recommended for heavy benchmarks; subagent harness mode):
  python3 support/thinking-benchmark.py --models Qwen3.8-27B-GGUF --levels off

  # All levels for one model, with a version-stamped markdown report:
  python3 support/thinking-benchmark.py --models Qwen3.8-27B-GGUF \
      --levels off,low,medium,high --report /tmp/report-qwen3.8.md

  # Full matrix (all catalogued models × all levels):
  python3 support/thinking-benchmark.py

Results: JSON to /tmp (per-record in single mode) and optional markdown
report via --report — regenerate after every pi/plugin/llama.cpp bump and
diff against the previous report.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from statistics import mean
from pathlib import Path

# ─── Configuration ────────────────────────────────────────────────────────────

DEFAULT_SERVER = "http://192.168.0.13:13305"
DEFAULT_API_KEY = os.environ.get("LEMONADE_API_KEY", "")

# Pi's testable thinking levels (off is binary, others have budget tiers)
TEST_LEVELS = ["off", "minimal", "low", "medium", "high"]

# Budget table level for xhigh/max (pi clamps them to high in payload-tuning.ts)
BUDGET_LEVEL_CLAMP = {"xhigh": "high", "max": "high"}

MIN_ANSWER_TOKENS = 1024  # mirror of pi's MIN_ANSWER_TOKENS / payload-tuning.ts

# Plugin catalog locations (plugin tier; user tier merged over it, same as
# lib/model-params.ts resolveModelEntry)
PLUGIN_CATALOG_CANDIDATES = [
    Path.home() / "workspace" / "lemonade-pi-plugin" / "lib" / "model-params.json",
]
USER_CATALOG = Path(os.environ.get("LPB_MODEL_PARAMS_FILE",
                                   str(Path.home() / ".pi" / "agent" / "model-params.json")))

NO_THINK_SUFFIX = "/no_think"  # lib/payload-debug.ts default (unused when offParams present)


# ─── Catalog loading (mirrors lib/model-params.ts merge) ──────────────────────

def load_catalog() -> dict:
    """Merge user tier over plugin tier; return {model_id: entry}."""
    catalog = {}
    for path in PLUGIN_CATALOG_CANDIDATES:
        if path.exists():
            with open(path) as f:
                catalog.update(json.load(f))
    if USER_CATALOG.exists():
        with open(USER_CATALOG) as f:
            user_tier = json.load(f)
        for mid, entry in user_tier.items():
            base = catalog.get(mid, {})
            merged = dict(base)
            for key in ("maxTokens", "budgets", "thinking", "coding",
                        "nonThinking", "offParams", "effortMap"):
                if key in entry:
                    merged[key] = entry[key]
            if "noThinkSuffix" in entry:
                merged["noThinkSuffix"] = entry["noThinkSuffix"]
            catalog[mid] = merged
    return catalog


def default_models(catalog: dict) -> list:
    """Catalogued models that support thinking (have offParams or budgets)."""
    return [m for m in sorted(catalog)
            if "offParams" in catalog[m] or "budgets" in catalog[m]]


# ─── Version stamping (for reproducible reports) ─────────────────────────────

def _git_rev(path: Path) -> str:
    try:
        out = subprocess.run(["git", "-C", str(path), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or "?"
    except Exception:
        return "?"


def stack_versions(server: str, api_key: str = "", model: str | None = None) -> dict:
    """pi / plugin / server versions — stamped into every report."""
    v = {"captured_at": time.strftime("%Y-%m-%d %H:%M:%S"), "server": server}
    try:
        out = subprocess.run(["pi", "--version"], capture_output=True, text=True, timeout=15)
        v["pi"] = (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr).strip() else "?"
    except Exception:
        v["pi"] = "?"
    plugin_root = Path.home() / "workspace" / "lemonade-pi-plugin"
    v["plugin"] = f"{_git_rev(plugin_root)} ({plugin_root.name})" if plugin_root.exists() else "?"
    # server build from a tiny completion's system_fingerprint (b10818-...)
    try:
        body_dict = {"messages": [{"role": "user", "content": "hi"}],
                     "max_completion_tokens": 8,
                     "enable_thinking": False}
        if model:
            body_dict["model"] = model
        body = json.dumps(body_dict).encode()
        req = urllib.request.Request(server.rstrip("/") + "/v1/chat/completions", data=body,
                                     headers={"Authorization": f"Bearer {api_key}",
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = json.loads(resp.read())
        v["server_build"] = raw.get("system_fingerprint", "?")
    except Exception as e:
        v["server_build"] = f"unreachable ({e})"
    return v


# ─── Wire payload construction (mirrors lib/payload-tuning.ts) ────────────────

def build_wire_payload(prompt: str, model: str, level: str, entry: dict | None):
    """Build the wire payload exactly as the plugin's tuneModelPayload would.

    Returns {"skip": True, "reason": ...} when the model template rejects
    this level (effortMap has no entry and it is not a pass-through level),
    or {"skip": False, **payload}.
    """
    messages = [{"role": "user", "content": prompt}]
    max_tokens = (entry or {}).get("maxTokens", 16384)

    if level == "off":
        # P5: offParams fill-missing → enable_thinking:false (Qwen entries).
        # NOTE: max_completion_tokens is REQUIRED — without it the server
        # default cap truncates reasoning into content and OFF looks like
        # thinking is still ON (2026-09-05 artifact).
        payload = {
            "model": model,
            "messages": messages,
            "max_completion_tokens": max_tokens,
        }
        off_params = (entry or {}).get("offParams") or {}
        for k, val in off_params.items():
            if k not in payload:
                payload[k] = val
        # P3: nonThinking sampling row (fill-missing)
        row = (entry or {}).get("nonThinking") or {}
        for k, val in row.items():
            if k not in payload:
                payload[k] = val
        return {"skip": False, **payload}

    # ── thinking ON ──
    effort_map = (entry or {}).get("effortMap")
    if effort_map is not None and level not in effort_map:
        supported = sorted(effort_map.keys())
        return {"skip": True,
                "reason": f"Level '{level}' rejected by {model} template (effortMap). Supported: {supported}"}

    budget_level = BUDGET_LEVEL_CLAMP.get(level, level)
    budgets = (entry or {}).get("budgets") or {}
    desired = budgets.get(budget_level)
    if desired is None:
        return {"skip": True, "reason": f"No catalog budget for '{level}' on {model}"}

    # P2 re-clamp: min(desired, max_completion_tokens − MIN_ANSWER_TOKENS)
    budget = min(desired, max(0, max_tokens - MIN_ANSWER_TOKENS))

    payload = {
        "model": model,
        "messages": messages,
        "max_completion_tokens": max_tokens,
        "thinking_budget_tokens": budget,
        "reasoning_effort": (effort_map or {}).get(level, level),
    }
    # P3: thinking sampling row (fill-missing)
    row = (entry or {}).get("thinking") or {}
    for k, val in row.items():
        if k not in payload:
            payload[k] = val
    return {"skip": False, **payload}


def api_call(model: str, payload: dict, server: str, api_key: str, timeout: int = 600) -> dict:
    """Send a wire payload to the API and return structured results."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{server}/v1/chat/completions",
        data=data,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed_ms = round((time.time() - t0) * 1000)
            raw = json.loads(resp.read())

            msg = raw["choices"][0]["message"]
            rc = msg.get("reasoning_content", "") or ""
            c = msg.get("content", "")
            u = raw.get("usage", {})

            return {
                "success": True,
                "elapsed_ms": elapsed_ms,
                "model": raw.get("model", model),
                "finish_reason": raw["choices"][0].get("finish_reason"),
                "reasoning_content": rc,
                "answer": c,
                "total_tokens": u.get("total_tokens", 0),
                "completion_tokens": u.get("completion_tokens", 0),
                "prompt_tokens": u.get("prompt_tokens", 0),
            }
    except Exception as e:
        err_msg = str(e)
        if '400' in err_msg or 'reasoning_effort' in err_msg.lower():
            return {
                "success": False,
                "elapsed_ms": round((time.time() - t0) * 1000),
                "error": f"Template rejection: {err_msg[:200]}",
                "rejection_type": "template",
            }
        return {
            "success": False,
            "elapsed_ms": round((time.time() - t0) * 1000),
            "error": str(e),
        }


# ─── Prompts & scoring ────────────────────────────────────────────────────────

PROMPTS = {
    ("math_easy", "easy",
     "A gardener plants 4 rows of 6 tomato plants, then adds 3 more rows of 6 each. How many total?"),
    ("logic_easy", "easy",
     "All dogs can swim. Max is a dog. Can Max swim? Answer yes or no."),

    ("math_med", "medium",
     "Solve: number * 3 + 5 = 320 - (number * 4). What is x? Show the equation."),
    ("logic_med", "medium",
     "Alice Bob Charlie each have number 1 2 or 3. Alice not largest/smallest. Bob > Charlie. Charlie != 3."),

    ("math_hard", "hard",
     "A farmer has sheep. If he sells 140, half as many left as if he didn't sell any. Then buys 2 more. How many?"),
    ("causal_med", "medium",
     "It is 95F outside. You leave a scoop of vanilla ice cream on the sidewalk for 30 minutes. What happens and why?"),
}

ANSWER_KEYWORDS = {
    "math_easy":   ["42"],
    "logic_easy":  ["yes"],
    "math_med":    ["120", "=120", "= 120", "x = 120", "x=120"],
    "logic_med":   ["alice", "bob", "charlie"],
    "math_hard":   ["142"],
    "causal_med":  ["melt", "melting"],
}


def score_answer(prompt_key: str, result: dict) -> dict:
    """Score the response for correctness and quality dimensions."""
    scoring = {
        "valid_response": False,
        "has_reasoning": False,
        "correct_answer": False,
        "overthinking_risk": False,
    }

    if not result["success"]:
        return scoring

    c = (result.get("answer", "") or "") + "\n" + (result.get("reasoning_content", "") or "")
    rc = result.get("reasoning_content", "") or ""

    scoring["valid_response"] = len(c.strip()) > 10
    # 0 chars = truly no thinking (empty-think-block fix PR #22507)
    scoring["has_reasoning"] = len(rc) > 30

    # Correct answer: keyword match on merged content, guarded against
    # substring false-positives ("142" inside "1420"): the keyword must sit
    # in a non-digit context — flanked by non-digits on at least one side.
    keywords = ANSWER_KEYWORDS.get(prompt_key, [])
    if keywords:
        content_lower = c.lower()

        def kw_hit(kw: str) -> bool:
            k = kw.lower()
            for m in re.finditer(re.escape(k), content_lower):
                before = content_lower[m.start() - 1] if m.start() > 0 else ""
                after = content_lower[m.end()] if m.end() < len(content_lower) else ""
                if before.isdigit() or after.isdigit():
                    continue
                return True
            return False

        scoring["correct_answer"] = any(kw_hit(kw) for kw in keywords)

    _, difficulty = prompt_key.rsplit("_", 1)
    if difficulty == "easy" and len(rc) > 500:
        scoring["overthinking_risk"] = True

    return scoring


# ─── Wire-fidelity assertions (the point of this benchmark) ──────────────────

def wire_fidelity_checks(model: str, level: str, entry: dict | None, payload: dict,
                         result: dict) -> list:
    """Assert every plugin-tuned param actually reached the backend and had
    its expected effect. Returns [(label, ok, detail), ...]."""
    checks = []

    if level == "off":
        # P5: offParams must be on the wire...
        et = payload.get("enable_thinking")
        checks.append(("P5 enable_thinking:false on wire",
                       et is False, f"payload enable_thinking={et!r}"))
        # ...and the server must honor it: zero reasoning.
        rc_len = len(result.get("reasoning_content", "") or "")
        checks.append(("P5 zero reasoning at off",
                       result["success"] and rc_len == 0,
                       f"reasoning_chars={rc_len}"))
        # P3: nonThinking row applied (fill-missing)
        nt = (entry or {}).get("nonThinking") or {}
        if nt.get("presence_penalty") is not None:
            ok = payload.get("presence_penalty") == nt["presence_penalty"]
            checks.append(("P3 nonThinking sampling row",
                           ok, f"presence_penalty={payload.get('presence_penalty')} "
                               f"(expected {nt['presence_penalty']})"))
        return checks

    if not result["success"]:
        return checks

    # P2: budget = catalog value re-clamped to ceiling − 1024
    max_tokens = payload.get("max_completion_tokens") or (entry or {}).get("maxTokens", 16384)
    budget_level = BUDGET_LEVEL_CLAMP.get(level, level)
    desired = ((entry or {}).get("budgets") or {}).get(budget_level)
    if desired is not None:
        expected = min(desired, max(0, max_tokens - MIN_ANSWER_TOKENS))
        sent = payload.get("thinking_budget_tokens")
        checks.append(("P2 budget on wire (clamped)",
                       sent == expected, f"sent={sent} expected={expected}"))

    # effortMap: wire effort must be the mapped value
    emap = (entry or {}).get("effortMap")
    if emap and level in emap:
        sent_effort = payload.get("reasoning_effort")
        checks.append(("effortMap applied",
                       sent_effort == emap[level],
                       f"sent={sent_effort!r} expected={emap[level]!r}"))

    # P3: thinking sampling row (fill-missing)
    th = (entry or {}).get("thinking") or {}
    if th.get("temperature") is not None:
        ok = payload.get("temperature") == th["temperature"]
        checks.append(("P3 thinking sampling row",
                       ok, f"temperature={payload.get('temperature')} (expected {th['temperature']})"))

    # Server honored the budget: reasoning must exist and stay finite.
    rc_len = len(result.get("reasoning_content", "") or "")
    checks.append(("server emitted reasoning (thinking ON)",
                   rc_len > 0, f"reasoning_chars={rc_len}"))
    return checks


# ─── Main benchmark runner ────────────────────────────────────────────────────

def run_benchmark(models, levels, runs, server, api_key, mode="full", report_path=None):
    catalog = load_catalog()
    versions = stack_versions(server, api_key, models[0] if models else None)

    print("=" * 85)
    print("THINKING LEVEL INTEGRATION BENCHMARK")
    print(f"Server: {server} (build: {versions.get('server_build', '?')})")
    print(f"pi: {versions.get('pi', '?')} | plugin: {versions.get('plugin', '?')}")
    print(f"Models: {', '.join(models)}")
    print(f"Levels: {', '.join(levels)}")
    print(f"Runs per config: {runs}")
    uncatalogued = [m for m in models if m not in catalog]
    if uncatalogued:
        print(f"⚠ NOT IN CATALOG (default pi behavior, no tuning asserted): {', '.join(uncatalogued)}")
    print("=" * 85)

    for model in models:
        entry = catalog.get(model)
        emap = (entry or {}).get("effortMap")
        if emap is not None:
            skip_levels = [l for l in levels if l != "off" and l not in emap]
            if skip_levels:
                print(f"\n⚠ {model}: skipping levels {skip_levels} (template rejects)")

    all_results = []
    fidelity = []  # [(model, level, label, ok, detail), ...]
    total_configs = len(models) * len(levels)
    single_mode = (mode == "single")

    print(f"\nConfigs to run: {total_configs} × {runs} runs × {len(PROMPTS)} prompts")
    print("Running sequentially...\n")

    for model in models:
        entry = catalog.get(model)
        if not single_mode:
            print(f"\n{'=' * 80}\n▶ MODEL: {model}\n{'=' * 80}")

        for level in levels:
            print(f"\n  ▶ Level: {level.upper()}")

            for run_idx in range(1, runs + 1):
                for prompt_key, difficulty, prompt_text in PROMPTS:
                    built = build_wire_payload(prompt_text, model, level, entry)

                    if built.get("skip"):
                        print(f"    ⏭ SKIP {prompt_key}: {built['reason']}")
                        all_results.append({
                            "model": model, "level": level, "run": run_idx,
                            "prompt_key": prompt_key, "difficulty": difficulty,
                            "success": False, "reasoning_chars": 0,
                            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                            "elapsed_ms": 0, "error": built["reason"],
                            "template_rejection": True,
                        })
                        continue

                    api_payload = {k: v for k, v in built.items() if k != "skip"}
                    api_result = api_call(model, api_payload, server, api_key)

                    # Wire-fidelity checks (once per model×level, on the first run)
                    if run_idx == 1 and not any(f[0] == model and f[1] == level for f in fidelity):
                        for label, ok, detail in wire_fidelity_checks(model, level, entry,
                                                                      api_payload, api_result):
                            fidelity.append((model, level, label, ok, detail))

                    if not api_result["success"]:
                        rejection = api_result.get("rejection_type") == "template"
                        print(f"    ✗ FAIL {prompt_key}: {api_result['error'][:60]}{' (template)' if rejection else ''}")
                        record = {
                            "model": model, "level": level, "run": run_idx,
                            "prompt_key": prompt_key, "difficulty": difficulty,
                            "success": False, "reasoning_chars": 0,
                            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
                            "elapsed_ms": api_result["elapsed_ms"],
                            "error": api_result["error"], "template_rejection": rejection,
                        }
                    else:
                        scoring = score_answer(prompt_key, api_result)
                        rc = api_result.get("reasoning_content", "") or ""
                        record = {
                            "model": model, "level": level, "run": run_idx,
                            "prompt_key": prompt_key, "difficulty": difficulty,
                            "success": True, **scoring,
                            "elapsed_ms": api_result["elapsed_ms"],
                            "finish_reason": api_result.get("finish_reason"),
                            "total_tokens": api_result.get("total_tokens", 0),
                            "completion_tokens": api_result.get("completion_tokens", 0),
                            "prompt_tokens": api_result.get("prompt_tokens", 0),
                            "reasoning_chars": len(rc),
                            "template_rejection": False,
                        }

                    all_results.append(record)

                    if single_mode:
                        ts = time.strftime("%Y%m%d-%H%M%S")
                        sf = f"/tmp/thinking_bench_{model.replace('-', '_')}_{level}_{ts}.json"
                        with open(sf, "w") as f:
                            json.dump({"versions": versions, "records": all_results}, f, indent=2)

                    status = "OK" if scoring["correct_answer"] else ("?" if scoring["valid_response"] else "NO")
                    rc_display = f"{record['reasoning_chars']:5d}r" if record['reasoning_chars'] > 0 else "     0r"
                    print(f"    [{status}] {prompt_key} → {rc_display} {record['elapsed_ms']}ms")

                time.sleep(2)

            print(f"\n  ✓ Completed level {level.upper()}")

        timestamp = time.strftime("%Y%m%d-%H%M%S")
        model_file = f"/tmp/thinking_bench_{model.replace('-', '_')}_{timestamp}.json"
        with open(model_file, "w") as f:
            json.dump({"versions": versions, "records": all_results}, f, indent=2)
        print(f"  Saved {len(all_results)} records to {model_file}")

    # ─── Summary ──────────────────────────────────────────────────────────
    summaries = {}
    for model in models:
        summaries[model] = {}
        for level in levels:
            results = [r for r in all_results
                       if r["model"] == model and r["level"] == level and r["success"]]
            if not results:
                summaries[model][level] = {"n": 0, "correct": 0, "has_reasoning": False,
                                           "avg_tokens": 0, "avg_prompt_tokens": 0,
                                           "avg_completion_tokens": 0, "avg_time_s": 0,
                                           "avg_reasoning_chars": 0}
                continue
            summaries[model][level] = {
                "n": len(results),
                "correct": sum(1 for r in results if r["correct_answer"]),
                "has_reasoning": sum(1 for r in results if r["has_reasoning"]) == len(results),
                "avg_tokens": mean([r["total_tokens"] for r in results]),
                "avg_prompt_tokens": mean([r["prompt_tokens"] for r in results]),
                "avg_completion_tokens": mean([r["completion_tokens"] for r in results]),
                "avg_time_s": mean([r["elapsed_ms"] for r in results]) / 1000,
                "avg_reasoning_chars": mean([r["reasoning_chars"] for r in results]),
            }

    print(f"\n{'=' * 85}\nSUMMARY BY MODEL × LEVEL\n{'=' * 85}")
    print(f"\n{'Model':<30} {'Level':<8} {'Correct':<9} {'Reason?':<7} "
          f"{'AvgTok':>6} {'AvgTime':>7} {'AvgRChars':>10}")
    print("-" * 85)
    for model in models:
        for level in levels:
            s = summaries[model][level]
            if s["n"] == 0:
                print(f"{model:<30} {level:<8} {'—':<9}")
                continue
            correct_pct = round(100 * s["correct"] / max(s["n"], 1), 0)
            rc_str = "YES" if s["has_reasoning"] else "NO "
            print(f"{model:<30} {level:<8} {s['correct']:>2d}/{s['n']:<4} ({correct_pct:>3.0f}%) "
                  f"{rc_str:<5} {s['avg_tokens']:>6.0f} {s['avg_time_s']:>6.1f}s "
                  f"{s['avg_reasoning_chars']:>9.0f}")

    # ─── Wire-fidelity report ─────────────────────────────────────────────
    print(f"\n{'=' * 85}\nWIRE FIDELITY — plugin params → backend (P2/P3/P5/effortMap)\n{'=' * 85}")
    fail_count = 0
    for model, level, label, ok, detail in fidelity:
        mark = "✅" if ok else "❌"
        if not ok:
            fail_count += 1
        print(f"  {mark} [{model}] {level:<8} {label}: {detail}")
    fid_ok = len(fidelity) - fail_count
    print(f"\n  Wire fidelity: {fid_ok}/{len(fidelity)} checks passed")

    # ─── Save results ─────────────────────────────────────────────────────
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    output_file = f"/tmp/thinking_bench_{timestamp}.json"
    with open(output_file, "w") as f:
        json.dump({"versions": versions, "records": all_results,
                   "fidelity": [{"model": m, "level": l, "label": lb, "ok": ok, "detail": d}
                                for m, l, lb, ok, d in fidelity]}, f, indent=2)
    print(f"\nResults saved to {output_file}")

    success_count = sum(1 for r in all_results if r["success"])
    skip_count = sum(1 for r in all_results if not r["success"] and r.get("template_rejection"))
    fail_count = sum(1 for r in all_results if not r["success"] and not r.get("template_rejection"))
    print(f"Total records: {len(all_results)} ({success_count} successful, "
          f"{skip_count} template-skipped, {fail_count} failed)")

    # ─── Markdown report (reproducible per-model artifact) ────────────────
    if report_path:
        write_report(report_path, models, levels, summaries, fidelity, versions, all_results)
        print(f"Markdown report written to {report_path}")

    return all_results


def write_report(path: str, models, levels, summaries, fidelity, versions, all_results):
    """Version-stamped markdown report — regenerate after every major bump."""
    lines = []
    a = lines.append
    a("# Thinking Benchmark Report")
    a("")
    a(f"- **Captured:** {versions['captured_at']}")
    a(f"- **Server:** `{versions['server']}` (build: `{versions.get('server_build', '?')}`)")
    a(f"- **pi:** {versions.get('pi', '?')}  ")
    a(f"- **lemonade-pi-plugin:** {versions.get('plugin', '?')}")
    a("")
    a("## Summary by model × level")
    a("")
    a("| Model | Level | Correct | Reasoning? | Avg tokens | Avg time | Avg reasoning chars |")
    a("|---|---|---|---|---|---|---|")
    for model in models:
        for level in levels:
            s = summaries[model][level]
            if s["n"] == 0:
                a(f"| {model} | {level} | — | — | — | — | — |")
                continue
            pct = round(100 * s["correct"] / max(s["n"], 1), 0)
            rc = "yes" if s["has_reasoning"] else "no"
            a(f"| {model} | {level} | {s['correct']}/{s['n']} ({pct:.0f}%) | {rc} | "
              f"{s['avg_tokens']:.0f} | {s['avg_time_s']:.1f}s | {s['avg_reasoning_chars']:.0f} |")

    a("")
    a("## Wire fidelity (plugin params → backend)")
    a("")
    a("| Model | Level | Check | Result | Detail |")
    a("|---|---|---|---|---|")
    for model, level, label, ok, detail in fidelity:
        a(f"| {model} | {level} | {label} | {'✅' if ok else '❌'} | {detail} |")

    failed = [r for r in all_results if not r["success"] and not r.get("template_rejection")]
    if failed:
        a("")
        a("## Failures")
        a("")
        for r in failed[:20]:
            a(f"- {r['model']} @ {r['level']} — {r['prompt_key']}: {r.get('error', '?')[:120]}")

    a("")
    a("_Generated by `support/thinking-benchmark.py --report`. Re-run after every pi / "
      "plugin / server version bump and diff against the previous report._")
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Thinking Level Integration Benchmark — validates plugin → wire → server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Single cell (recommended for heavy benchmarks):
  python3 support/thinking-benchmark.py --models Qwen3.8-27B-GGUF --levels off

  # All levels for one model + markdown report:
  python3 support/thinking-benchmark.py --models Qwen3.8-27B-GGUF \\
      --levels off,low,medium,high --report /tmp/report-qwen3.8.md

  # Full matrix (all catalogued models × all levels):
  python3 support/thinking-benchmark.py
        """)

    parser.add_argument("--models", default=None,
                        help="Comma-separated model IDs (default: all catalogued thinking models)")
    parser.add_argument("--levels", default=",".join(TEST_LEVELS),
                        help="Comma-separated thinking levels (default: off,minimal,low,medium,high)")
    parser.add_argument("--runs", type=int, default=1, help="Runs per configuration (default: 1)")
    parser.add_argument("--server", default=DEFAULT_SERVER, help="Lemonade server URL")
    parser.add_argument("--api-key", default=DEFAULT_API_KEY, help="API key for the server")
    parser.add_argument("--report", default=None,
                        help="Write a version-stamped markdown report to this path")
    parser.add_argument("--mode", choices=["full", "single"], default=None,
        help="single = exactly one model × one level (subagent harness mode; "
             "default: single when 1×1 is given, else full)")

    args = parser.parse_args()

    catalog = load_catalog()
    models = ([m.strip() for m in args.models.split(",")] if args.models
              else default_models(catalog))
    levels = [l.strip() for l in args.levels.split(",")]

    mode = args.mode or ("single" if len(models) == 1 and len(levels) == 1 else "full")
    if mode == "single" and (len(models) != 1 or len(levels) != 1):
        print("ERROR: --mode single requires exactly one --models and one --levels entry.")
        sys.exit(1)

    api_key = args.api_key or os.environ.get("LEMONADE_API_KEY", "")
    if not api_key:
        # fall back to the configured auth.json (same as validate/serverhealth)
        try:
            import json as _json
            from pathlib import Path as _P
            auth = _json.loads((_P.home() / ".pi" / "agent" / "auth.json").read_text())
            entry = (auth.get("lemonade") or (auth.get("providers") or {}).get("lemonade")
                     or (auth.get("oauth") or {}).get("lemonade") or {})
            creds = _json.loads(entry.get("refresh") or "{}")
            api_key = creds.get("apiKey") or entry.get("access") or ""
        except Exception:
            pass
    if not api_key:
        print("ERROR: No API key provided. Set --api-key, LEMONADE_API_KEY, "
              "or configure auth.json.")
        sys.exit(1)

    run_benchmark(models, levels, args.runs, args.server, api_key, mode=mode,
                  report_path=args.report)


if __name__ == "__main__":
    main()

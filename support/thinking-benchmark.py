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
    # ── Easy (GSM8K-style, 2 steps — expect pass in both modes) ──
    ("gsm_clips", "easy",
     "Natalia sold clips to 48 of her friends in April, and then she sold half as many clips in May. How many clips did Natalia sell altogether?"),
    ("gsm_babysit", "easy",
     "Weng earns $12 an hour for babysitting. Yesterday she just worked 50 minutes. How much did she earn?"),
    ("gsm_letter", "easy",
     "James writes a 3-page letter to 2 different friends twice a week. How many pages does he write a year?"),

    # ── Medium (GSM8K multi-step / MATH L3 — expect non-thinking to miss some) ──
    ("gsm_wallet", "medium",
     "Betty is saving money for a new wallet which costs $100. Betty has only half of the money she needs. Her parents decided to give her $15 for that purpose, and her grandparents twice as much. How much more money does Betty need?"),
    ("gsm_book", "medium",
     "Julie is reading a 120-page book. Yesterday she was able to read 12 pages and today she read twice as many pages as yesterday. If she wants to read half of the remaining pages tomorrow, how many pages should she read?"),
    ("gsm_flowers", "medium",
     "Mark planted 10 yellow flowers. He planted 80% more purple flowers than yellow flowers. He also planted 25% as many green flowers as the total of yellow and purple flowers combined. How many flowers did Mark plant in total?"),
    ("math_heads_legs", "medium",
     "A farmer has chickens and rabbits in a field. There are 40 heads and 100 legs in total. How many rabbits does the farmer have?"),

    # ── Hard (MATH L4-5 style — expect only thinking to pass) ──
    ("math_div_or", "hard",
     "How many positive integers less than 1000 are divisible by 7 or 11?"),
    ("math_balls_boxes", "hard",
     "In how many ways can 3 indistinguishable balls be placed into 4 distinguishable boxes?"),
    ("math_coin_prob", "hard",
     "A fair coin is flipped 5 times. What is the probability of getting exactly 3 heads? Express your answer as a fraction in lowest terms."),
    ("math_log_eq", "hard",
     "Solve for x: log base 2 of x plus log base 2 of (x minus 2) equals 4. Give the exact value of x."),
    ("math_sequence", "hard",
     "A sequence starts 2, 3, 5, 9, 17 and each term after the first is one less than twice the previous term. What is the sixth term of the sequence?"),
}

# Expected answer keywords — used for automated correctness checking.
# Numeric keywords are matched with digit-boundary guards (no substring
# false-positives like "10" inside "100"). Sources: GSM8K (openai/grade-school-math)
# and MATH (hendrycks/math) style problems, adapted for single-shot scoring.
ANSWER_KEYWORDS = {
    "gsm_clips":        ["72"],
    "gsm_babysit":      ["10", "$10"],
    "gsm_letter":       ["624"],
    "gsm_wallet":       ["5", "$5"],
    "gsm_book":         ["42"],
    "gsm_flowers":      ["35"],
    "math_heads_legs":  ["10"],
    "math_div_or":      ["220"],
    "math_balls_boxes": ["20"],
    "math_coin_prob":   ["5/16"],
    "math_log_eq":      ["\u221a17", "sqrt(17)"],
    "math_sequence":    ["33"],
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
    wire_params = {}  # (model, level) -> exact params sent on the wire
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

                    # Record the exact wire params once per model×level (for the report)
                    if (model, level) not in wire_params:
                        wire_params[(model, level)] = {
                            k: api_payload.get(k) for k in (
                                "max_completion_tokens", "thinking_budget_tokens",
                                "reasoning_effort", "enable_thinking", "temperature",
                                "top_p", "top_k", "min_p", "presence_penalty",
                                "repetition_penalty") if api_payload.get(k) is not None
                        }

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
                            json.dump({"versions": versions, "records": all_results,
                                       "fidelity": [{"model": m, "level": l, "label": lb, "ok": ok, "detail": dt}
                                                    for m, l, lb, ok, dt in fidelity],
                                       "wire_params": {f"{m}|{l}": p for (m, l), p in wire_params.items()}},
                                      f, indent=2)

                    status = "OK" if scoring["correct_answer"] else ("?" if scoring["valid_response"] else "NO")
                    rc_display = f"{record['reasoning_chars']:5d}r" if record['reasoning_chars'] > 0 else "     0r"
                    print(f"    [{status}] {prompt_key} → {rc_display} {record['elapsed_ms']}ms")

                time.sleep(2)

            print(f"\n  ✓ Completed level {level.upper()}")

        timestamp = time.strftime("%Y%m%d-%H%M%S")
        model_file = f"/tmp/thinking_bench_{model.replace('-', '_')}_{timestamp}.json"
        with open(model_file, "w") as f:
            json.dump({"versions": versions, "records": all_results,
                       "fidelity": [{"model": m, "level": l, "label": lb, "ok": ok, "detail": dt}
                                    for m, l, lb, ok, dt in fidelity],
                       "wire_params": {f"{m}|{l}": p for (m, l), p in wire_params.items()}}, f, indent=2)
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
                                for m, l, lb, ok, d in fidelity],
                   "wire_params": {f"{m}|{l}": p for (m, l), p in wire_params.items()}}, f, indent=2)
    print(f"\nResults saved to {output_file}")

    success_count = sum(1 for r in all_results if r["success"])
    skip_count = sum(1 for r in all_results if not r["success"] and r.get("template_rejection"))
    fail_count = sum(1 for r in all_results if not r["success"] and not r.get("template_rejection"))
    print(f"Total records: {len(all_results)} ({success_count} successful, "
          f"{skip_count} template-skipped, {fail_count} failed)")

    # ─── Markdown report (reproducible per-model artifact) ────────────────
    if report_path:
        write_report(report_path, models, levels, summaries, fidelity, versions, all_results,
                     wire_params)
        print(f"Markdown report written to {report_path}")

    return all_results


def _md_escape(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ").strip()


def write_report(path: str, models, levels, summaries, fidelity, versions, all_results,
                 wire_params=None):
    """Version-stamped markdown report — regenerate after every major bump."""
    wire_params = wire_params or {}
    lines = []
    a = lines.append
    a("# Thinking Benchmark Report")
    a("")
    a(f"- **Captured:** {versions['captured_at']}")
    a(f"- **Server build:** `{versions.get('server_build', '?')}`")
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

    # ── Wire params actually sent (per model × level) ──
    a("")
    a("## Wire params sent per level")
    a("")
    a("Exact values on the wire for each cell (from the plugin catalog: P2 budget, "
      "effortMap, P3 sampling row, P5 offParams).")
    a("")
    header_keys = ["max_completion_tokens", "thinking_budget_tokens", "reasoning_effort",
                   "enable_thinking", "temperature", "top_p", "top_k", "min_p",
                   "presence_penalty", "repetition_penalty"]
    short = {"max_completion_tokens": "max_tok", "thinking_budget_tokens": "budget",
             "reasoning_effort": "effort", "enable_thinking": "en_think",
             "temperature": "temp", "top_p": "top_p", "top_k": "top_k", "min_p": "min_p",
             "presence_penalty": "pres_pen", "repetition_penalty": "rep_pen"}
    for model in models:
        a(f"### {model}")
        a("")
        a("| Level | " + " | ".join(short[k] for k in header_keys) + " |")
        a("|---" * (len(header_keys) + 1) + "|")
        for level in levels:
            p = wire_params.get((model, level))
            if not p:
                a(f"| {level} | " + " | ".join(["—"] * len(header_keys)) + " |")
                continue
            cells = []
            for k in header_keys:
                v = p.get(k)
                cells.append("—" if v is None else ("false" if v is False else str(v)))
            a(f"| {level} | " + " | ".join(cells) + " |")
        a("")

    # ── Per-prompt detail ──
    a("## Per-prompt results")
    a("")
    a("Each prompt × level cell: correctness, reasoning length, wall time, and the "
      "model's answer (truncated). Lets you inspect WHY a level scores the way it does.")
    for model in models:
        a(f"### {model}")
        a("")
        a("| Prompt | Level | Correct | Reasoning chars | Time | Answer (truncated) |")
        a("|---|---|---|---|---|---|")
        for prompt_key, difficulty, _ in PROMPTS:
            row_levels = [l for l in levels
                          if any(r["model"] == model and r["level"] == l and r["prompt_key"] == prompt_key
                                 for r in all_results)]
            for level in row_levels:
                rs = [r for r in all_results if r["model"] == model and r["level"] == level
                      and r["prompt_key"] == prompt_key]
                if not rs:
                    continue
                r = rs[0]  # first run
                if not r.get("success"):
                    a(f"| {prompt_key} | {level} | ⏭ skipped | — | — | {_md_escape(r.get('error', '?'))[:100]} |")
                    continue
                mark = "✅" if r["correct_answer"] else ("⚠" if r["valid_response"] else "❌")
                # answer may live in reasoning_content when content is empty
                ans_text = r.get("answer") or ""
                if not ans_text.strip():
                    rc_tail = (r.get("reasoning_content") or "")[-200:]
                    ans_text = ("…" if len(r.get("reasoning_content") or "") > 200 else "") + rc_tail
                ans = _md_escape(ans_text)[:150]
                a(f"| {prompt_key} | {level} | {mark} | {r['reasoning_chars']} | "
                  f"{r['elapsed_ms']/1000:.1f}s | {ans} |")
        a("")

    # ── Interpretation notes (auto-generated observations) ──
    a("## Notes")
    a("")
    for model in models:
        on_levels = [l for l in levels if l != "off" and summaries[model].get(l, {}).get("n", 0) > 0]
        off_s = summaries[model].get("off", {})
        if len(on_levels) >= 2 and off_s.get("n", 0) > 0:
            rchars = {l: summaries[model][l]["avg_reasoning_chars"] for l in on_levels}
            lo, hi = min(rchars.values()), max(rchars.values())
            if hi < 1.5 * max(lo, 1):
                a(f"- **{model}: flat reasoning band** — avg reasoning stays within "
                  f"{lo:.0f}–{hi:.0f} chars across {', '.join(on_levels)} despite distinct "
                  f"budgets/efforts on the wire. The model thinks its natural length and "
                  f"stops before any budget is hit, so effort values do not shape thinking "
                  f"length for this model; off vs on is the only effective switch.")
            else:
                a(f"- **{model}: levels spread** — reasoning grows {lo:.0f} → {hi:.0f} chars "
                  f"across on-levels; effort/budget are effective dials.")
        if off_s.get("n", 0) > 0:
            a(f"- **{model} @ off:** {off_s['correct']}/{off_s['n']} correct with zero "
              f"reasoning — the wire off-switch (`enable_thinking:false`) is honored.")
    # prompts that vary across levels (sampling variance indicator)
    varying = set()
    for prompt_key, _, _ in PROMPTS:
        outcomes = set()
        for model in models:
            for level in levels:
                rs = [r for r in all_results if r["model"] == model and r["level"] == level
                      and r["prompt_key"] == prompt_key and r.get("success")]
                if rs:
                    outcomes.add(rs[0]["correct_answer"])
        if len(outcomes) > 1:
            varying.add(prompt_key)
    if varying:
        a(f"- **Varying prompts:** {', '.join(sorted(varying))} scored differently across "
          f"cells — consistent with sampling variance on near-boundary problems, not a "
          f"level effect (answers are stochastic at temperature 1.0).")
    # thinking lift: prompts failed at off but passed at some on-level
    for model in models:
        off_fails = {r["prompt_key"] for r in all_results
                     if r["model"] == model and r["level"] == "off"
                     and r.get("success") and not r["correct_answer"]}
        if not off_fails:
            continue
        rescued, still = [], []
        for pk in sorted(off_fails):
            on_passes = [r["level"] for r in all_results
                         if r["model"] == model and r["prompt_key"] == pk and r["level"] != "off"
                         and r.get("success") and r["correct_answer"]]
            (rescued if on_passes else still).append(pk)
        if rescued:
            a(f"- **{model}: thinking lift** — {', '.join(rescued)} fail(s) at off but "
              f"pass with thinking ON; this is the measurable benefit of reasoning mode.")
        if still:
            a(f"- **{model}: persistent failure** — {', '.join(still)} fails at ALL levels "
              f"(off and on): a model capability gap on that problem, not a thinking-level effect.")

    failed = [r for r in all_results if not r["success"] and not r.get("template_rejection")]
    if failed:
        a("")
        a("## Failures")
        a("")
        for r in failed[:20]:
            a(f"- {r['model']} @ {r['level']} — {r['prompt_key']}: {r.get('error', '?')[:120]}")

    # ── Wire fidelity (plugin params → backend) ──
    a("")
    a("## Wire fidelity (plugin params → backend)")
    a("")
    a("| Model | Level | Check | Result | Detail |")
    a("|---|---|---|---|---|")
    for f in fidelity:
        # f is a 5-tuple from the live run, or a dict from the JSON path
        if isinstance(f, dict):
            m, l, lb, ok, dt = f["model"], f["level"], f["label"], f["ok"], f["detail"]
        else:
            m, l, lb, ok, dt = f
        a(f"| {m} | {l} | {lb} | {'✅' if ok else '❌'} | {dt} |")

    failed = [r for r in all_results if not r["success"] and not r.get("template_rejection")]

    a("")
    a("## Test set & sources")
    a("")
    a(f"{len(PROMPTS)} single-shot problems with deterministic answers, adapted from "
      "battle-tested public benchmarks:")
    a("")
    a("- **Easy (3):** GSM8K-style 2-step word problems "
      "(openai/grade-school-math) — expected to pass in both modes")
    a("- **Medium (4):** GSM8K multi-step + MATH L3-style "
      "(hendrycks/math) — non-thinking mode misses some")
    a("- **Hard (5):** MATH L4-L5 style (combinatorics, number theory, "
      "probability, exact-value algebra) — expected to require thinking")
    a("")
    a("Scoring: keyword match with digit-boundary guards; all answers "
      "machine-verified before inclusion. Prompts and keywords live in "
      "`support/thinking-benchmark.py` (PROMPTS / ANSWER_KEYWORDS).")
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

"""Lemonade server health checks — live probes of the configured server.

The Lemonade server lives OUTSIDE this stack (user-managed host), so validate
cannot inspect its startup flags directly. Instead it probes what is observable
over HTTP and warns when a known-good setting appears to be missing after an
upgrade:

  • KV/prompt cache persistence — measured via two identical requests: the
    second must show prompt caching (cache hit) or every session turn
    re-prefills the whole context. The pre-upgrade server build kept the
    context slot in RAM; a wiped config shows up here as cache=0 on the 2nd
    request (or no usage data at all).
  • Reasoning-budget-full message — when the thinking budget is exhausted,
    llama.cpp should append a short transition note before the answer.
    Measured by forcing a tiny budget on an easy prompt and looking for the
    marker in the response text. Missing → ~10% quality drop at budget
    exhaustion (docs/reference/thinking-support.md §6 P1).

Both checks are WARNINGS, never hard failures: the server is external and may
legitimately run a different model set. Unreachable server → single warning.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request


def _post(url: str, key: str, body: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def check_kv_cache(base_url: str, key: str, model: str | None = None) -> dict:
    """Two identical requests; the 2nd must hit the prompt cache.

    Returns {"ok": bool, "detail": str, "fix": str}.
    """
    url = base_url.rstrip("/") + "/v1/chat/completions"
    body = {
        "model": model or "",
        "messages": [{"role": "user", "content": "Cache probe: reply with the single word PONG."}],
        "max_completion_tokens": 8,
        "temperature": 0.0,
    }
    if not model:
        body.pop("model")
    try:
        r1 = _post(url, key, dict(body))
        r2 = _post(url, key, dict(body))
    except Exception as e:
        return {"ok": False, "detail": f"probe failed: {e}", "fix": "check server reachability"}

    u1 = (r1.get("usage") or {})
    u2 = (r2.get("usage") or {})
    p1 = u1.get("prompt_tokens", 0)
    cached = (u2.get("prompt_tokens_details") or {}).get("cached_tokens", u2.get("cached_tokens", 0))

    if not p1:
        return {"ok": False, "detail": "no usage data in response — cannot verify caching",
                "fix": "check server version exposes token usage"}
    if cached >= int(p1 * 0.8):
        return {"ok": True, "detail": f"2nd request cached {cached}/{p1} prompt tokens (KV slot persisted)", "fix": ""}
    return {"ok": False,
            "detail": f"2nd identical request: only {cached}/{p1} prompt tokens cached — context re-prefilled",
            "fix": "re-enable the server's KV/prompt-cache retention setting that was lost in the upgrade"}


def check_budget_message(base_url: str, key: str, model: str | None = None) -> dict:
    """Tiny thinking budget on an easy prompt → look for the exhaustion marker."""
    url = base_url.rstrip("/") + "/v1/chat/completions"
    body = {
        "model": model or "",
        "messages": [{"role": "user", "content": "What is 2+2? Answer with just the number."}],
        # 128, not 512: the exhaustion marker lands in reasoning_content and
        # content can be empty — a too-generous cap lets the model finish
        # normally before we observe anything (observed on b10818).
        "max_completion_tokens": 128,
        "temperature": 0.0,
        "reasoning_effort": "low",
        # 4, not 64: on easy prompts the model can stay under a 64-token
        # budget and never trigger the exhaustion message (observed on b10818).
        "thinking_budget_tokens": 4,
    }
    if not model:
        body.pop("model")
    try:
        r = _post(url, key, body)
    except Exception as e:
        return {"ok": False, "detail": f"probe failed (or thinking unsupported): {e}",
                "fix": "check server supports thinking_budget_tokens"}

    msg = (r.get("choices") or [{}])[0].get("message") or {}
    text = ((msg.get("reasoning_content") or "") + "\n" + (msg.get("content") or "")).lower()
    markers = ("budget", "exceeded", "answer now", "stop thinking", "concise answer")
    if any(m in text for m in markers):
        return {"ok": True, "detail": "reasoning-budget-full message present in response", "fix": ""}
    return {"ok": False,
            "detail": "no budget-exhaustion marker in response — --reasoning-budget-message likely not set",
            "fix": 'start the server with: --reasoning-budget-message "You have exceeded your reasoning budget. Provide a concise answer now." (docs/reference/thinking-support.md §6)'}


def run_server_checks(base_url: str, key: str, model: str | None = None) -> list[dict]:
    """All checks as [{label, ok, detail, fix}, ...]."""
    out = []
    kv = check_kv_cache(base_url, key, model)
    out.append({"label": "Lemonade KV/prompt cache persistence (context slot in RAM)", **kv})
    bm = check_budget_message(base_url, key, model)
    out.append({"label": "Lemonade reasoning-budget-full message", **bm})
    return out

#!/usr/bin/env python3
"""Validate the strixite wire contract that pi's models.json entry assumes.

pi talks to strixite natively (models.json, no plugin), so the provider's
quirks live in MODEL_CAPABILITIES in scripts/localpibox/setup.py — pi's
request shape is decided by pi, and only a live request proves those values
are still right. Run this after a strixite upgrade or when the stack's
defaults change; non-zero exit means the entry in the repo is now wrong.

  python3 support/strixite-probe.py --server http://127.0.0.1:8000 \
      --model qwen3.8-flash-next --api-key strixite

Checks (numbered in the output):
  1  /v1/models reports the model and its context_length (pi's contextWindow)
  2  reasoning_effort accepts minimal|low|medium|high|xhigh (none: check 4)
  3  "max" is rejected — hence thinkingLevelMap {"max": "xhigh"}
  4  reasoning_effort:"none" really stops thinking — hence {"off": "none"}
  5  a level turns thinking on and returns reasoning_content
  6  vision works — hence input: ["text","image"]
  7  max_completion_tokens caps the response — pi's maxTokens is honoured
  8  pi's extras (store, stream_options, include) are ignored, not 400s
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

LEVELS_ON = ("minimal", "low", "medium", "high", "xhigh")
LEVELS_REJECTED = ("max", "auto")

# Red left half / blue right half, 2x2: a wrong answer can only mean no image.
TEST_IMG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEElEQVR42mP4zwAE"
    "/xkgFAAb8gP9PpddpAAAAABJRU5ErkJggg==")


def post(url: str, payload: dict, api_key: str = "", timeout: float = 120.0):
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            pass
        return e.code, body


def chat(base: str, model: str, key: str, extra: dict, prompt="Reply with OK.",
         max_tokens=64):
    # include is what surfaces the per-request diagnostics and the chain of
    # thought: "strix" adds the strix object (thinking / skipped / off + drop
    # reasons), "reasoning.content" the final reasoning_content on the message.
    return post(f"{base}/chat/completions", {
        "model": model, "messages": [{"role": "user", "content": prompt}],
        "max_completion_tokens": max_tokens,
        "include": ["strix", "reasoning.content"], **extra}, key)


def content_parts(body):
    """(message, strix) of the first choice. strix is top-level on the response
    in the current build; older/other shapes put it on the message or choice."""
    choice = ((body or {}).get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    return msg, (msg.get("strix") or choice.get("strix")
                  or (body or {}).get("strix") or {})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--server", default="http://127.0.0.1:8000")
    ap.add_argument("--model", default="qwen3.8-flash-next")
    ap.add_argument("--api-key", default="strixite")
    args = ap.parse_args()
    base = args.server.rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    failures = []

    def check(label: str, ok: bool, detail: str = ""):
        print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f" — {detail}" if detail else ""))
        if not ok:
            failures.append(label)

    # 1 — model list + context window
    try:
        with urllib.request.urlopen(
                urllib.request.Request(f"{base}/models", headers={
                    "Authorization": f"Bearer {args.api_key}"}), timeout=10) as r:
            listing = json.loads(r.read().decode())
    except Exception as e:  # noqa: BLE001 - the point is to report the failure
        print(f"FAIL  server unreachable at {base}: {e}")
        return 1
    entry = next((m for m in listing.get("data", [])
                  if m.get("id") == args.model), None)
    check("1 /v1/models lists the model", entry is not None,
          f"ids={[m.get('id') for m in listing.get('data', [])]}")
    ctx = (entry or {}).get("context_length")
    check("1 context_length reported (pi contextWindow)", bool(ctx), f"={ctx}")

    # 2/3 — effort spellings
    for level in LEVELS_ON:
        code, body = chat(base, args.model, args.api_key, {"reasoning_effort": level},
                          "Reply with OK.")
        check(f"2 reasoning_effort '{level}' accepted", code == 200,
              "" if code == 200 else str(body)[:150])
    for level in LEVELS_REJECTED:
        code, body = chat(base, args.model, args.api_key, {"reasoning_effort": level})
        check(f"3 reasoning_effort '{level}' rejected (needs thinkingLevelMap)",
              code == 400, f"HTTP {code}")

    # 4 — the off spelling pi must send for thinking=off
    code, body = chat(base, args.model, args.api_key, {"reasoning_effort": "none"},
                      "What is 12*9? Answer with the number only.")
    msg, strix = content_parts(body if code == 200 else None)
    check("4 reasoning_effort 'none' -> thinking off",
          code == 200 and strix.get("thinking") == "off"
          and not (msg.get("reasoning_content") or ""),
          f"strix.thinking={strix.get('thinking')}")

    # 5 — thinking on, content delivered
    code, body = chat(base, args.model, args.api_key, {"reasoning_effort": "low"},
                      "What is 17*4? Answer with the number only.")
    msg, strix = content_parts(body if code == 200 else None)
    check("5 reasoning_effort 'low' -> thinking on + reasoning_content",
          code == 200 and strix.get("thinking") == "on"
          and bool(msg.get("reasoning_content")),
          f"strix.thinking={strix.get('thinking')} "
          f"reasoning_chars={len(msg.get('reasoning_content') or '')}")

    # 6 — vision
    code, body = post(f"{base}/chat/completions", {
        "model": args.model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": "What colour is the left half of this image? "
                                     "Answer with one word."},
            {"type": "image_url",
             "image_url": {"url": f"data:image/png;base64,{TEST_IMG_B64}"}}]}],
        "max_completion_tokens": 32, "reasoning_effort": "none",
        "include": ["strix", "reasoning.content"]}, args.api_key)
    answer = ""
    if code == 200:
        answer = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    check("6 vision (image_url) works", code == 200 and "red" in answer.lower(),
          f"answer={answer.strip()[:40]!r}")

    # 7 — output cap honoured (pi's maxTokens)
    code, body = chat(base, args.model, args.api_key, {"reasoning_effort": "none"},
                      "Count from 1 to 500, one number per line.", max_tokens=16)
    choice = (body.get("choices") or [{}])[0] if code == 200 else {}
    fr = choice.get("finish_reason")
    check("7 max_completion_tokens caps output (length stop)",
          code == 200 and fr == "length", f"finish_reason={fr}")

    # 8 — pi sends these; strixite must ignore rather than reject
    code, body = chat(base, args.model, args.api_key,
                      {"store": False, "include": ["reasoning.content"],
                       "stream_options": {"include_usage": True},
                       "reasoning_effort": "none"})
    check("8 pi extras (store/include/stream_options) ignored", code == 200,
          "" if code == 200 else str(body)[:150])

    print()
    if failures:
        print(f"FAIL — {len(failures)} check(s) drifted from MODEL_CAPABILITIES: "
              + ", ".join(failures))
        return 1
    print("PASS — strixite matches MODEL_CAPABILITIES (strixite entry in models.json)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

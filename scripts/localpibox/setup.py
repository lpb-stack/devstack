"""Canonical LocalPibox initial setup wizard + doctor (all launch modes).

One setup flow for every mode (cli, shell, ssh, web). The wizard configures
AND validates the installation before first use:

  [1] config repo      — clone/fetch the config repo into the agent dir
  [2] lemonade server  — URL, validated with a live HTTP probe
  [3] api key          — validated by fetching the model list (auth-aware)
  [4] default model    — picked from the server's live model list
  [5] lpb-memory       — full memory wizard (mode, transport, model, limits)
  [6] MCP servers      — per-server enable/disable toggles (mcp.json)
  [7] persist + verify — auth.json, settings.json, lpb-memory-config.json

Principles:
  - Every step shows its live result (✓ or ✗ with the raw error and the
    remedy). Nothing is persisted until it validates.
  - Interactive steps loop on failure: the offending input is re-offered
    with the error visible. Entering 'a' continues anyway (explicitly
    flagged in the summary); 'q' aborts (nothing written).
  - Non-interactive: the first failure aborts with a precise error +
    remedy, and nothing is written.
  - ``doctor()`` is the read-only checklist (``lpb doctor``,
    ``lpb-config check``); ``quick_status()`` is the one-line check the
    launcher uses before/after container start.

Consumed by:
  - lpb (host launcher)      — ``lpb setup``, ``lpb doctor``, and the
                               first-boot preflight that gates every fresh
                               container start
  - lpb-config (host/container) — ``lpb-config setup``,
                               ``lpb-config memory setup``, ``lpb-config check``
  - start.sh (in-container)  — non-interactive fallback for boots where
                               the host wizard did not run
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .log import Console
from .run import run_cmd

# ─── Constants ───────────────────────────────────────────────────────────────

DEFAULT_REMOTE = "https://github.com/lpb-stack/config.git"
DEFAULT_BASE_URL = "http://127.0.0.1:13305"
DEFAULT_API_KEY = "lemonade"
_CREDS_TTL_MS = 24 * 60 * 60 * 1000  # matches the plugin's CREDS_TTL_MS

# Built-in lpb-memory defaults (used only when the config repo template is
# unavailable, e.g. offline first boot). Keys mirror the config repo's
# lpb-memory-config.json.template.
_MEMORY_DEFAULTS: dict = {
    "memoryMode": "legacy-inject",
    "memoryPolicyStyle": "compact",
    "reviewTransport": "subprocess",
    "memoryCharLimit": 3000,
    "userCharLimit": 3000,
    "failureInjectionMaxEntries": 3,
    # Fallback only — the template/config value wins (setdefault below).
    # Subprocess LLM tasks (review/consolidation/flush) are mechanical;
    # thinking slows them down and risks long NPU occupation.
    "llmThinkingOverride": "off",
}

_ABORT_TOKENS = ("q", "quit", "abort")
_ANYWAY_TOKENS = ("a", "anyway", "skip")

# Friendly metadata for the MCP step (name → (description, api-key note)).
# Servers not listed here are offered with a "custom server" label.
_MCP_SERVER_INFO: dict = {
    "exa": ("web search + content fetch", "needs EXA_API_KEY (devstack .env: LPB_EXA_API_KEY)"),
    "agent-browser": ("browser automation (navigate, click, fill, screenshots)", ""),
    "chrome-devtools": ("Chrome DevTools diagnostics (off by default)", ""),
    "context7-mcp": ("up-to-date library docs (Context7)", "optional CONTEXT7_API_KEY raises rate limits"),
}


# ─── Results ─────────────────────────────────────────────────────────────────

@dataclass
class ProbeResult:
    """Outcome of a live lemonade server probe."""
    ok: bool
    reachable: bool        # any HTTP response (incl. 401) counts
    auth_failed: bool      # 401/403 — server is up, key rejected
    error: str = ""        # raw failure message ("" when ok)
    models: list = field(default_factory=list)   # [{id, name, ctx}]
    loaded: set = field(default_factory=set)     # model ids loaded per /health


@dataclass
class SetupResult:
    """Outcome of a wizard run."""
    ok: bool
    steps: list = field(default_factory=list)    # [(name, ok, detail)]
    warnings: list = field(default_factory=list)
    error: str = ""          # precise failure + remedy (non-interactive)
    aborted: bool = False    # user aborted — caller must not start
    base_url: str = ""
    api_key: str = ""
    model: str = ""

    def add(self, name: str, ok: bool, detail: str = "") -> None:
        self.steps.append((name, ok, detail))


# ─── URL / JSON / credentials helpers ────────────────────────────────────────

def bare_base_url(raw: str) -> str:
    """Normalize a server URL to bare http://host:port (strip /v1, /api/v1 …).

    Matches the plugin's buildBaseUrl(): the persisted form is always bare.
    """
    url = (raw or "").strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "http://" + url
    url = re.sub(r"/(api/)?v\d+/?$", "", url)
    return url.rstrip("/")


def server_name(base_url: str) -> str:
    return urllib.parse.urlparse(base_url).hostname or "Lemonade"


def load_json_file(path: Path) -> dict | None:
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def lemonade_creds(agent_dir: str | Path) -> dict | None:
    """Stored lemonade creds entry from auth.json (matches the plugin's
    readStoredPayload candidate order), or None."""
    data = load_json_file(Path(agent_dir) / "auth.json")
    if not data:
        return None
    for entry in (data.get("lemonade"),
                  (data.get("providers") or {}).get("lemonade"),
                  (data.get("oauth") or {}).get("lemonade")):
        if isinstance(entry, dict) and isinstance(entry.get("refresh"), str):
            return entry
    return None


def decode_creds(entry: dict) -> tuple[str, str]:
    """(baseUrl, apiKey) from a stored creds entry (refresh is a JSON string)."""
    try:
        payload = json.loads(entry.get("refresh") or "")
        base = payload.get("baseUrl") or ""
        key = payload.get("apiKey") or entry.get("access") or ""
    except (json.JSONDecodeError, AttributeError):
        base, key = "", entry.get("access") or ""
    return bare_base_url(base), key


def write_lemonade_auth(agent_dir: Path, base_url: str, api_key: str, cons: Console) -> bool:
    """Persist the lemonade provider (shape matches the plugin's encodeCreds)."""
    agent_dir = Path(agent_dir)
    auth = load_json_file(agent_dir / "auth.json") or {}
    auth["lemonade"] = {
        "type": "oauth",
        "refresh": json.dumps({"baseUrl": base_url, "apiKey": api_key,
                               "serverName": server_name(base_url)}),
        "access": api_key,
        "expires": int(time.time() * 1000) + _CREDS_TTL_MS,
    }
    try:
        agent_dir.mkdir(parents=True, exist_ok=True)
        path = agent_dir / "auth.json"
        path.write_text(json.dumps(auth, indent=2) + "\n")
        os.chmod(path, 0o600)
        cons.info(f"  ✓ auth.json — lemonade provider at {base_url}")
        return True
    except OSError as e:
        cons.error(f"  ✗ could not write {agent_dir / 'auth.json'}: {e}")
        return False


def write_default_model(agent_dir: Path, model: str, cons: Console) -> bool:
    """Set defaultProvider/defaultModel in settings.json (merge, keep user keys)."""
    agent_dir = Path(agent_dir)
    settings = load_json_file(agent_dir / "settings.json")
    if settings is None:
        cons.warn("  ✗ settings.json not found — default model NOT persisted")
        cons.warn("    (config repo templates unavailable — re-run 'lpb setup' once the repo is in place)")
        return False
    settings["defaultProvider"] = "lemonade"
    settings["defaultModel"] = model
    try:
        (agent_dir / "settings.json").write_text(json.dumps(settings, indent=2) + "\n")
        cons.info(f"  ✓ settings.json — default model lemonade/{model}")
        return True
    except OSError as e:
        cons.error(f"  ✗ could not write settings.json: {e}")
        return False


# ─── Stack version (template __LPB_VERSION__ substitution) ───────────────────

def resolve_stack_version() -> str:
    """Stack version for template substitution.

    LPB_VERSION env (baked into the image) first, then the VERSION file
    next to lpb.stack.env (repo checkout or host install), then a fallback.
    """
    v = os.environ.get("LPB_VERSION", "").strip()
    if v:
        return v
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2],                        # <devstack>/ (repo checkout)
        Path("/opt/devstack"),                  # Docker image
        Path.home() / ".lpb-stack" / "devstack",  # host install
    ]
    for cand in candidates:
        vf = cand / "VERSION"
        if vf.is_file():
            v = vf.read_text().strip()
            if v:
                return v
    return "0.0.0-lpb"


# ─── Config repo + templates ─────────────────────────────────────────────────

def ensure_config_repo(agent_dir: str | Path, remote: str, ref: str,
                       cons: Console) -> tuple[bool, str]:
    """Ensure the config repo at *agent_dir*. Returns (ok, detail).

    - missing, empty target   → git clone --depth=1
    - missing, non-empty target → git init in place + fetch + reset --hard
      (untracked runtime files survive — they are gitignored)
    - already a repo          → best-effort fetch (offline is OK)
    """
    agent_dir = Path(agent_dir)
    remote = remote or os.environ.get("CONFIG_REMOTE", "") or DEFAULT_REMOTE
    ref = ref or os.environ.get("CONFIG_REF", "") or "main"

    if (agent_dir / ".git").is_dir():
        out, err, code = run_cmd(["git", "-C", str(agent_dir), "fetch",
                                  "--depth=1", "origin", ref], timeout=60)
        head = run_cmd(["git", "-C", str(agent_dir), "rev-parse", "--short", "HEAD"])[0].strip()
        if code == 0:
            return True, f"present ({head[:8]}, fetched origin/{ref})"
        cons.warn(f"  config repo present ({head[:8]}) but fetch failed: "
                  f"{(err or out).strip()[:120]} — using local copy")
        return True, f"present ({head[:8]}, fetch failed — local copy used)"

    agent_dir.mkdir(parents=True, exist_ok=True)
    if any(agent_dir.iterdir()):
        # Non-empty, not a repo: stale runtime state — initialize in place.
        cons.info(f"  Config area not empty — initializing config repo in place...")
        run_cmd(["git", "-C", str(agent_dir), "init", "-q"], timeout=30)
        run_cmd(["git", "-C", str(agent_dir), "remote", "add", "origin", remote],
                timeout=30)
        out, err, code = run_cmd(["git", "-C", str(agent_dir), "fetch", "--depth=1",
                                  "origin", ref], timeout=300)
        if code != 0:
            return False, f"fetch failed: {(err or out).strip()[:200]}"
        # Tracked files land at repo root; untracked runtime state survives.
        run_cmd(["git", "-C", str(agent_dir), "reset", "-q", "--hard",
                 f"origin/{ref}"], timeout=60)
        return True, "initialized in place"

    cons.info(f"  Cloning config repo from {remote} ({ref})...")
    out, err, code = run_cmd(
        ["git", "clone", "--quiet", "--depth=1", "--branch", ref,
         remote, str(agent_dir)], timeout=300,
    )
    if code != 0:
        return False, f"clone failed: {(err or out).strip()[:200]}"
    return True, "cloned"


def render_templates(agent_dir: str | Path, cons: Console) -> bool:
    """Generate rendered runtime config files from templates (non-destructive:
    existing files are never overwritten). Returns True when all generated
    files exist afterwards."""
    agent_dir = Path(agent_dir)
    version = resolve_stack_version()
    ok = True
    for template_name, out_name in (
        ("settings.json.template", "settings.json"),
        ("lpb-memory-config.json.template", "lpb-memory-config.json"),
        ("mcp.json.template", "mcp.json"),
    ):
        template = agent_dir / template_name
        out = agent_dir / out_name
        if not template.is_file():
            cons.warn(f"  {template_name} not found — skipped ({out_name} will use extension defaults)")
            if not out.is_file():
                ok = False
            continue
        if out.is_file():
            cons.info(f"  ✓ {out_name} (already present)")
            continue
        try:
            text = template.read_text().replace("__LPB_VERSION__", version)
            data = json.loads(text)
            out.write_text(json.dumps(data, indent=2) + "\n")
            cons.info(f"  ✓ {out_name} (generated from template, stack {version})")
        except (OSError, json.JSONDecodeError) as e:
            cons.error(f"  ✗ could not render {out_name} from {template_name}: {e}")
            ok = False
    return ok


# ─── Server probe ────────────────────────────────────────────────────────────

def _http_get_json(url: str, timeout: float = 8.0, api_key: str = ""):
    """GET a JSON document (Bearer auth when a key is given). Raises on error."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return json.loads(res.read().decode())


def probe_server(base_url: str, api_key: str = "", timeout: float = 8.0) -> ProbeResult:
    """Live-probe a lemonade server.

    - any HTTP response (including 401/403) means REACHABLE;
    - 401/403 means AUTH FAILED (the key — or the lack of one — was rejected);
    - connection-level failures mean UNREACHABLE.
    On success, also fetches /api/v1/health (best effort) to mark loaded models.
    """
    base = bare_base_url(base_url)
    if not base:
        return ProbeResult(False, False, False, "no server URL given")

    try:
        data = _http_get_json(f"{base}/api/v1/models", timeout=timeout, api_key=api_key)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return ProbeResult(False, True, True,
                               f"authentication rejected (HTTP {e.code}) — the API key is wrong")
        return ProbeResult(False, True, False, f"unexpected response (HTTP {e.code})")
    except Exception as e:  # URLError, timeout, JSON decode…
        return ProbeResult(False, False, False, str(e).strip() or "connection failed")

    items = data.get("data") if isinstance(data, dict) else None
    models = [
        {"id": str(m.get("id", "")), "name": m.get("name"),
         "ctx": m.get("max_context_window")}
        for m in (items or []) if isinstance(m, dict) and m.get("id")
    ]
    loaded: set = set()
    try:
        health = _http_get_json(f"{base}/api/v1/health", timeout=min(timeout, 5.0),
                                api_key=api_key)
        if isinstance(health, dict):
            for bm in health.get("all_models_loaded") or []:
                if isinstance(bm, dict) and bm.get("model_name"):
                    loaded.add(bm["model_name"])
    except Exception:
        pass  # health is optional — only marks models as "loaded"
    return ProbeResult(True, True, False, "", models, loaded)


# ─── lpb-memory configuration (wizard step 5 / `lpb-config memory setup`) ────

def _ask_cons(cons: Console, prompt: str, default: str = "") -> str:
    """input() wrapper: returns the stripped answer ('' → *default*), and
    converts EOF/Ctrl-C into an AbortError."""
    try:
        val = input(prompt)
    except (EOFError, KeyboardInterrupt):
        raise AbortError
    val = val.strip()
    return val if val else default


def _ask_choice(cons: Console, question: str, options: list[tuple[str, str]],
                current: str, allow_abort: bool = True) -> str:
    """Numbered-choice prompt with validation; returns the chosen value."""
    cons.info(f"  {question}")
    while True:
        for i, (value, desc) in enumerate(options, 1):
            marker = "  ← current" if value == current else ""
            cons.info(f"    {i}. {value:<15} {desc}{marker}")
        default_idx = next((i for i, (v, _) in enumerate(options, 1)
                            if v == current), 1)
        ans = _ask_cons(cons, f"  Choice [Enter = {default_idx}]: ")
        if allow_abort and ans.lower() in _ABORT_TOKENS:
            raise AbortError
        if not ans:
            return options[default_idx - 1][0]
        if ans.isdigit() and 1 <= int(ans) <= len(options):
            return options[int(ans) - 1][0]
        cons.warn(f"  Invalid choice {ans!r} — enter {1}-{len(options)}, or 'q' to abort")


def _ask_int(cons: Console, prompt: str, default: int) -> int:
    """Integer prompt with validation (no more ValueError tracebacks)."""
    while True:
        ans = _ask_cons(cons, f"  {prompt} [Enter = {default}]: ")
        if not ans:
            return default
        if re.fullmatch(r"\d+", ans):
            return int(ans)
        cons.warn(f"  {ans!r} is not a number — enter a number or press Enter for {default} (or 'q' to abort)")


def configure_memory(agent_dir: str | Path, cons: Console, *,
                     interactive: bool, default_model: str = "",
                     step_label: str = "lpb-memory configuration") -> bool:
    """Configure lpb-memory (wizard step 5, also `lpb-config memory setup`).

    Interactive: full wizard (mode, transport, background model, limits),
    pre-filled from the CURRENT config (template when absent).
    Non-interactive: template/defaults + the default-model override.
    Returns True when the config file was written.
    """
    agent_dir = Path(agent_dir)
    out = agent_dir / "lpb-memory-config.json"
    template = agent_dir / "lpb-memory-config.json.template"

    existing = load_json_file(out)
    if existing:
        base = dict(_MEMORY_DEFAULTS)
        base.update(existing)          # current values win (pre-fill)
    elif template.is_file():
        base = dict(_MEMORY_DEFAULTS)
        base.update(load_json_file(template) or {})
    else:
        base = dict(_MEMORY_DEFAULTS)
        cons.warn("  lpb-memory-config.json.template not found — using built-in defaults")

    if not interactive:
        if default_model:
            base["llmModelOverride"] = default_model
        base.setdefault("llmThinkingOverride", "off")  # config/template wins
        try:
            agent_dir.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(base, indent=2) + "\n")
            cons.info(f"  ✓ lpb-memory-config.json (generated, main model: "
                      f"{default_model or 'no override'})")
            return True
        except OSError as e:
            cons.error(f"  ✗ could not write lpb-memory-config.json: {e}")
            return False

    cons.info(f"  {step_label}")
    try:
        mode = _ask_choice(
            cons, "Memory mode:",
            [("legacy-inject", "Inject memory into every prompt (~4KB, recommended)"),
             ("policy-only", "AI must search memory proactively (saves context)")],
            str(base.get("memoryMode", "legacy-inject")))
        base["memoryMode"] = mode
        if mode == "policy-only":
            base["memoryPolicyStyle"] = "compact"

        transport = _ask_choice(
            cons, "Review transport:",
            [("subprocess", "Offload to separate model (free main session, recommended)"),
             ("direct", "Use main model (faster, blocks main session)")],
            str(base.get("reviewTransport", "subprocess")))
        base["reviewTransport"] = transport

        model = _ask_cons(
            cons, f"  Model for background operations [Enter = {default_model or 'main model'}]: ")
        model = model or default_model
        if model:
            base["llmModelOverride"] = model
        else:
            base.pop("llmModelOverride", None)
        # Thinking level is a personal preference — config/template wins;
        # "off" is only the fallback (works with any review model).
        base.setdefault("llmThinkingOverride", "off")

        cons.info("  Context limits (press Enter for defaults):")
        base["memoryCharLimit"] = _ask_int(cons, "Memory entries", int(base.get("memoryCharLimit", 3000)))
        base["userCharLimit"] = _ask_int(cons, "User preferences", int(base.get("userCharLimit", 3000)))
        base["failureInjectionMaxEntries"] = _ask_int(
            cons, "Max failures", int(base.get("failureInjectionMaxEntries", 3)))
    except AbortError:
        raise

    try:
        agent_dir.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(base, indent=2) + "\n")
    except OSError as e:
        cons.error(f"  ✗ could not write lpb-memory-config.json: {e}")
        return False
    cons.info("  ✓ lpb-memory-config.json")
    return True


def configure_mcp(agent_dir: str | Path, cons: Console, *,
                  interactive: bool,
                  step_label: str = "MCP servers") -> bool:
    """Configure MCP servers (wizard step 6).

    Interactive: per-server enable/disable toggle (y / n / Enter = keep
    current), 'q' aborts. Non-interactive: keeps the rendered defaults
    (mcp.json must already exist — rendered from mcp.json.template in
    step [1]). The pi-mcp-adapter reads mcp.json at Pi startup; a server
    is disabled only when its "enabled" flag is explicitly false.

    Returns True when mcp.json is usable (defaults kept or toggles
    written); False when there is nothing to configure (no file — e.g.
    config repo unavailable) or the write failed.
    """
    agent_dir = Path(agent_dir)
    out = agent_dir / "mcp.json"
    cfg = load_json_file(out)
    if cfg is None:
        cons.warn(f"  {out.name} not found — MCP step skipped "
                  "(config repo template unavailable?)")
        return False
    servers = cfg.get("mcpServers")
    if not isinstance(servers, dict) or not servers:
        cons.warn(f"  {out.name} has no mcpServers — nothing to configure")
        return True

    def _summary() -> str:
        enabled = [n for n, s in servers.items()
                   if isinstance(s, dict) and s.get("enabled") is not False]
        disabled = [n for n, s in servers.items()
                    if isinstance(s, dict) and s.get("enabled") is False]
        line = f"{', '.join(enabled) or 'none'} enabled"
        if disabled:
            line += f" · {', '.join(disabled)} disabled"
        return line

    if not interactive:
        cons.info(f"  ✓ {out.name} — {_summary()} (template defaults)")
        return True

    cons.info(f"  {step_label} — enable the servers you use (Enter keeps current state)")
    for name, srv in servers.items():
        if not isinstance(srv, dict):
            continue
        desc, note = _MCP_SERVER_INFO.get(name, ("", ""))
        currently_on = srv.get("enabled") is not False
        label = desc or "custom server"
        cons.info(f"  {name:<16} {label}" + (f"  ({note})" if note else ""))
        target = currently_on
        while True:
            ans = _ask_cons(cons, f"    enable [y/n] [Enter = keep ({'enabled' if currently_on else 'disabled'}), q = abort]: ")
            a = ans.lower()
            if a in _ABORT_TOKENS:
                raise AbortError
            if not a:
                break
            if a in ("y", "yes"):
                target = True
                break
            if a in ("n", "no"):
                target = False
                break
            cons.warn(f"  Invalid answer {ans!r} — enter y, n, or press Enter (or 'q' to abort)")
        srv["enabled"] = target
    try:
        out.write_text(json.dumps(cfg, indent=2) + "\n")
    except OSError as e:
        cons.error(f"  ✗ could not write {out.name}: {e}")
        return False
    cons.info(f"  ✓ {out.name} — {_summary()}")
    return True


class AbortError(Exception):
    """User aborted the wizard (EOF/Ctrl-C or 'q')."""


# ─── The wizard ──────────────────────────────────────────────────────────────

def run_wizard(
    *,
    agent_dir: str | Path,
    cons: Console,
    interactive: bool,
    reconfigure: bool = False,
    default_base_url: str = "",
    default_api_key: str = "",
    config_remote: str = "",
    config_ref: str = "main",
    timeout: float = 8.0,
) -> SetupResult:
    """Run the initial setup wizard. See module docstring for the steps.

    Interactive: prompts loop on failure; 'a' = continue anyway (flagged),
    'q' = abort (nothing written, result.aborted). Non-interactive: first
    failure returns ok=False with a precise error, nothing written.
    """
    agent_dir = Path(agent_dir)
    result = SetupResult(ok=False)
    remote = config_remote or os.environ.get("CONFIG_REMOTE", "") or DEFAULT_REMOTE
    # Caller-provided defaults win; otherwise the env (same names the
    # container bridges to bare LEMONADE_*).
    base_env = os.environ.get("LEMONADE_BASE_URL", "") or os.environ.get("LPB_LEMONADE_BASE_URL", "")
    key_env = os.environ.get("LEMONADE_API_KEY", "") or os.environ.get("LPB_LEMONADE_API_KEY", "")

    cons.info("=" * 54)
    cons.info("  LocalPibox initial setup")
    cons.info("=" * 54)
    cons.info(f"  Agent dir:  {agent_dir}")
    if reconfigure:
        cons.info("  (reconfigure — current values shown as defaults)")
    cons.info("")

    def _fail(msg: str) -> SetupResult:
        result.error = msg
        return result

    try:
        # ── [1] Config repo ────────────────────────────────────────────────
        cons.info("[1] Config repo")
        repo_ok, detail = ensure_config_repo(agent_dir, remote, config_ref, cons)
        if repo_ok:
            cons.info(f"  ✓ config repo — {detail}")
        result.add("config repo", repo_ok, detail)
        rendered = False
        if repo_ok:
            rendered = render_templates(agent_dir, cons)
        if not repo_ok and interactive:
            c = _ask_cons(cons, "  (q) abort · (Enter) continue without config repo: )")
            if c.lower() in _ABORT_TOKENS:
                result.aborted = True
                return result
        if not repo_ok:
            cons.warn(f"  config repo unavailable — provider will be configured anyway: {detail}")

        # ── [2]+[3] Server URL + API key (validated together) ─────────────
        base = bare_base_url(default_base_url or base_env) or DEFAULT_BASE_URL
        key = (default_api_key or key_env or "").strip() or DEFAULT_API_KEY
        cons.info("[2] Lemonade server")
        models: list = []
        loaded: set = set()
        url_skipped = key_skipped = False
        phase = "url"
        while True:
            if phase == "url":
                if interactive:
                    val = _ask_cons(cons, f"  URL [Enter = {base}]  (a = start anyway, q = abort): ")
                    if val.lower() in _ABORT_TOKENS:
                        result.aborted = True
                        return result
                    if val.lower() in _ANYWAY_TOKENS:
                        url_skipped = True
                        key_skipped = True  # the key is never checked either
                        cons.warn("  Continuing without server validation")
                        break
                    base = bare_base_url(val or base)
                    if not base:
                        cons.warn("  Enter a URL like http://host:13305 (or 'a' to skip validation)")
                        continue
                cons.info(f"  Checking {base} …")
                probe = probe_server(base, "", timeout=timeout)
                if not probe.reachable:
                    cons.error(f"  ✗ unreachable: {probe.error}")
                    if not interactive:
                        return _fail(f"lemonade server unreachable at {base}: {probe.error} "
                                     f"(is it running? set LEMONADE_BASE_URL or re-run 'lpb setup')")
                    phase = "url"
                    continue
                note = " (key required)" if probe.auth_failed else ""
                cons.info(f"  ✓ reachable{note}")
                phase = "key"
            # phase == "key"
            if interactive:
                val = _ask_cons(cons, f"  API key [Enter = {key}]  (a = skip key check, q = abort): ")
                if val.lower() in _ABORT_TOKENS:
                    result.aborted = True
                    return result
                if val.lower() in _ANYWAY_TOKENS:
                    key_skipped = True
                    cons.warn("  Continuing without key validation")
                    break
                key = val or key
            probe = probe_server(base, key, timeout=timeout)
            if probe.ok:
                models, loaded = probe.models, probe.loaded
                if models:
                    cons.info(f"  ✓ key accepted — {len(models)} model(s) available")
                else:
                    cons.warn("  server reachable but reported no models (loaded yet?)")
                break
            if probe.auth_failed:
                cons.error(f"  ✗ {probe.error}")
                if not interactive:
                    return _fail(f"lemonade API key rejected at {base} (HTTP 401/403) "
                                 f"(set LEMONADE_API_KEY or re-run 'lpb setup')")
                continue
            if not probe.reachable:
                cons.error(f"  ✗ server no longer reachable: {probe.error}")
                if not interactive:
                    return _fail(f"lemonade server unreachable at {base}: {probe.error}")
                phase = "url"
                continue
            cons.error(f"  ✗ {probe.error}")
            if not interactive:
                return _fail(f"lemonade server probe failed at {base}: {probe.error}")

        result.add("lemonade server", not url_skipped,
                   f"{base} (validated)" if not url_skipped else f"{base} (NOT validated)")
        result.add("api key", not key_skipped,
                   "accepted" if not key_skipped else "NOT validated")

        # ── [4] Default model ──────────────────────────────────────────────
        cons.info("[4] Default model")
        chosen = ""
        if not models:
            cons.warn("  no models reported — default model not set")
            if interactive:
                c = _ask_cons(cons, "  (load a model, then re-run 'lpb setup'. Continue? [Y/n] 'q' aborts: )")
                if c.lower() in _ABORT_TOKENS:
                    result.aborted = True
                    return result
            result.add("default model", False, "no models available")
        else:
            default_idx = next((i for i, m in enumerate(models) if m["id"] in loaded), 0)
            if interactive:
                for i, m in enumerate(models, 1):
                    mark = "  (loaded)" if m["id"] in loaded else ""
                    name = f"  ({m['name']})" if m.get("name") and m["name"] != m["id"] else ""
                    cur = "  ← current" if m["id"] == (reconfigure and _current_model(agent_dir)) else ""
                    cons.info(f"    {i}. {m['id']}{name}{mark}{cur}")
                while True:
                    ans = _ask_cons(cons, f"  Pick [Enter = {default_idx + 1}]: ")
                    if ans.lower() in _ABORT_TOKENS:
                        result.aborted = True
                        return result
                    if not ans:
                        chosen = models[default_idx]["id"]
                        break
                    if ans.isdigit() and 1 <= int(ans) <= len(models):
                        chosen = models[int(ans) - 1]["id"]
                        break
                    cons.warn(f"  Enter a number 1-{len(models)} (or 'q' to abort)")
            else:
                chosen = models[default_idx]["id"]
                cons.info(f"  ✓ {chosen} (auto — loaded model preferred)")
            if chosen and chosen not in loaded:
                cons.warn(f"  note: {chosen} not reported as loaded by the server yet")
            result.add("default model", True, f"lemonade/{chosen}")

        # ── [5] lpb-memory ─────────────────────────────────────────────────
        if configure_memory(agent_dir, cons, interactive=interactive,
                            default_model=chosen,
                            step_label="[5] lpb-memory configuration"):
            result.add("lpb-memory", True, "configured")
        else:
            result.add("lpb-memory", False, "write failed")
            if not interactive:
                return _fail("could not write lpb-memory-config.json (permission?)")

        # ── [6] MCP servers ───────────────────────────────────────────────
        if configure_mcp(agent_dir, cons, interactive=interactive,
                         step_label="[6] MCP servers"):
            result.add("mcp", True, "configured")
        else:
            # MCP is optional — a missing mcp.json (e.g. offline boot without
            # the config repo) must not fail the wizard.
            result.add("mcp", False, "skipped (no mcp.json)")
            result.warnings.append(
                "mcp.json not found — MCP servers unavailable until the config "
                "repo is in place (re-run 'lpb setup' or 'lpb-config render')")

        # ── [7] Persist + verify ───────────────────────────────────────────
        cons.info("[7] Persisting configuration")
        persisted = write_lemonade_auth(agent_dir, base, key, cons)
        if chosen:
            persisted = write_default_model(agent_dir, chosen, cons) and persisted
        elif not url_skipped:
            cons.warn("  no default model chosen — settings.json untouched")

        if not (url_skipped or key_skipped):
            final = probe_server(base, key, timeout=min(timeout, 5.0))
            if final.ok:
                cons.info(f"  ✓ final check — server responding"
                          f"{f', {len(final.loaded)} model(s) loaded' if final.loaded else ''}")
            else:
                cons.warn(f"  final check failed: {final.error} (it responded during setup — re-run 'lpb setup' if this persists)")

        result.ok = True
        result.base_url = base
        result.api_key = key
        result.model = chosen
        if url_skipped:
            result.warnings.append("server was NOT validated — re-run 'lpb setup' once it is reachable")
        if key_skipped:
            result.warnings.append("API key was NOT validated")
        if not chosen:
            result.warnings.append("no default model set — settings.json has no defaultModel")
        if not persisted:
            result.warnings.append("not all configuration files were written")
        if not rendered:
            result.warnings.append("config repo templates not fully rendered — runtime config may use defaults")

        cons.info("=" * 54)
        if result.warnings:
            for w in result.warnings:
                cons.warn(f"  ⚠ {w}")
        cons.info(f"  Setup complete — lemonade provider "
                  f"{'configured' if persisted else 'partially configured'}")
        cons.info("=" * 54)
        return result
    except AbortError:
        result.aborted = True
        cons.warn("Setup aborted — nothing was written.")
        return result


def _current_model(agent_dir: Path) -> str:
    settings = load_json_file(agent_dir / "settings.json")
    return (settings or {}).get("defaultModel", "")


# ─── Quick status (launcher pre/post flight) ─────────────────────────────────

def quick_status(agent_dir: str | Path, timeout: float = 3.0) -> tuple[str, str]:
    """Cheap status for the launcher: ('ok'|'broken'|'missing', message)."""
    agent_dir = Path(agent_dir)
    creds = lemonade_creds(agent_dir)
    if creds is None:
        return "missing", "no model provider configured"
    base, key = decode_creds(creds)
    probe = probe_server(base, key, timeout=timeout)
    settings = load_json_file(agent_dir / "settings.json")
    model = (settings or {}).get("defaultModel", "")
    label = f"lemonade/{model}" if model else "lemonade (no default model set)"
    if probe.ok:
        extra = f" — {len(probe.models)} model(s) on server" if probe.models else ""
        return "ok", f"{label} @ {base}{extra}"
    if probe.auth_failed:
        return "broken", f"{label} @ {base} — API key rejected (run 'lpb setup')"
    return "broken", f"{label} @ {base} — server unreachable: {probe.error} (run 'lpb setup')"


# ─── Doctor (read-only checklist) ────────────────────────────────────────────

def doctor(agent_dir: str | Path, cons: Console, *, timeout: float = 5.0,
           final_line: bool = True) -> int:
    """Run the non-interactive health checklist. Returns 0 when all critical
    checks pass, 1 otherwise. Never writes anything.

    ``final_line=False`` suppresses the verdict (callers that append extra
    checks, like the launcher's container-runtime line, print their own)."""
    agent_dir = Path(agent_dir)
    cons.info("=" * 54)
    cons.info(f"  LocalPibox setup check — {agent_dir}")
    cons.info("=" * 54)
    critical_ok = True

    # [1] Config repo
    if (agent_dir / ".git").is_dir():
        head = run_cmd(["git", "-C", str(agent_dir), "rev-parse", "--short", "HEAD"])[0].strip()
        remote = run_cmd(["git", "-C", str(agent_dir), "remote", "get-url", "origin"])[0].strip()
        cons.info(f"  ✓ config repo      {head[:8]}  ({remote})")
    else:
        cons.error(f"  ✗ config repo      missing — run 'lpb setup' (clones it) or 'lpb-config update'")
        critical_ok = False

    # [2] settings.json
    settings = load_json_file(agent_dir / "settings.json")
    if settings:
        provider = settings.get("defaultProvider", "(none)")
        model = settings.get("defaultModel", "(none)")
        if provider and model != "(none)":
            cons.info(f"  ✓ settings.json    default lemonade/{model} (provider {provider})")
        else:
            cons.error("  ✗ settings.json    present but no default model — run 'lpb setup'")
            critical_ok = False
    else:
        cons.error("  ✗ settings.json    missing — run 'lpb setup' (or 'lpb-config render')")
        critical_ok = False

    # [3] lpb-memory config (non-critical)
    mem = load_json_file(agent_dir / "lpb-memory-config.json")
    if mem:
        cons.info(f"  ✓ lpb-memory       mode={mem.get('memoryMode', '?')}, "
                  f"transport={mem.get('reviewTransport', '?')}")
    else:
        cons.warn("  ⚠ lpb-memory       not configured — extension uses built-in defaults "
                  "(run 'lpb setup' or 'lpb-config memory setup')")

    # [4] MCP servers (non-critical)
    mcp = load_json_file(agent_dir / "mcp.json")
    mcp_servers = (mcp or {}).get("mcpServers") or {}
    if isinstance(mcp_servers, dict) and mcp_servers:
        enabled = [n for n, s in mcp_servers.items()
                   if isinstance(s, dict) and s.get("enabled") is not False]
        disabled = [n for n, s in mcp_servers.items()
                    if isinstance(s, dict) and s.get("enabled") is False]
        line = f"  ✓ mcp.json         {', '.join(enabled) or 'none'} enabled"
        if disabled:
            line += f" · {', '.join(disabled)} disabled"
        cons.info(line)
    else:
        cons.warn("  ⚠ mcp.json         not configured — MCP servers unavailable "
                  "(run 'lpb setup' or 'lpb-config render')")

    # [5] provider credentials
    creds = lemonade_creds(agent_dir)
    if creds is None:
        cons.error("  ✗ provider         no lemonade credentials — run 'lpb setup'")
        if final_line:
            cons.info("=" * 54)
            cons.error("  Setup has problems — see ✗ lines above.")
        return 1
    base, key = decode_creds(creds)
    cons.info(f"  ✓ provider         auth.json (server {base})")

    # [6] live probe
    probe = probe_server(base, key, timeout=timeout)
    model = (settings or {}).get("defaultModel", "")
    if probe.ok:
        loaded_note = f", {len(probe.loaded)} loaded" if probe.loaded else ""
        cons.info(f"  ✓ server           reachable — {len(probe.models)} model(s){loaded_note}")
        if model and probe.models and model not in {m["id"] for m in probe.models}:
            cons.warn(f"  ⚠ default model {model} not in the server's model list")
        elif model and model not in probe.loaded and probe.loaded:
            cons.warn(f"  ⚠ default model {model} not reported as loaded yet")
    elif probe.auth_failed:
        cons.error(f"  ✗ server           {probe.error} — run 'lpb setup' to fix the key")
        critical_ok = False
    else:
        cons.error(f"  ✗ server           unreachable: {probe.error} — run 'lpb setup' to change host/key")
        critical_ok = False

    cons.info("=" * 54)
    if final_line:
        if critical_ok:
            cons.done("  Setup healthy.")
        else:
            cons.error("  Setup has problems — see ✗ lines above.")
    return 0 if critical_ok else 1


__all__ = [
    "AbortError", "ProbeResult", "SetupResult",
    "DEFAULT_BASE_URL", "DEFAULT_API_KEY", "DEFAULT_REMOTE",
    "bare_base_url", "server_name", "load_json_file", "lemonade_creds",
    "decode_creds", "write_lemonade_auth", "write_default_model",
    "resolve_stack_version", "ensure_config_repo", "render_templates",
    "probe_server", "configure_memory", "configure_mcp", "run_wizard", "quick_status", "doctor",
]

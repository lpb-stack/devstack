# Browser Automation Setup (install-browser)

> Installed: `support/install-browser.py`  
> Purpose: Install Chrome-for-Testing + agent-browser MCP server  
> Platform: Container environment (Linux)

---

## What It Does

`install-browser` sets up browser automation for the LocalPibox stack by:

1. **Downloading Chrome-for-Testing** — fetches the latest stable Chrome from Google's official CDN
2. **Installing agent-browser** — runs `agent-browser install`, then `install --with-deps` (Playwright dependency resolver)
3. **Configuring container-safe defaults** — writes container-optimized Chrome launch args

### Why `install-browser` over `agent-browser install` alone

Running `agent-browser install --with-deps` directly downloads a system Chrome and handles deps, but:

- **Slow connections** — the built-in download has a hardcoded timeout that can fail on slow networks
- **Chrome-for-Testing CDN** — `install-browser` fetches from Google's official CDN (`googlechromelabs.github.io` → `storage.googleapis.com`) with explicit timeouts (30s for version fetch, no hard cap on the download itself)
- **Exec bit self-healing** — Python's `zipfile.extractall()` drops Unix exec bits; `install-browser` detects and restores them automatically (Chrome crashes at startup if `chrome_crashpad_handler` is not executable)
- **Container config merging** — writes `~/.agent-browser/config.json` with container-safe launch args, **merged** into any existing config (preserves user customizations)
- **Version tracking** — installs from the same version JSON API that Playwright uses, keeping Chrome in sync with agent-browser's expectations
- Visual testing of web apps
- Browser-based validation (login flows, form submission, UI testing)
- Capturing screenshots and accessibility audits
- Testing the pi.dev agent-browser integration

---

## Installation Process

### Step 1: Chrome Download

```
Downloads from: https://googlechromelabs.github.io/chrome-for-testing/
Target path:    /home/lpb/.agent-browser/browsers/chrome-<version>/
```

- Fetches the latest stable version from Google's version JSON API
- Extracts `chrome-linux64.zip` to the target directory
- Restores Unix exec bits (Python's `zipfile.extractall()` drops them)
- Self-heals existing installs if exec bits were lost

**Chrome executables tracked:**
- `chrome` — main binary
- `chrome_crashpad_handler` — crash reporter
- `headless_shell` — headless mode shell

### Step 2: agent-browser Install

```
Runs: agent-browser install → agent-browser install --with-deps
```

- Installs the `agent-browser` CLI globally (already installed via npm)
- Uses Playwright's dependency resolver to install only the exact libraries needed
- System deps are installed per the downloaded Chrome version

### Step 3: Container Config

```
Writes: ~/.agent-browser/config.json
```

Container-safe launch args are merged into the config:
```json
{
  "args": "--no-sandbox,--no-first-run,--disable-gpu,--disable-crashpad"
}
```

These args are required because:
- `--no-sandbox` — Chrome refuses to run as root without it
- `--no-first-run` — skips the first-run dialog
- `--disable-gpu` — no GPU in containers
- `--disable-crashpad` — crash reports can't be sent from containers

The config is **merged** (not replaced) — user customizations are preserved.

### Step 4: Verification

The script verifies:
- Chrome binary is present and executable
- `agent-browser` binary is present
- Chrome reports a valid version string
- agent-browser reports installed status

---

## Usage

`install-browser` is installed in the PATH (user-space, no root/sudo needed).

```bash
install-browser
```

### What's Installed

| Component | Path | Version Source |
|---|---|---|
| Chrome | `/home/lpb/.agent-browser/browsers/chrome-<ver>/chrome-linux64/chrome` | Google CDN |
| agent-browser | `/home/lpb/.npm-global/bin/agent-browser` | npm global install |
| Config | `~/.agent-browser/config.json` | Generated on first run |

### Source Files

| File | Purpose |
|---|---|
| `support/install-browser.py` | Python script — main logic |
| `~/.local/bin/install-browser` | User-space CLI wrapper (in PATH) |

### Self-Healing

If Chrome is extracted without exec bits (e.g., from a broken zip extraction), `install-browser` detects and restores them automatically. The following files are checked:
- `chrome`
- `chrome_crashpad_handler`
- `headless_shell`

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  install-browser.py                                                │
│                                                                     │
│  1. fetch_stable_chrome_version()                                  │
│     → GET https://googlechromelabs.github.io/chrome-for-testing/   │
│       last-known-good-versions-with-downloads.json                 │
│                                                                     │
│  2. install_chrome(cons)                                           │
│     → Download chrome-linux64.zip                                  │
│     → Extract with mode restoration                                │
│     → Self-heal exec bits if needed                                │
│                                                                     │
│  3. install_agent_browser(cons)                                    │
│     → Run agent-browser install                                    │
│     → Run agent-browser install --with-deps                        │
│     → Merge container-safe args into config.json                   │
│                                                                     │
│  4. verify_installation(cons)                                      │
│     → Check Chrome binary exists + executable                      │
│     → Run chrome --version                                         │
│     → Run agent-browser --version                                  │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Troubleshooting

### Chrome PermissionError (errno 13)

**Symptom:** Chrome crashes on startup with `PermissionError: [Errno 13] Permission denied`

**Cause:** Chrome binary lacks exec bit (Python's `zipfile.extractall()` drops Unix modes)

**Fix:** Re-run `install-browser` — it restores exec bits automatically:
```bash
install-browser
```

### Chrome Already Installed

The script is idempotent — if Chrome is already present and executable, it skips installation:
```
Chrome already installed at /home/lpb/.agent-browser/browsers/chrome-123.0.6275.0
```

### Missing System Dependencies

If `agent-browser install --with-deps` fails partially, Chrome may work but with reduced functionality. Check:
```bash
agent-browser install --with-deps
```

### Chrome Version Mismatch

If Chrome version doesn't match Playwright's expectations:
```bash
agent-browser install  # reinstalls Playwright browsers
agent-browser install --with-deps  # reinstalls system deps
```

---

## Current Status

| Component | Status | Version |
|---|---|---|
| Chrome | ✅ Installed | Latest stable (auto-updated) |
| agent-browser | ✅ Installed | Via npm global |
| Config | ✅ Generated | Container-safe args merged |
| MCP Server | ✅ Ready | `agent-browser` MCP available |

**Last verified:** 2026-08-25

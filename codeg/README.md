# codeg + goose-as-ACP (strixite) — host-side tools

Scripts for running [codeg](https://ghcr.io/spacering-net/codeg) on slaptop with
goose wired as a second runtime against the strixite server
(`http://192.168.0.13:8000`, model `qwen3.8-flash-next`).

## Background / root-cause log (2026-11-04)

codeg **auto-downloads and manages ACP agent binaries itself** — it fetched the
lean `goose-acp` build into the `codeg-data` volume:

```
/data/acp-binaries/goose-acp/1.57.0/linux-x86_64/goose-acp
```

It spawns that binary as a **child process inside the codeg container**
(root, `HOME=/root`) — `CODEG_RUNTIME=docker` / docker.sock is not involved in
this path. Consequence: the host's `~/.config/goose/config.yaml` is invisible
to it, and the plain `docker run` (no env, data volume only) gave goose nothing
→ the codeg UI error:

```
Failed to resolve provider and model: Configuration value not found: GOOSE_PROVIDER
```

The strix endpoint IS reachable from inside the codeg container (verified via
`/health`), so config visibility was the only gap. Fix: pass the `GOOSE_*` /
`OPENAI_*` values as container env (env beats `config.yaml` in goose's
resolution order; the API key must come from env anyway — goose ignores keys
placed in config.yaml, and strixite is unauthenticated on the LAN anyway, see
devstack#17).

## Files

| File | Purpose |
|---|---|
| `codeg.sh` | **The controller.** `start/stop/restart` lifecycle · `patch` catalog · `reload` = patch + respawn the goose agent · `recreate` = rebuild container with current `codeg.env` · `verify` / `status` / `logs`. |
| `codeg-start.sh` | Thin alias for `codeg.sh start` (old muscle memory). |
| `codeg.env.example` | Copy to `codeg.env` and edit — goose/strix settings in docker `--env-file` format. Changing it needs `codeg.sh recreate` (env is baked at container create). |
| `patch-goose-catalog.sh` | Alias for `codeg.sh patch`. Vision-capable catalog entry (`attachment:true` + image modalities, `reasoning:false` — the `reasoning:true` variant triggers the `developer`-role incompatibility, devstack#21). |
| `goose-codeg-diag.sh` | Read-only diagnostics (container topology, goose presence, endpoint reachability, `/data` state, logs). Auto-detects the codeg container by image. |

### Why "patched but still no vision" happens (all three are real)

1. The running `goose-acp` child **caches the catalog in memory at process start** —
   editing the file does nothing until the agent restarts. `codeg.sh reload` kills the
   child; codeg respawns it (or hit reconnect in the UI).
2. Container recreation wipes `/root/.local/share` — only `/data` is a volume. `codeg.sh`
   keeps a patched copy on the host (`codeg/.catalog-cache/`, git-ignored) and re-applies it.
3. Upstream models.dev changed its etag *during our testing* → goose **re-downloads** the
   catalog and clobbers local patches. `patch`/`reload` are idempotent; `verify` shows what
   the agent will actually see.

## "Invalid params: invalid directory path" — it is NOT the MCP companion (2026-11-04)

goose validates the **session `cwd`** on `session/new` (`validate_absolute_cwd`,
`goose/crates/goose/src/acp/server.rs:791`): it must be absolute **and exist inside the
codeg container**. Reproduced directly against goose-acp 1.57.0:

```
cwd=<existing dir>   -> session/new OK        (with stdio/http MCP companion attached too)
cwd=<missing dir>    -> -32602 Invalid params: "invalid directory path"
```

codeg's `tag_mcp_suspect` (`acp/connection.rs:1585`) appends the "turn off MCP support"
hint to **any** `session/new` failure whenever a companion is attached — a red herring;
leaving MCP support **on**. Disabling MCP just strips the hint and shows the raw error.

Why it appeared after our env changes: `codeg.sh recreate` rebuilds the container and
**everything outside the `/data` volume is reset to the image**, so a project dir that
lived in the old container's ephemeral fs vanishes; likewise host paths (`/home/lpb/…`)
never existed in-container. Fixes:

1. `./codeg.sh recreate` — now bind-mounts `$CODEG_MOUNT` (default `$HOME/workspace`) at
   the same in-container path, so host project paths validate and goose edits real repos.
2. Or re-point the project folder in the codeg UI to a dir that exists in-container
   (preferably under `/data/…`, the only persistent volume).
3. `./codeg.sh cwdcheck` — prints the cwds recent sessions requested and flags MISSING ones.

If your container is still the old unnamed one (`awesome_agnesi`), either run
`./codeg.sh recreate` once to get the named setup, or prefix commands with
`CODEG_NAME=awesome_agnesi`.

## Vision (2026-11-04 findings)

- strixite itself does vision: raw `image_url` request → correct answer, ~90 prompt tokens.
- goose treats the model as **text-only** (unknown in models.dev catalog) and silently drops
  images — the binary contains `[image omitted: model does not support vision]`. Same catalog
  gate as the thinking issue, but fixable without the devstack#21 blocker:
  run `./patch-goose-catalog.sh` (vision-only entry, `reasoning:false` keeps the safe role path).
- After the patch, goose+strix answers image questions end-to-end — **but** the qwen4exp engine
  intermittently (≈1 in 3 here) fails image runs with
  `forward_batch: Qwen4ExpSession::forward: run on seq 0 carries mRoPE position state`
  (devstack#22 — server-side race, retry succeeds).

## Usage (on slaptop)

```bash
cp codeg.env.example codeg.env    # optional — otherwise script defaults are used
./codeg.sh start                  # first run creates the container (replaces old unnamed one)
./codeg.sh status                 # container + goose children + what the catalog says
./codeg.sh patch                  # vision entry (waits for goose's first catalog fetch)
./codeg.sh reload                 # full reload: re-patch + respawn goose agent
./codeg.sh recreate               # after editing codeg.env — rebuilds with new env
./codeg.sh stop                   # docker stop
```

Typical "I changed something, make it live" flow:

```bash
./codeg.sh reload && ./codeg.sh verify     # catalog-level changes
./codeg.sh recreate                        # env-level changes
```

Verify:

```bash
docker exec codeg printenv | grep GOOSE
docker exec codeg /data/acp-binaries/goose-acp/1.57.0/linux-x86_64/goose-acp --version
```

Then in the codeg UI: reconnect the goose agent, send "reply ACP-OK".
If it fails: `docker exec codeg sh -c 'tail -50 /data/logs/codeg-server.*.log' | grep -i goose`

## Single source of truth (stack follow-up, devstack#18)

`lpb-config render` should additionally emit `~/.config/goose/goose.env`
(KEY=VALUE lines, same facts it already derives from `models.json`), so this
`codeg.env` can be a symlink/copy and switching models via `lpb-config set`
follows into codeg automatically. Then this start script becomes:

```bash
docker run ... --env-file ~/.config/goose/goose.env ghcr.io/spacering-net/codeg:latest
```

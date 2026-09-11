# Forking & Repointing

You can fork this stack, personalize it, and repoint it at your own managed
set of repositories (Pi core, config preset, and extensions) instead of the
LocalPibox originals.

## What each component maps to

| Component | URL / ref lives in | Effort | Repoint path |
|---|---|---|---|
| **Extensions** (lemonade-pi-plugin, lpb-memory, pi-subagents, …) | runtime config `~/.pi/agent/settings.json` → `packages` | 🟢 trivial, **no rebuild** | edit the `packages` array, or `pi install git:<fork>/<repo>@<ref>`; applied at next startup |
| **Config preset** (lpb-stack/config) | `lpb.stack.env` → `LPB_CONFIG_FORK` / `LPB_CONFIG_REF` | 🟡 one rebuild, or live at runtime | rebuild with `--build-arg CONFIG_FORK=...`, **or** repoint the clone in the running container (below) |
| **Pi core** (mainstream `earendil-works/pi`) | `lpb.stack.env` → `LPB_PI_VERSION` (npm version) | 🟡 one rebuild | set `LPB_PI_VERSION=<x.y.z>` in `lpb.stack.env` + both profiles, rebuild. A *forked/patched* pi is a deliberate re-introduction of the source build — see [Forking pi](#forking-pi-only-if-strictly-needed) |

## Full repoint procedure (image build)

1. Fork the repos you care about (e.g. `lpb-stack/config`).
2. Clone **this** repo (devstack) and edit `lpb.stack.env` at the root to
   point at your forks:

   ```sh
   export LPB_PI_VERSION=0.85.1            # mainstream pi from npm (pin a different release)
   export LPB_CONFIG_FORK=https://github.com/<you>/config.git
   export LPB_CONFIG_REF=main             # your branch
   export LPB_IMAGE_CLI=ghcr.io/<you>/devstack:dev-cli
   export LPB_IMAGE_WEB=ghcr.io/<you>/devstack:dev-web
   export LPB_CONTAINER_NAME=mybox        # avoid colliding with lpb-stack
   ```

   (Or pass them as `docker build --build-arg PI_VERSION=... --build-arg
   CONFIG_FORK=...` without editing the file.)
3. Build and push the image (locally, or via a fork of the GitHub Actions
   workflow, which reads `lpb.stack.env`).
4. Install and run `lpb` — it reads the same `lpb.stack.env` for
   image/container names, so it picks up your fork automatically.
5. To pin a specific image version persistently, export
   `LPB_IMAGE_TAG=0.0.1-lpb-dev` (a version tag on your fork's registry) —
   or let `lpb` resolve the latest for your pipeline.

## Repointing the config preset without a rebuild

The config preset is a git clone at `~/.pi/agent/` (container). After first
boot you can repoint it live — no image rebuild needed:

```sh
podman exec -it lpb-stack bash
cd ~/.pi/agent/
git remote set-url origin https://github.com/<you>/config.git
git pull --ff-only origin <your-branch>
```

`start.sh` only does a non-destructive `git fetch` on later boots (it never
wipes local changes — use `lpb-config update/reset/merge` for actual
updates). The clone *is* the live config directory, so pulled files are in
place at the next Pi start.

## Repointing extensions at runtime (no rebuild)

Extensions are not baked into the image; they are installed from
`settings.json#packages`. Repoint them from the running container:

```sh
podman exec -it lpb-stack pi remove git:github.com/lpb-stack/lemonade-pi-plugin
podman exec -it lpb-stack pi install git:github.com/<you>/lemonade-pi-plugin@<your-ref>
podman exec -it lpb-stack pi update --extensions
```

Changes apply at the **next pi startup** (or `/reload` in a running session
for config-only changes).

## Forking pi (only if strictly needed)

Since the 2026-08-31 de-fork, the image installs mainstream pi from the npm
registry (`@earendil-works/pi-coding-agent` at `LPB_PI_VERSION`) — the
`lpb-stack/pi` fork is retired (last state: tag `pre-defork-0.0.71`). Most
needs that the fork used to cover are handled upstream (Qwen/Llama.cpp
overflow detection) or by the lemonade-pi-plugin (per-level thinking budget
via `compat.thinkingTokenBudgetField`).

If a pi patch becomes strictly necessary, the source-build path must be
re-introduced deliberately:

1. Rebase a fork on the current mainstream tag you pin (see
   `patches/pi-case4-overflow.patch` for how the last fork patch is preserved).
2. Re-add `LPB_PI_FORK` / `LPB_PI_REF` build args to the Dockerfile and the
   CI workflow, and restore the fork-clone step that installed pi from
   `git clone` + `pnpm build` instead of `npm install -g`.
3. Manage `allow-scripts` for the fork's native addons via the `.npmrc`
   allow-scripts whitelist (the mainstream npm package does not ship
   `allowScripts`; devstack manages it independently).

Document any such change here and in `lpb.stack.env` comments.

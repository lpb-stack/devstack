# syntax=docker/dockerfile:1
# ═══════════════════════════════════════════════════════════════════════════
# LOCALPIBOX DEVSTACK — MULTI-STAGE DOCKERFILE
# ═══════════════════════════════════════════════════════════════════════════
# Two build targets:
#   cli  — Base dev environment with Pi CLI (interactive terminal)
#   web  — Extends cli + adds VSCodium server + web access
#
# Build:
#   docker build --target cli  -t ghcr.io/lpb-stack/devstack:cli  .
#   docker build --target web  -t ghcr.io/lpb-stack/devstack:web  .
#
# Pi configuration: lpb.stack.env (project root)
#   Sourced at build time to populate ARG defaults:
#   LPB_PI_VERSION, LPB_NODE_VERSION, LPB_VSCODIUM_VERSION
#   (pi installs from the npm registry at a pinned mainstream version —
#   no fork; see lpb.stack.env comments for the 2026-08-31 de-fork)
#
# NOTE: Runtime extensions (lemonade-pi-plugin, lpb-memory) are NOT
#       built into the image. They are installed at container startup by
#       `pi update --extensions`, which reads their branches from the
#       config repo served at ~/.pi/agent/settings.json → "packages" array.
# ═══════════════════════════════════════════════════════════════════════════

# Source fork configuration — ARG defaults come from lpb.stack.env
# Instead of editing this file, edit lpb.stack.env (canonical) and either:
#   - run support/build.sh (reads lpb.stack.env + lpb.conf.env), or
#   - pass --build-arg per value.
# NOTE: Docker ARG values can't reference source'd shell variables.
ARG NODE_VERSION=24
ARG VSCODIUM_VERSION=1.126.04524
ARG PI_VERSION=0.84.4
ARG PI_HEAD_SHA=unknown

# Config preset repo — baked so start.sh clones the fork's config at boot.
# (CI passes these from lpb.stack.env LPB_CONFIG_FORK / LPB_CONFIG_REF.)
ARG CONFIG_FORK=https://github.com/lpb-stack/config.git
ARG CONFIG_REF=main

# Runtime defaults baked from lpb.conf.env (start.sh can still override).
ARG LPB_MAX_TOKENS_CONTEXT_RATIO=0.06
ARG LPB_VERSION=unknown

# Provenance — set via --build-arg in CI, defaults to "unknown" for local builds
ARG IMAGE_REVISION=unknown
ARG IMAGE_BUILT=unknown

# ═══════════════════════════════════════════════════════════════════════════
# BASE STAGE — Common setup for both cli and web images
# ═══════════════════════════════════════════════════════════════════════════
FROM ubuntu:26.04 AS base

ARG NODE_VERSION
ARG VSCODIUM_VERSION
ARG PI_FORK
ARG PI_REF
ARG PI_HEAD_SHA
ARG CONFIG_FORK
ARG CONFIG_REF
ARG LPB_MAX_TOKENS_CONTEXT_RATIO
ARG LPB_VERSION
ARG IMAGE_REVISION
ARG IMAGE_BUILT

ENV DEBIAN_FRONTEND=noninteractive

# Context window / max tokens ratio — baked into image from lpb.conf.env.
# Container-level overrides take precedence via --env or -e at runtime.
ENV LPB_MAX_TOKENS_CONTEXT_RATIO=${LPB_MAX_TOKENS_CONTEXT_RATIO}
# Stack/version banner value (baked from lpb.conf.env).
ENV LPB_VERSION=${LPB_VERSION}
# Baked from lpb.stack.env/CI — start.sh reads these for the config repo clone.
ENV LPB_CONFIG_REMOTE=${CONFIG_FORK}
ENV LPB_CONFIG_REF=${CONFIG_REF}

# ── System packages ─────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential pkg-config \
    curl ca-certificates gnupg \
    git jq unzip rsync \
    python3 python3-pip python3-venv libsqlite3-dev \
    sudo openssh-server openssl \
    gh ripgrep fzf fd-find tmux \
    && rm -rf /var/lib/apt/lists/*

# ── Node.js ─────────────────────────────────────────────────────────────────
RUN curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g npm@latest \
    && corepack enable

# ── User lpb (UID 1000) ────────────────────────────────────────────────────
RUN set -eux; \
    if getent passwd 1000 >/dev/null; then \
      oldname="$(getent passwd 1000 | cut -d: -f1)"; \
      if [ "$oldname" != "lpb" ]; then \
        usermod -l lpb -d /home/lpb -m "$oldname"; \
        if getent group 1000 >/dev/null; then \
          oldgroup="$(getent group 1000 | cut -d: -f1)"; \
          [ "$oldgroup" = "lpb" ] || groupmod -n lpb "$oldgroup"; \
        fi; \
      fi; \
    else \
      useradd -m -s /bin/bash -u 1000 lpb; \
    fi; \
    chown -R 1000:1000 /home/lpb

RUN echo '%sudo ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/nopasswd && chmod 440 /etc/sudoers.d/nopasswd

# ── Global npm packages ─────────────────────────────────────────────────────
# Cache mounted — persists downloaded tarballs across builds.
RUN --mount=type=cache,target=/home/lpb/.npm \
    set -eux; \
    npm config set prefix '/home/lpb/.npm-global'; \
    npm config set fetch-retries 5; \
    npm config set fetch-retry-mintimeout 20000; \
    npm config set fetch-retry-maxtimeout 120000; \
    npm config set fetch-timeout 300000; \
    npm config set registry https://registry.npmjs.org/; \
    # Single comma-separated list — npm only reads the last value when a key
    # is repeated in .npmrc; this is written for both the build user (root)
    # and the runtime user (lpb). The mainstream pi packages do NOT declare
    # `allowScripts` (that was a fork-only addition — fork commit 2a3e9bcb8);
    # allow-scripts are managed here, independently, by devstack.
    printf 'allow-scripts=better-sqlite3,agent-browser,esbuild,protobufjs,@google/genai\n' > /home/lpb/.npmrc; \
    printf 'allow-scripts=better-sqlite3,agent-browser,esbuild,protobufjs,@google/genai\n' >> /root/.npmrc; \
    npm install -g zod@3 agent-browser exa-mcp-server; \
    chown -R 1000:1000 /home/lpb/.npm-global

# ── Pi (mainstream, pinned npm version) ─────────────────────────────────
# One package pulls in pi-ai/pi-tui/pi-agent-core/pi-client as dependencies.
# Mainstream pi packages do NOT declare allowScripts (fork-only addition;
# fork commit 2a3e9bcb8). devstack manages allow-scripts independently via the
# .npmrc comma-separated whitelist above (written for both root and lpb user).
# A source build for a forked pi is re-introduced only if strictly needed
# — see docs/reference/forking.md.
RUN set -eux; \
    export PATH="/home/lpb/.npm-global/bin:${PATH}"; \
    npm install -g @earendil-works/pi-coding-agent@${PI_VERSION}; \
    pi --version || (echo "FATAL: pi binary not functional" && exit 1); \
    [ "$(node -p "require('/home/lpb/.npm-global/lib/node_modules/@earendil-works/pi-coding-agent/package.json').version")" = "${PI_VERSION}" ] \
      || (echo "FATAL: installed pi version != ${PI_VERSION}" && exit 1); \
    ls -la /home/lpb/.npm-global/bin/

# ── Switch to non-root user ─────────────────────────────────────────────────
USER lpb

# ── Extensions + Config ─────────────────────────────────────────────────────
# Config repo is cloned at container start by start.sh — the image stays lean.

# ═══════════════════════════════════════════════════════════════════════════
# CLI IMAGE
# ═══════════════════════════════════════════════════════════════════════════
FROM base AS cli

# ── Support utilities (moved from config/support/ to devstack/support/) ──
# Copied to /opt/pi-support/ — used by start.sh at runtime
COPY --chmod=755 support/browser-state-cleanup.py /opt/pi-support/browser-state-cleanup.py
COPY support/browser-validate.ts /opt/pi-support/browser-validate.ts
COPY --chmod=755 support/_lib.sh /opt/pi-support/_lib.sh
COPY support/session-uuid.ts /opt/pi-support/session-uuid.ts
COPY --chmod=755 support/browser /opt/pi-support/browser
COPY support/validate-subagent-output.ts /opt/pi-support/validate-subagent-output.ts
COPY support/config/ /opt/pi-support/config/
COPY support/docs/ /opt/pi-support/docs/
COPY support/schemas/ /opt/pi-support/schemas/
# ── Shared Python helpers + user-facing CLIs ─────────────────────────────
# The localpibox package and the lpb-config/lpb-devstack CLIs are the source of
# truth in scripts/ (single copy — no support/ duplicates). They must live
# under /opt/pi-support/ for their sys.path resolution.
COPY scripts/localpibox/ /opt/pi-support/localpibox/
COPY --chmod=755 scripts/lpb-config /opt/pi-support/lpb-config
COPY --chmod=755 scripts/lpb-devstack /opt/pi-support/lpb-devstack

# ── Devstack deployment scripts ──
COPY --chmod=755 support/install-browser.py /opt/devstack/install-browser.py
COPY --chmod=755 support/validate.py /opt/devstack/validate.py
COPY --chmod=755 support/install-openspec.py /opt/pi-support/install-openspec.py
COPY --chmod=755 support/start.sh /opt/devstack/start.sh
COPY lpb.conf.env /opt/devstack/lpb.conf.env
COPY lpb.stack.env /opt/devstack/lpb.stack.env
COPY --chmod=755 support/entrypoint-cli.sh /opt/devstack/entrypoint-cli.sh

# ── User-facing support utilities (baked, not first-run) ────────────────
# These are immutable tools users invoke from PATH; they live in the image
# layer so they survive rebuilds/recreates. Symlinks are created here (as
# lpb) rather than at container first-run — ~/.local/bin is not a mount, so
# a first-run-only ln would vanish on the next rebuild.
RUN mkdir -p /home/lpb/.local/bin \
    && ln -sf /opt/devstack/install-browser.py /home/lpb/.local/bin/install-browser \
    && ln -sf /opt/devstack/validate.py /home/lpb/.local/bin/validate \
    && ln -sf /opt/pi-support/install-openspec.py /home/lpb/.local/bin/install-openspec \
    && ln -sf /opt/pi-support/browser-state-cleanup.py /home/lpb/.local/bin/browser-state-cleanup \
    && ln -sf /opt/pi-support/lpb-config /home/lpb/.local/bin/lpb-config \
    && ln -sf /opt/pi-support/lpb-devstack /home/lpb/.local/bin/lpb-devstack

# ── Shell PATH helper ───────────────────────────────────────────────────────
# lpb-config / lpb-devstack need to live under /opt/pi-support/ for their
# sys.path resolution (the localpibox package is copied there above).
# Symlinks at ~/.local/bin give users the clean CLI names.

# ── Root operations: ownership + gitconfig + shell PATH ─────────────────────
USER root
RUN mkdir -p /home/lpb/.agent-browser/sessions \
    && chown -R 1000:1000 /home/lpb /opt/devstack /opt/pi-support \
    && printf '[credential "https://github.com"]\n    helper = !gh auth git-credential\n[credential "https://gist.github.com"]\n    helper = !gh auth git-credential\n' > /home/lpb/.gitconfig \
    && chown 1000:1000 /home/lpb/.gitconfig \
    && printf '\n# LocalPibox: npm-global and local bin on PATH\nexport PATH="/home/lpb/.npm-global/bin:/home/lpb/.local/bin:${PATH}"\n' >> /home/lpb/.bashrc

USER lpb

WORKDIR /home/lpb/workspace

LABEL org.opencontainers.image.title="LocalPibox Devstack — CLI" \
      org.opencontainers.image.description="AI-powered dev environment with Pi CLI (interactive terminal)" \
      org.opencontainers.image.source="https://github.com/lpb-stack/devstack" \
      org.opencontainers.image.vendor="LocalPibox" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.revision="${IMAGE_REVISION}" \
      org.opencontainers.image.created="${IMAGE_BUILT}"

ENTRYPOINT ["/opt/devstack/entrypoint-cli.sh"]

# ═══════════════════════════════════════════════════════════════════════════
# WEB IMAGE — Extends cli + adds VSCodium + web access
# ═══════════════════════════════════════════════════════════════════════════
FROM cli AS web

# ── VSCodium ────────────────────────────────────────────────────────────────
USER root
RUN curl -fsSL \
      "https://github.com/VSCodium/vscodium/releases/download/${VSCODIUM_VERSION}/vscodium-reh-web-linux-x64-${VSCODIUM_VERSION}.tar.gz" \
    -o /tmp/vscodium.tar.gz \
    && mkdir -p /opt/vscodium \
    && tar -xzf /tmp/vscodium.tar.gz -C /opt/vscodium --strip-components=1 \
    && rm /tmp/vscodium.tar.gz

# ── VSCodium extensions ─────────────────────────────────────────────────────
RUN set -eux; \
    export PATH="/home/lpb/.npm-global/bin:${PATH}"; \
    EXT_DIR="/home/lpb/.vscodium-server/extensions"; \
    mkdir -p "${EXT_DIR}"; \
    install_ext() { \
        publisher="$1"; name="$2"; \
        meta_url="https://open-vsx.org/api/${publisher}/${name}"; \
        version=$(curl -fsSL "${meta_url}" | jq -r '.version'); \
        if [ -z "$version" ] || [ "$version" = "null" ]; then echo "WARN: no version for ${publisher}.${name}"; return 0; fi; \
        vsix_url=$(curl -fsSL "${meta_url}" | jq -r '.files.download'); \
        if [ -z "$vsix_url" ] || [ "$vsix_url" = "null" ]; then echo "WARN: no download for ${publisher}.${name}"; return 0; fi; \
        dest="${EXT_DIR}/${publisher}.${name}-${version}"; \
        mkdir -p "${dest}"; \
        curl -fsSL "${vsix_url}" -o /tmp/ext.vsix || return 0; \
        rm -rf /tmp/ext_extracted; mkdir -p /tmp/ext_extracted; \
        unzip -q /tmp/ext.vsix -d /tmp/ext_extracted; \
        cp -r /tmp/ext_extracted/extension/. "${dest}/"; \
        rm -rf /tmp/ext.vsix /tmp/ext_extracted; \
        echo "  Installed ${publisher}.${name}@${version}"; \
    }; \
    install_ext pi0 pi-vscode || echo "WARN: pi-vscode install failed"; \
    chown -R 1000:1000 /home/lpb/.vscodium-server

COPY --chmod=755 support/entrypoint-web.sh /opt/devstack/entrypoint-web.sh

RUN chown -R 1000:1000 /home/lpb

USER lpb

WORKDIR /home/lpb/workspace

ENV ED_PORT=3000
ENV HOST=0.0.0.0
# Auto-generated random token on container start.
# Override with --env CONNECTION_TOKEN=xxx or set a fixed value for persistence.
ENV CONNECTION_TOKEN=

# Healthcheck uses bare names (VSCodium server reads them)
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=60s \
    CMD curl -sf "http://localhost:${ED_PORT}/" >/dev/null 2>&1 && curl -sf "http://localhost:${ED_PORT}/?tkn=${CONNECTION_TOKEN:-}" >/dev/null 2>&1 || exit 1

LABEL org.opencontainers.image.title="LocalPibox Devstack — Web" \
      org.opencontainers.image.description="AI-powered dev environment with VSCodium web editor" \
      org.opencontainers.image.source="https://github.com/lpb-stack/devstack" \
      org.opencontainers.image.vendor="LocalPibox" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.revision="${IMAGE_REVISION}" \
      org.opencontainers.image.created="${IMAGE_BUILT}"

ENTRYPOINT ["/opt/devstack/entrypoint-web.sh"]

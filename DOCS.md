# Docs site (MkDocs Material + mike)

This branch carries the documentation site for the LocalPibox stack.

- **Content lives in place** — `README.md`, `doc/`, `support/docs/`,
  `.pi/skills/...` are the same files as on `dev`. Doc changes are made
  on `dev` like any other content; this branch only needs a one-shot sync
  at release time (see below).
- **`docs/` is derived** (gitignored) — `scripts/generate.py` copies the
  tracked content and stamps the repo map page (roles, branches, latest
  tags) from the 6 stack repos. Run it before every build.
- **Mermaid diagrams** (```mermaid fences, e.g. in `README.md`) are rendered
  client-side by the Material theme, which lazy-loads mermaid from the
  unpkg CDN on first visit — viewing diagrams needs internet access;
  without it the raw source shows as a code block (fallback).
- **Versions are stable-only** — one immutable docs version per stable
  release, published by the main pipeline (`docs-publish` job in
  `build-and-publish.yml`) after `tag-repos`. There are no published
  dev versions (no `edge`, no `dev` alias). The `latest` alias and the
  site root point at the newest stable version.
- **Served** from the `gh-pages` branch (mike's branch) by GitHub Pages:
  `https://lpb-stack.github.io/devstack/<version>/`

## Release flow — docs are gated into the stable release

1. Doc changes happen **on `dev`** (nothing docs-related runs while
   working).
2. Before promoting, flag the docs as reviewed:

   ```bash
   lpb-devstack release docs-ready
   ```

   This merges `origin/dev` → `docs` (worktree at
   `~/.lpb-stack/docs-preview`), regenerates + builds the site, and —
   after your review and confirmation — commits `DOCS_READY=<stable-version>`
   on the `docs` branch and pushes it. Review first:

   ```bash
   cd ~/.lpb-stack/docs-preview && python3 -m http.server 8000 -d site   # http://localhost:8000
   ```
   (serves the local `mkdocs build` output — `mike serve` serves the
   gh-pages branch, i.e. the last *deployed* content, not your build)

3. `lpb-devstack release status` shows the docs verdict:
   - `READY` — `DOCS_READY` matches the release version and doc content
     matches `dev`
   - `MISSING` — no flag yet
   - `STALE` — flag for another version, or doc content changed on `dev`
     after flagging (re-run `docs-ready`)
4. `lpb-devstack release promote` **refuses** unless docs are `READY`
   for the version being released (`--force` overrides with a warning).
5. The main pipeline re-verifies the flag (catches `--force`
   promotions), then publishes: `mike deploy <version> latest --push
   --update-aliases` + `mike set-default latest --push`.

Note: while no fully-aligned stable release exists yet, the newest
`*-lpb-dev` content can be previewed locally (step 2) but is not
published; the first `0.0.X-lpb` stable release flips the site root.

## Local build / preview

```bash
python3 -m pip install --user --break-system-packages "mkdocs-material==9.7.7" mike   # once
git checkout docs && git pull
python3 scripts/generate.py            # or: --tag 0.0.X-lpb
mkdocs build
python3 -m http.server 8000 -d site    # preview the local build at http://localhost:8000
```

## Fallback (manual mike cut)

If CI's `docs-publish` job fails or an out-of-band cut is needed:

```bash
git checkout docs && git pull
git config user.name lpb-docs && git config user.email ci@lpb-stack.dev
python3 scripts/generate.py --tag 0.0.X-lpb
mike deploy 0.0.X-lpb latest --push --update-aliases
mike set-default latest --push
```

If mike reports `gh-pages has diverged`, the local branch is stale from an
earlier local session — reset it: `git fetch origin gh-pages &&
git update-ref refs/heads/gh-pages origin/gh-pages`.

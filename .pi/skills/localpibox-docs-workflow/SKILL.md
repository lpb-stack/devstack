---
name: lpb-stack-docs-workflow
description: Create and maintain LocalPibox docs — GitHub-compatible Mermaid, single-source content on dev branches, the docs site (mkdocs + mike), and the release docs gate.
---
# LocalPibox Documentation Workflow

Docs are **single-source**: the tracked content lives on the **`dev` branch of
each repo** (README, CONTRIBUTING, `doc/`, `AGENTS.md`, …). A separate
**`docs` branch** (devstack only) carries the site tooling and materializes a
derived `docs/` tree with `scripts/generate.py` before every build. Versions
are published with **mike** (one immutable docs version per stable tag, served
from `gh-pages` at `https://lpb-stack.github.io/devstack/<version>/`).

## When to Use

- Writing or revising any doc in devstack, config, or lpb-memory
- Adding or fixing Mermaid diagrams (must render on GitHub **and** the site)
- Adding a new reference page to the docs site
- Preparing a stable release (docs gate: `release docs-ready` / `status` / `promote`)
- Debugging "the site shows stale content" or broken links on the site

## Where Docs Live

| Repo | Tracked doc content (on `dev`) | Notes |
|---|---|---|
| **devstack** | `README.md`, `CONTRIBUTING.md`, `doc/*.md`, `support/docs/*.md`, `.pi/skills/*/SKILL.md` | Site source. `docs/` and `site/` are **derived, gitignored** — never edit or commit them |
| **config** | `README.md`, `CONTRIBUTING.md`, `VALIDATION.md`, `AGENTS.md` | `AGENTS.md` is agent-facing (loaded as system context) — keep it current like any doc |
| **lpb-memory** | `README.md`, `CONTRIBUTING.md`, `AGENTS.md`, `CHANGELOG.md` | `docs/0.x/PLAN.md` + `TASKS.md` are working docs (task tracking), not reference docs |

**Site plumbing (devstack `docs` branch only):** `mkdocs.yml` (nav),
`scripts/generate.py` (CONTENT map: source → derived path), `DOCS.md`,
`DOCS_READY` flag file. These files do **not** exist on `dev`.

**Branch facts** (for "branch off X / PR to X" lines in CONTRIBUTING files):
default branch is `dev` for devstack/config/lpb-memory, `lpb-dev` for the
forks (pi, pi-subagents, lemonade-pi-plugin). `main`/`lpb` are stable
branches written by `lpb-devstack release promote` — not PR targets.

## GitHub-Compatible Mermaid

GitHub renders ` ```mermaid ` fences natively (mermaid 11.x). The site renders
the same fences via `pymdownx.superfences` (already configured in
`mkdocs.yml`). Write for the **lowest common denominator**:

- **Fence:** exactly ```` ```mermaid ```` — no `%%{init: …}%%` directives
  (portability: GitHub + site + VS Code), no HTML tags outside of `<br/>`.
- **Reserved keywords** break when used as **bare node ids** (quote them or
  rename): `STYLE`, `LINKSTYLE`, `CLASSDEF`, `CLASS`, `CLICK`, `GRAPH`,
  `DIR`, `subgraph`, `end`, `DOWN`, `UP`. Lowercase `end` is the most fragile.
- **Quote any label** containing special characters (`:`, `/`, `(`, `+`,
  spaces): `H4["Lemonade (:13305)"]`. In special node shapes (e.g.
  parallelogram `[/…/]`) escape with `\\` and `\/`.
- **Line breaks in labels:** `\n` (backslash-n) inside a quoted label is
  converted to a line break by mermaid 11 (verified by render — the SVG gets
  `<br />`). `<br/>` is the documented, most portable alternative; multi-line
  *markdown strings* (label in double quotes **and** backticks, e.g.
  `A["`**Bold** and
  line two`"]`) also work. All three are fine on GitHub — pick one style per
  diagram and stay consistent.
- **Markdown strings** (backticks inside double quotes) support `**bold**`,
  `_italic_` and real line breaks; they expect `htmlLabels: false`. For
  maximum portability prefer plain quoted labels; use markdown strings only
  when formatting is worth the renderer dependency.
- **Subgraphs:** `subgraph Id["Label"]` … `end`; `direction TB/LR` inside;
  edges between subgraphs work (`one --> two`).
- **Semantics before syntax:** every edge must represent a real relationship
  in the system (e.g. Lemonade ↔ Pi provider — *not* Lemonade ↔ Chrome). A
  diagram that renders but wires things wrong is worse than a table.
- Keep diagrams small (≤ ~10 nodes) and mirror a table when the table is
  clearer. One diagram per concept; don't merge "architecture" and
  "update flow" into one picture.

**Validate every diagram before shipping** (see Validation below) — a parse
failure means a raw code block on GitHub.

## Writing Conventions

1. **Verify against code before writing.** File trees vs `ls`, pins vs
   `settings.json.template`, versions vs `VERSION`/`package.json`, branches
   vs `git remote show origin`, commands vs the CLI's `--help`. Stale facts
   (old paths, removed commands, wrong branch names) are the #1 doc failure
   in this stack.
2. **Cross-file consistency:** the fork/repo table (README ↔
   `doc/fork-improvements.md` ↔ `CONTRIBUTING.md`), the support-files table
   (Dockerfile `COPY` lines are the source of truth), and "how to contribute"
   steps must match across README/CONTRIBUTING/AGENTS in every repo.
3. **Links (devstack):** README links doc pages as `doc/<x>.md` — that path
   works on GitHub, and `generate.py` rewrites it to `reference/<x>.md` in
   the derived site copy. Doc pages link siblings with bare filenames
   (e.g. the link target `lpb-cli.md`). **Every linked `doc/*.md` must exist in
   generate.py's CONTENT map + mkdocs nav** or the site link 404s.
4. **Callouts:** use `> ⚠️ …` blockquotes — they render on GitHub *and* the
   site. MkDocs admonitions (`!!! warning`) do **not** render on GitHub.
5. **Point-in-time docs** (analysis reports, design notes): start with a
   banner — `> ⚠️ Point-in-time diagnostic (date). Settings have changed
   since: …` — so nobody mistakes a snapshot for current truth.
6. **Template-driven files:** document `*.template` (tracked) and the
   rendered runtime file (gitignored, generated at boot) explicitly; never
   list the rendered file as tracked.
7. **External links:** the org is `github.com/lpb-stack` (there is no
   `localpibox` repo); the docs site is `lpb-stack.github.io/devstack/`.
   "Back to LocalPibox" links point at `lpb-stack/devstack`.
8. **Docs and tests stay in sync** — a behavior change without a doc update
   (and vice versa) is an incomplete change.

## Maintenance Workflow

**Doc changes** (any repo):
1. Edit the tracked content on `dev` in the owning repo.
2. Validate: mermaid parse check, link check (commands below).
3. Commit with a `docs:` prefix (matches how the release gate's content
   drift check sees changes).

**New site page** (devstack):
1. Put the source in place on `dev` (e.g. `doc/<name>.md`).
2. On the `docs` branch: add it to `generate.py` CONTENT
   (`("doc/<name>.md", "reference/<name>.md")`) **and** to `mkdocs.yml` nav.
   Both — missing either produces a 404 or an unlisted page.
3. `README.md`/doc pages may then link it (`doc/<name>.md`).

**Release gate** (docs must be flagged before a stable release):
1. `lpb-devstack release docs-ready` — merges `origin/dev` → `docs` in the
   worktree `~/.lpb-stack/docs-preview`, regenerates + builds the site, and
   (after your review) commits `DOCS_READY=<stable-version>` on `docs`.
2. Review the **local build**: `cd ~/.lpb-stack/docs-preview &&
   python3 -m http.server 8000 -d site` → http://localhost:8000.
   This serves exactly what `mkdocs build` just produced (relative URLs
   work as-is — the `/devstack/<version>/` prefix is applied by mike at
   deploy time, not by the local server).
   ⚠️ **`mike serve` is NOT the review command** (mike 2.x): it serves
   the **`gh-pages` branch** — i.e. the *last deployed* content, stale
   for a pending release. It only accepts `-a HOST[:PORT]` (no `--port`),
   and `python3 -m mike serve` fails (no `__main__`).
3. `lpb-devstack release status` → verdicts: `READY` / `MISSING` /
   `WRONG-VERSION` / `STALE`.
4. `lpb-devstack release promote` **refuses** unless `READY` (`--force`
   overrides). The main pipeline re-verifies the flag, then publishes
   (`mike deploy <version> latest`) to `gh-pages`.
5. **Drift rule:** only *site content* invalidates the flag (README,
   CONTRIBUTING, `doc/**`, `support/docs/**`, `.pi/**`). Code paths
   (`scripts/`, `support/` excl. docs, env files, Dockerfile) do not.

## Validation (run before any doc commit)

```bash
SKILL=.pi/skills/localpibox-docs-workflow   # in the devstack repo

# 1. Mermaid: every ```mermaid block must parse (mermaid 11, no browser).
#    One-time setup: copy the script into a scratch dir with its deps
#    (bare imports resolve from the script's location — the copy matters).
D=/tmp/lpb-doc-check; mkdir -p $D
cp $SKILL/check-mermaid.mjs $D/
(cd $D && [ -d node_modules ] || npm install --no-audit --no-fund mermaid@11.12.2 jsdom@24)
node $D/check-mermaid.mjs README.md CONTRIBUTING.md doc/*.md support/docs/*.md

# 2. Links: every relative .md link in the repo must resolve (tracked files only)
node $SKILL/check-links.mjs .            # (repeat per repo root)
```

Optional visual check (needs a working Chrome — e.g. the agent-browser
build at `~/.agent-browser/browsers/`):

```bash
export PUPPETEER_EXECUTABLE_PATH=~/.agent-browser/browsers/chrome-*/chrome-linux64/chrome
printf '{ "executablePath": "%s", "args": ["--no-sandbox"] }' \
  "$PUPPETEER_EXECUTABLE_PATH" > /tmp/pptr.json
npx -y @mermaid-js/mermaid-cli@11 -i diagram.mmd -o out.svg -p /tmp/pptr.json
```

**Done when:** parse check passes for all diagrams, link check passes for
every repo touched, and (for release) `release status` shows `READY`.

## Pitfalls

- **Editing derived trees:** `docs/` and `site/` in the devstack working
  tree are local build output (gitignored on `dev`). They may be stale on
  disk — the source of truth is always the tracked files.
- **Literal `\n` surprises:** it *does* become a line break in mermaid 11
  flowchart labels (verified), but it is easy to confuse with a real newline
  in a markdown string — if a label shows the characters `\n`, you double-
  escaped it. Use one line-break style per diagram.
- **Unquoted special chars** (`:`, `/`, `(`) in node labels can break the
  parse on some renderers even when others tolerate them — quote everything.
- **Bare `end`/`class`/`click` ids** silently terminate subgraphs or throw.
- **Linking a `doc/*.md` page not in the CONTENT map** → works on GitHub,
  404s on the site.
- **mike 2.x** has no `mike build`; use `mkdocs build`. And `mike serve`
  serves the **`gh-pages` branch**, not your local build — reviewing a
  fresh build means `python3 -m http.server 8000 -d site` in the preview
  worktree. (Verified against mike 2.2.0: `serve()` hard-codes
  `branch='gh-pages'` and reads files via git, so it shows last-deployed
  content — exactly the wrong thing to review pre-release.)
- **Version stamps:** generate.py matches the devstack VERSION **exactly**
  (substring matching confuses `0.0.N-lpb` with `0.0.N-lpb-dev`).
- **Docs branch is not a PR target** — content is merged `dev → docs` by
  `release docs-ready`; only the site plumbing lives there.
- **mermaid/jsdom pin:** the parse check is pinned to `mermaid@11.12.2` +
  `jsdom@24` — 11.17.0 fails under jsdom with
  `DOMPurify.sanitize is not a function` (test newer minors before moving
  the pin). GitHub itself tracks current 11.x; a parse pass on the pinned
  build is a proxy, the mmdc render check below is the full proof.

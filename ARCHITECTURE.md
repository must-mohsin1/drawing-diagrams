# Architecture

How the `drawing-diagrams` skill works end-to-end — from a user request to a hand-drawn Excalidraw canvas + canonicalized JSON snapshot on disk.

![Architecture overview](screenshots/02-architecture.png)

---

## The two paths

The skill routes every diagram request down one of two execution paths:

| Path | When | Components |
|---|---|---|
| **Deterministic** | The user wants a dependency map from a `package.json` | `scripts/gen_deps_diagram.py` + `scripts/dep_rules.yaml` |
| **LLM-driven** | The user wants an architecture, sequence, state, flowchart, data-flow, or ER diagram | Claude reads `references/canvas-api.md` + `references/layout-disciplines.md` and composes element JSON directly |

Both paths converge at the same backend: a `POST /api/elements/batch` to the canvas server.

![Skill execution flowchart](screenshots/03-flowchart.png)

---

## Components

### 1. `SKILL.md` — the orchestrator

Loaded by Claude Code at session start (via the YAML frontmatter description). It contains:

- A **type-detection table** mapping user phrasing to diagram type
- Step-by-step routing instructions for each type
- Per-type tips (e.g. "use diamond for decisions in flowcharts")
- A common-mistakes table

When Claude matches a user request like *"draw the architecture of `~/projects/foo`"*, it reads `SKILL.md`, identifies the type as "Architecture", and follows the LLM-driven path.

### 2. `references/` — the LLM context bundle

Two markdown files that Claude is explicitly instructed to read before composing any LLM-driven diagram:

- **`canvas-api.md`** — REST endpoints, element schemas (rectangle, ellipse, diamond, arrow, text), color palette, `fixedPoint` binding reference, race-condition handling, canonicalization rules
- **`layout-disciplines.md`** — pitch & breathing room, defensive sizing for wrapping labels, cascade-aware row placement, column counts by item count, verification checklist

Without these, Claude would rediscover every layout gotcha each session. With them, a fresh session draws a clean architecture diagram on the first try.

### 3. `scripts/gen_deps_diagram.py` — deterministic generator

A standalone Python script (no LLM in the loop) that:

1. Reads a `package.json`
2. Categorizes deps into 8 buckets via regex patterns from `dep_rules.yaml`
3. Applies family-collapse rules (e.g. `@types/*` → single entry, `@xterm/*` → single entry)
4. Computes layout with defensive sizing
5. POSTs all elements to the canvas
6. Polls `GET /api/elements` until the count matches what was POSTed
7. Verifies containment (every dep box inside its zone) and inter-zone gaps (≥20 px)
8. Writes a canonicalized snapshot (volatile fields stripped) for git-friendly diffs

Two consecutive runs over the same `package.json` produce **byte-identical** output — critical for CI.

### 4. `scripts/dep_rules.yaml` — categorization rules

User-editable bucket definitions. Each bucket has:

- `id` — used internally for element IDs
- `title` — shown as zone header
- 5 color fields (zone bg, zone stroke, header text, box bg, box stroke)
- `patterns` — list of regex strings matched against package names

Buckets are checked in declaration order; first match wins. Unmatched runtime deps fall through to `utils`; **all `devDependencies` always go to `dev`** regardless of pattern (declaration-over-semantics rule — `@docusaurus/types` is a dev dep, so it goes to Dev/Build even though `^@docusaurus/` matches the Web UI bucket).

### 5. Canvas server (external dependency)

[yctimlin/mcp_excalidraw](https://github.com/yctimlin/mcp_excalidraw) — a Node.js server that exposes Excalidraw via a REST API + WebSocket sync to the browser frontend. The skill expects it running at `http://127.0.0.1:3030`. The skill auto-starts it on first use if it's installed at `~/mcp_excalidraw/`.

### 6. `ci/` — automation harness

- **`ci/.github/workflows/diagram-refresh.yml`** — drop-in GitHub Action that regenerates the dep diagram on every `package.json` change and opens a PR if the snapshot differs
- **`ci/simulate_ci.sh`** — local dry-run of the same flow, validates against the user's repo before they commit the workflow

---

## Data flow

A typical request, beginning to end:

1. **Trigger.** User types *"Draw the dependency map for `~/projects/my-app`"* in a Claude Code session.
2. **Skill match.** Claude's session-start skill registry includes `drawing-diagrams` (loaded from `~/.claude/skills/drawing-diagrams/SKILL.md`). The trigger phrasing matches.
3. **Type detection.** Claude reads `SKILL.md`. The phrase *"dependency map"* routes to the **deterministic** path.
4. **Canvas health check.** Skill runs `curl /health`. If the canvas server isn't up, the skill starts it: `cd ~/mcp_excalidraw && PORT=3030 nohup node dist/server.js`.
5. **Generation.** Claude runs `gen_deps_diagram.py --package ... --rules ... --canvas ... --output ...`. Python:
   - Reads `package.json` and `dep_rules.yaml`
   - Categorizes deps
   - Builds Excalidraw element JSON with defensive sizing
   - `POST /api/elements/batch` → canvas
   - Polls until `GET /api/elements` returns the right count (the race-condition fix)
   - Asserts containment + gaps
   - Strips volatile fields (timestamps, seeds, version nonces)
   - Writes canonical JSON to `--output`
6. **Browser sync.** The canvas server's WebSocket pushes the new elements to any open browser tab at `http://127.0.0.1:3030`. The user sees the diagram render in real time with the hand-drawn animation.
7. **Snapshot committed.** The `--output` file is git-trackable. Same inputs → same bytes.

For an LLM-driven type (architecture, sequence, etc.), step 5 looks slightly different:

5a. Claude reads `references/canvas-api.md` for the element schema + color palette
5b. Claude reads `references/layout-disciplines.md` for the layout rules
5c. Claude reads relevant source files (for arch) or follows user-provided structure (for sequence/state/flowchart)
5d. Claude composes the element JSON inline and `POST`s to the canvas
5e. Claude runs verification queries against `GET /api/elements`

Steps 6 and 7 are identical.

---

## Key design decisions

### Why two paths instead of one?

The deterministic path is **cheaper, faster, and reproducible** for fact-based diagrams. A dep map is fully determined by `package.json` + rules; an LLM in that loop adds latency, cost, and indeterminism without adding quality.

The LLM-driven path is **essential for synthesis** diagrams (architecture, sequence, etc.) where the structure isn't directly derivable from a single file.

### Why bundle references inside the skill?

The references encode hard-won lessons from a dogfood pilot (race condition, defensive sizing for label wrapping, cascade rule for stacked zones, deterministic canonicalization). If Claude had to rediscover these every session, the LLM-driven path would have a 50-70% first-try failure rate. With the references loaded as part of the skill, fresh sessions get to a working diagram on the first try.

### Why poll-until-match instead of `sleep N`?

`POST /api/elements/batch` returns success **before** the canvas server has finished propagating the new elements via WebSocket to the page state. A subsequent `GET /api/elements` can return the cleared/stale state. The fix is to poll `GET /api/elements` until `count` matches what was POSTed, with a timeout. Hardcoded sleeps are flaky (variable propagation time, 50–500 ms depending on payload).

### Why canonicalize snapshots?

Excalidraw element objects include `seed`, `version`, `versionNonce`, `updatedAt`, `syncedAt`, etc. — fields that change every render even when content is identical. Stripping these in the output makes two consecutive runs produce byte-identical JSON, which means `git diff` shows real changes only and CI can reliably decide "diagram changed yes/no".

### Why is `dev` the catch-all for devDependencies?

Following the **declaration over semantics** rule. `@docusaurus/types` matches `^@docusaurus/` (Web UI bucket) by regex, but the author declared it as a devDependency. The author's declaration beats the regex — types deps belong in Dev/Build, where you expect them.

---

## File layout

```
drawing-diagrams/
├── SKILL.md                              ← The skill — Claude loads this first
├── README.md                             ← Public-facing docs (install, use, why)
├── ARCHITECTURE.md                       ← This file
├── LICENSE                               ← MIT
├── .gitignore
├── references/
│   ├── canvas-api.md                     ← REST endpoints, schemas, palette, race fix
│   └── layout-disciplines.md             ← Pitch, defensive sizing, cascade, verify
├── scripts/
│   ├── gen_deps_diagram.py               ← Deterministic dep-map generator
│   └── dep_rules.yaml                    ← Categorization rules (regex-based)
├── ci/
│   ├── .github/workflows/
│   │   └── diagram-refresh.yml           ← Drop into target repo's workflows
│   └── simulate_ci.sh                    ← Local dry-run before committing CI
└── screenshots/
    ├── 01-dependency-map.png             ← Example: docusaurus deps
    ├── 02-architecture.png               ← This skill's own architecture
    └── 03-flowchart.png                  ← The execution flowchart
```

---

## Extending the skill

### Add a new diagram type (LLM-driven)

1. Add an entry to the type-detection table in `SKILL.md`
2. Add a per-type tips block in `SKILL.md` describing the visual conventions (e.g. "swimlanes use vertical lifelines with dashed strokeStyle")
3. (Optional) Add a worked example to `references/canvas-api.md`

No code changes — the LLM composes from primitives using the existing references.

### Add a new deterministic generator

For new fact-based diagram types (file tree, schema-from-SQL, etc.):

1. Add a new script in `scripts/`
2. Mirror the contract: takes inputs, talks to the canvas REST API, snapshots to `--output`, exits 0 on success
3. Use the same poll-until-match + canonicalize patterns from `gen_deps_diagram.py`
4. Add a row to the type-detection table in `SKILL.md` pointing at it

### Add a new bucket / customize categorization

Edit `scripts/dep_rules.yaml`. Add a bucket entry with `id`, `title`, 5 color fields, and a `patterns` list. Buckets matched in declaration order, first match wins.

---

## Provenance

Born from a dogfood pilot that hammered the deterministic generator across 4 real `package.json` files (mcp-excalidraw-server, a Docusaurus website, hermes-tui, hermes-web — 13 to 35 deps each). The pilot caught 7 bugs in the first repo; 6 were fixed in the prompt template / rules and the next 3 repos drew cleanly on the first try. The references encode the lessons; the verify-after-POST routine catches the rare regressions.

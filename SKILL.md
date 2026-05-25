---
name: drawing-diagrams
description: Use when asked to draw, render, or visualize any diagram on a hand-drawn Excalidraw canvas — architecture diagrams, dependency maps (Node.js package.json AND Python pyproject.toml/requirements.txt), file tree / directory structure, sequence diagrams, state machines, flowcharts, process flows, system topologies, data-flow diagrams, ER diagrams, or "draw this on a whiteboard". Triggers include "draw the architecture", "visualize this", "make a diagram", "deps diagram", "Python deps", "show the file tree", "draw the directory structure", "sequence for the login flow", "flowchart for X", "state machine", "draw the data flow", "whiteboard this", or any "render onto the canvas / Excalidraw" intent. Skip for PDFs, screenshots of existing images, UI mockups, or non-visual outputs.
---

# Drawing Diagrams on Excalidraw

Render any diagram type onto a live Excalidraw canvas at `http://127.0.0.1:3030`, then export a canonicalized JSON snapshot. Two paths:

- **Deterministic generators** for fact-based diagrams: dependency map (from `package.json`), file tree (planned).
- **LLM-driven canvas-primitive composition** for synthesis diagrams: architecture, sequence, state machine, flowchart, data flow, ER/schema, custom.

The skill teaches the LLM the canvas API, the layout disciplines, and how to verify before declaring done.

## Type detection — figure out what kind of diagram

| User said something like… | Diagram type | Path |
|---|---|---|
| "draw the deps", "dep map", "visualize package.json" (Node) | **Node.js dep map** | Deterministic — `scripts/gen_deps_diagram.py` against a `package.json` |
| "Python deps", "visualize pyproject.toml", "deps from requirements.txt" | **Python dep map** | Deterministic — `scripts/gen_deps_diagram.py` against `pyproject.toml` or `requirements.txt`. Auto-detects ecosystem from filename. |
| "file tree", "directory structure", "show me the repo layout", "draw the codebase" | **File tree** | Deterministic — `scripts/gen_file_tree_diagram.py` walks the directory |
| "architecture diagram", "system diagram", "modules + relationships", "draw the components" | **Architecture** | LLM-driven (read code, pick layout, place primitives) |
| "sequence diagram", "request flow", "swimlanes", "how X talks to Y over time" | **Sequence** | LLM-driven (vertical lifelines + horizontal messages) |
| "state machine", "states and transitions", "lifecycle of X" | **State machine** | LLM-driven (ellipses + labeled transition arrows) |
| "flowchart", "process flow", "decision tree", "onboarding flow", "user signs up → email → active" | **Flowchart** | LLM-driven (rectangles + diamonds for decisions) |
| "data flow", "ETL diagram", "pipeline diagram" | **Data flow** | LLM-driven (sources → processors → sinks) |
| "ER diagram", "schema diagram", "database tables" | **ER / schema** | LLM-driven (entities as tall rects + relationship arrows) |
| "from this mermaid", "convert mermaid to excalidraw" | **Mermaid** | POST to `/api/elements/from-mermaid` |
| "modify the canvas", "change the colors on", "redraw the X" | **Edit existing** | PUT individual elements (`/api/elements/:id`) |

If unclear, **ask one short question:** "Architecture, sequence, flowchart, or something else?"

## Prerequisites — one-time setup

Canvas server source at `~/mcp_excalidraw/`. If missing:

```bash
git clone https://github.com/yctimlin/mcp_excalidraw.git ~/mcp_excalidraw
cd ~/mcp_excalidraw && npm install && npm run build
```

Python ≥3.7 with `pyyaml` (always required for the deterministic paths) and `tomli` (only if Python <3.11 and you'll use the Python dep-map path — Python 3.11+ has `tomllib` built in):

```bash
pip3 install pyyaml         # always
pip3 install tomli          # only for Python <3.11 with pyproject.toml support
```

## Step 1 — Ensure canvas server is running

```bash
if ! curl -sf http://127.0.0.1:3030/health >/dev/null 2>&1; then
  cd ~/mcp_excalidraw && PORT=3030 nohup node dist/server.js > /tmp/excalidraw-canvas.log 2>&1 &
  for i in 1 2 3 4 5; do sleep 1; curl -sf http://127.0.0.1:3030/health >/dev/null && break; done
fi
```

If port 3030 is occupied by something unrelated, use 3031 (and adjust everything below). `lsof -i:3030` will tell you who's listening.

## Step 2 — Route to the right generator

### Path A — Dependency map (deterministic, no LLM needed)

Works for **Node.js** (`package.json`), **Python** (`pyproject.toml` and `requirements.txt`). Ecosystem auto-detected from filename. Rules file auto-picked unless `--rules` is passed explicitly.

```bash
# Node — package.json
python3 ~/.claude/skills/drawing-diagrams/scripts/gen_deps_diagram.py \
  --package <ABSOLUTE-PATH-TO/package.json> \
  --canvas  http://127.0.0.1:3030 \
  --output  <ABSOLUTE-OUTPUT-PATH>

# Python — pyproject.toml (PEP 621, PEP 735, Poetry, PDM all supported)
python3 ~/.claude/skills/drawing-diagrams/scripts/gen_deps_diagram.py \
  --package <ABSOLUTE-PATH-TO/pyproject.toml> \
  --canvas  http://127.0.0.1:3030 \
  --output  <ABSOLUTE-OUTPUT-PATH>

# Python — requirements.txt (no dev/runtime split — everything goes runtime)
python3 ~/.claude/skills/drawing-diagrams/scripts/gen_deps_diagram.py \
  --package <ABSOLUTE-PATH-TO/requirements.txt> \
  --canvas  http://127.0.0.1:3030 \
  --output  <ABSOLUTE-OUTPUT-PATH>
```

Exit 0 + `verification ✅` line = success. Containment + 20px inter-zone gaps verified automatically. Two runs over the same input produce byte-identical canonicalized output.

**Customize categorization:**
- Node: edit `~/.claude/skills/drawing-diagrams/scripts/dep_rules.yaml`
- Python: edit `~/.claude/skills/drawing-diagrams/scripts/python_dep_rules.yaml`

Add new buckets with `patterns:` (regex). Buckets matched in declaration order, first match wins. devDependencies (or PEP 735 `[dependency-groups]` / Poetry dev / PDM dev) always go to `dev` bucket regardless of pattern.

### Path A2 — File tree (deterministic, walks the filesystem)

Visualize a directory's structure as an indented hand-drawn tree. Color-codes by file extension (dirs blue, Python yellow, TypeScript blue, Markdown teal, configs purple, images pink, special files like README/LICENSE/Dockerfile in tinted gold).

```bash
python3 ~/.claude/skills/drawing-diagrams/scripts/gen_file_tree_diagram.py \
  --root      <ABSOLUTE-PATH-TO/repo> \
  --canvas    http://127.0.0.1:3030 \
  --output    <ABSOLUTE-OUTPUT-PATH> \
  --max-depth 4 \
  --max-per-dir 12
```

Skips by default: `node_modules`, `__pycache__`, `.git`, `.venv`, `dist`, `build`, `target`, `.next`, hidden files. Pass `--include-hidden` to override.

For dirs with more children than `--max-per-dir`, the script appends a `… N more` row instead of cluttering the diagram.

### Path B — Architecture / sequence / state / flowchart / data flow / ER (LLM-driven)

This is where you compose from primitives. **Before writing any JSON, read both reference files** so you don't rediscover the gotchas:

- `~/.claude/skills/drawing-diagrams/references/canvas-api.md` — REST endpoints, element schemas, color palette, race-condition handling, canonicalization
- `~/.claude/skills/drawing-diagrams/references/layout-disciplines.md` — pitch, defensive sizing, cascade rule, column counts, verification checklist

**General loop for any LLM-driven type:**

1. **Understand the structure.** Architecture: read source files, identify modules, decide grouping. Sequence: list actors and message order. State machine: enumerate states + transitions. Flowchart: nail down decision points.
2. **Sketch a layout** mentally — what's where, how tall, what colors.
3. **Compute coordinates** following the layout disciplines (especially the cascade rule for stacked zones).
4. **POST to `/api/elements/batch`** with all elements.
5. **Poll-until-match** for the element count (see canvas-api.md race-condition section). **Do not sleep N seconds and hope.**
6. **Verify** containment, inter-zone gaps, arrow attachment.
7. **Snapshot + canonicalize** (`GET /api/elements`, strip VOLATILE fields).
8. **Tell the user** to open `http://127.0.0.1:3030` to see it.

### Type-specific tips

**Architecture diagram:**
- One labeled rectangle per module/service. Color by layer (frontend=blue `#a5d8ff`, logic=purple `#d0bfff`, data=teal `#c3fae8`, infrastructure=orange `#ffd8a8`).
- For 3-tier layouts: layer-based vertical stack (frontend top, services middle, data bottom). Use background zones (`opacity: 40`) for the layers.
- For 5+ services: spread horizontally within a layer; arrows show coupling.
- **Arrows must use full `startBinding`/`endBinding` with `fixedPoint`** — diagonals never snap with the simple `start`/`end` form.

**Sequence diagram:**
- One labeled rectangle per actor along the top (`y ≈ 80`).
- Below each actor, a dashed vertical arrow (`strokeStyle: "dashed", endArrowhead: null`) — the lifeline.
- Messages = horizontal labeled arrows between lifelines, sorted top→bottom by message order.
- Solid arrows for requests, dashed for responses.

**State machine:**
- Each state = ellipse (`type: "ellipse"`) with `label.text`.
- Transitions = labeled arrows.
- Start state = small filled black circle (filled ellipse); end state = double-bordered ellipse (two concentric ellipses).

**Flowchart:**
- Start/End = rounded rectangle (`roundness: {type: 3}`) with green/red fill.
- Process = regular rectangle.
- Decision = diamond (`type: "diamond"`).
- Yes/No branches = arrows with short `label.text`.

**Data flow:**
- Sources (left) → processors (middle) → sinks (right).
- Use teal (`#c3fae8`) for stores, blue (`#a5d8ff`) for sources, green (`#b2f2bb`) for sinks.

**ER / schema:**
- Each entity = a tall rectangle, entity name in a header row, fields below as smaller text elements OR row-rectangles.
- Cardinality on arrow labels: `"1 → *"`, `"1 → 1"`.

### Path C — Mermaid passthrough

User gave you Mermaid source? Skip composition entirely:

```bash
curl -X POST http://127.0.0.1:3030/api/elements/from-mermaid \
  -H "Content-Type: application/json" \
  -d '{"mermaid": "graph TD; A-->B; B-->C"}'
```

Canvas server uses `@excalidraw/mermaid-to-excalidraw` internally.

### Path D — Editing an existing canvas

To modify rather than redraw, PUT individual elements:

```bash
curl -X PUT http://127.0.0.1:3030/api/elements/<id> \
  -H "Content-Type: application/json" \
  -d '{"x": 200, "y": 300}'   # only fields you want to change
```

IDs preserved; bindings preserved. See `canvas-api.md` for full schema.

## Step 3 — Verify (mandatory)

Per `layout-disciplines.md` §11. Don't claim success without:

1. Count matches (poll-until-match)
2. All elements contained within their parent zones (for zone-based layouts)
3. Inter-zone gaps ≥ 20 px
4. Arrow endpoints visually attached (or you've consciously chosen to leave them floating)

The dep-map generator (`gen_deps_diagram.py`) runs these checks automatically. LLM-path you run them yourself — sample Python is in canvas-api.md and layout-disciplines.md.

## Step 4 — Snapshot (recommended)

```bash
curl -s http://127.0.0.1:3030/api/elements | python3 -c "
import json, sys
d = json.load(sys.stdin)
VOLATILE = {'createdAt', 'updatedAt', 'syncedAt', 'syncTimestamp',
            'seed', 'version', 'versionNonce', 'updated', 'source'}
for e in d['elements']:
    for k in list(e.keys()):
        if k in VOLATILE: del e[k]
print(json.dumps(d, indent=2, sort_keys=True))" > <output-path>.excalidraw.json
```

Canonicalized snapshots are byte-identical across runs, so they diff cleanly in git. Use this for "team docs that don't rot" workflows.

## Common mistakes

| Symptom | Fix |
|---|---|
| "canvas not reachable" | Step 1 didn't complete — check `lsof -i:3030` for conflict; restart canvas |
| Boxes overflow their zone | A wrapped label doubled box height; see defensive sizing in `layout-disciplines.md` §2 |
| Zones overlap each other | Cascade rule violated — when a zone grows, shift the row below by the same delta (§4) |
| Diagonal arrows float in space | Used simple `start`/`end` instead of full `startBinding`/`endBinding` with `fixedPoint`; see `canvas-api.md` §Arrow |
| Snapshot file = 40 bytes | Race condition between POST and GET — use poll-until-match, not `sleep N`; see `canvas-api.md` §Race condition |
| Two runs produce different JSON | Skipped canonicalization — strip the VOLATILE fields |
| `@remotion/*` lands in "Utils" | Dep map only: add a `video` bucket to `scripts/dep_rules.yaml` |
| Sequence diagram lifelines look wrong | Lifeline = arrow with `strokeStyle: "dashed"` and `endArrowhead: null`, NOT a regular line |
| Flowchart decisions look like rectangles | Use `type: "diamond"`, not `type: "rectangle"`, for decision nodes |

## When NOT to use

- **Image-output asks** (PNG, JPG of an already-drawn diagram). Use canvas's `POST /api/export/image` — but the browser tab must be open to render.
- **PDF reports.** Use the `markdown-pdf-with-diagrams` skill.
- **UI mockups / interface designs.** Use `design-shotgun` or `frontend-design`.
- **Non-visual outputs.** Just answer normally.

## Output

- **Live canvas** at `http://127.0.0.1:3030` (open in browser to view)
- **JSON snapshot** at the path you specified (canonicalized, ready to commit)
- **(Optional) PNG/SVG export** via `POST /api/export/image` while the canvas tab is open

## CI wiring (deps only, for now)

A ready-to-use GitHub Action template ships with this skill at `ci/.github/workflows/diagram-refresh.yml` (resolved path: `~/.claude/skills/drawing-diagrams/ci/.github/workflows/diagram-refresh.yml`). Drop it into the target repo's `.github/workflows/`, copy `scripts/gen_deps_diagram.py` + `scripts/dep_rules.yaml` into `.ci/diagrams/`, and the workflow regenerates the diagram on every relevant push and opens a PR if the snapshot changed.

A local dry-run script is also bundled: `ci/simulate_ci.sh PATH/to/target/repo`.

LLM-driven diagram types aren't CI-wired — they need an LLM in the loop. Manual regen for those.

## Provenance

Born from a long pilot that dogfooded the deterministic dep-map generator across multiple real `package.json` files. The reference files capture every gotcha caught during that work: arrow binding, wrap-overflow, cascade overlap, race condition, deterministic canonicalization. LLM-driven diagram types build on the same canvas API and the same disciplines.

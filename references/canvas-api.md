# Excalidraw Canvas REST API — Primitives Reference

The canvas server (yctimlin/mcp_excalidraw) listens at `http://127.0.0.1:3030`. Every diagram type — deterministic or LLM-synthesized — goes through these endpoints.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness check |
| `GET` | `/api/elements` | Full canvas state (`{success, elements, count}`) |
| `POST` | `/api/elements/batch` | Create many elements at once (body: `{elements: [...]}`) |
| `POST` | `/api/elements` | Create one element |
| `PUT` | `/api/elements/:id` | Update one element (partial JSON allowed) |
| `DELETE` | `/api/elements/:id` | Delete one element |
| `DELETE` | `/api/elements/clear` | Clear canvas |
| `POST` | `/api/elements/from-mermaid` | Convert Mermaid source to Excalidraw elements |
| `POST` | `/api/viewport` | Set viewport (pan/zoom) |
| `POST` | `/api/export/image` | Request PNG export (browser must be open) |

## Element types

### Rectangle (the workhorse)

```json
{
  "type": "rectangle",
  "id": "stable-id",
  "x": 100, "y": 100, "width": 200, "height": 60,
  "backgroundColor": "#a5d8ff",
  "fillStyle": "solid",
  "strokeColor": "#4a9eed",
  "strokeWidth": 2,
  "roundness": {"type": 3},
  "label": {"text": "Auth Service"}
}
```

- `label.text` auto-centers inside the box. **Avoid using a separate text element for box labels.**
- Long labels wrap to 2 lines and **double the box height silently** — plan for it (see `layout-disciplines.md`).

### Ellipse, Diamond — same fields as rectangle, just change `type`.

### Standalone text (titles, annotations only — not for labeling shapes)

```json
{
  "type": "text",
  "id": "title",
  "x": 300, "y": 20,
  "text": "System Architecture",
  "fontSize": 24,
  "strokeColor": "#1e1e1e"
}
```

- `x, y` is the **top-left** of the text bounding box.
- To center text horizontally at `cx`: `x = cx - len(text) * fontSize * 0.5`.

### Arrow (the connector)

```json
{
  "type": "arrow",
  "id": "a1",
  "x": 220, "y": 130,
  "width": 80, "height": 0,
  "points": [[0, 0], [80, 0]],
  "endArrowhead": "arrow",
  "startBinding": {"elementId": "box_a", "fixedPoint": [1, 0.5], "focus": 0, "gap": 1},
  "endBinding":   {"elementId": "box_b", "fixedPoint": [0, 0.5], "focus": 0, "gap": 1}
}
```

- `x, y` = start point in scene coords.
- `points` = `[[dx0, dy0], [dx1, dy1]]` relative to `x, y`. The last point should equal `[width, height]`.
- `endArrowhead`: `"arrow" | "bar" | "dot" | "triangle" | null`
- **For diagonal arrows, you MUST use `startBinding` and `endBinding`** with `fixedPoint` — the simpler `start: {id}` / `end: {id}` only auto-snaps for axis-aligned arrows.

### `fixedPoint` reference

Coordinates within the bound element, range 0–1:

| Where | Value |
|---|---|
| top-center | `[0.5, 0]` |
| bottom-center | `[0.5, 1]` |
| left-center | `[0, 0.5]` |
| right-center | `[1, 0.5]` |
| top-left corner | `[0, 0]` |
| For fan-in to one node from multiple sources, use asymmetric points (`[0.25, 0]` and `[0.75, 0]`) so arrows don't overlap |

### Labeled arrow

Add `"label": {"text": "tools/call"}` to any arrow. Caveat: long arrow labels look cramped on short arrows.

## Color palette (use consistently)

### Solid pastels (use as `backgroundColor` for boxes; `fillStyle: "solid"`)

| Hex | Use |
|---|---|
| `#a5d8ff` | Inputs, sources, primary nodes (light blue) |
| `#b2f2bb` | Success, output, completed (light green) |
| `#ffd8a8` | Warning, pending, external (light orange) |
| `#d0bfff` | Processing, middleware, special (light purple) |
| `#ffc9c9` | Error, critical, alerts (light red) |
| `#fff3bf` | Notes, decisions (light yellow) |
| `#c3fae8` | Storage, data, memory (light teal) |
| `#eebefa` | Analytics, metrics (light pink) |

### Stroke colors (matching, slightly darker)

| Pastel | Stroke |
|---|---|
| `#a5d8ff` (blue) | `#4a9eed` |
| `#b2f2bb` (green) | `#22c55e` |
| `#ffd8a8` (orange) | `#f59e0b` |
| `#d0bfff` (purple) | `#8b5cf6` |
| `#c3fae8` (teal) | `#06b6d4` |

### Background zones (use `opacity: 40` for layered diagrams)

| Hex | Use |
|---|---|
| `#dbe4ff` | Blue zone — UI / frontend layer |
| `#e5dbff` | Purple zone — Logic / agent layer |
| `#d3f9d8` | Green zone — Data / tool layer |
| `#fff3bf` | Yellow zone — Notes / utils |

### Text on light backgrounds

Minimum: `#757575` (anything lighter is invisible). Use darker zone-matched colors for headers:
- Blue zone header: `#2563eb`
- Green zone header: `#15803d`
- Orange zone header: `#b45309`
- Purple zone header: `#6d28d9`
- Teal zone header: `#0e7490`

## Verification pattern

After any POST, **verify before declaring done**:

```python
import json
from urllib.request import urlopen
state = json.load(urlopen("http://127.0.0.1:3030/api/elements"))
# 1. count matches what you posted?
# 2. every dep-box inside its parent zone?
# 3. every pair of zones has ≥20px gap?
```

The skill's `gen_deps_diagram.py` shows a complete verification implementation.

## Race condition (important)

POST returns success **before** the WebSocket sync propagates to subsequent GETs. After POST, **poll** `GET /api/elements` until `count` matches expected, with a timeout. Don't `sleep N` — that's flaky.

```python
def wait_for_count(url, expected, timeout_s=3):
    import time
    from urllib.request import urlopen
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = json.load(urlopen(f"{url}/api/elements"))
            if r["count"] == expected: return True
        except: pass
        time.sleep(0.1)
    return False
```

## Canonical snapshot for git

When writing a snapshot to disk for version control, **strip volatile fields** so diffs reflect logical changes only:

```python
VOLATILE = {"createdAt", "updatedAt", "syncedAt", "syncTimestamp",
            "seed", "version", "versionNonce", "updated", "source"}
for e in snapshot["elements"]:
    for k in list(e.keys()):
        if k in VOLATILE:
            del e[k]
```

Two consecutive renders of the same content should produce **byte-identical** canonicalized JSON.

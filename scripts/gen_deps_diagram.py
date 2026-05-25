#!/usr/bin/env python3
"""
gen_deps_diagram.py — Read a package.json + rules YAML, generate the
Excalidraw dep-map JSON, POST it to a running canvas server, then
return the canvas snapshot. Idempotent: same inputs → same output.

Usage:
    python3 gen_deps_diagram.py \
        --package PATH/to/package.json \
        --rules   ci/dep_rules.yaml \
        --canvas  http://127.0.0.1:3030 \
        --output  docs/diagrams/deps.excalidraw.json

Exit codes:
    0   success
    1   verification failure (containment or gap)
    2   canvas unreachable
    3   I/O or schema error
"""
from __future__ import annotations
import argparse
import json
import re
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


# ---------- I/O ----------

def load_package_json(path: Path) -> tuple[str, str, dict, dict]:
    p = json.loads(path.read_text())
    return p.get("name", "?"), p.get("version", "?"), p.get("dependencies", {}), p.get("devDependencies", {})


def load_rules(path: Path) -> dict:
    text = path.read_text()
    # Tiny YAML parser — we avoid pyyaml dependency in CI for speed
    try:
        import yaml  # type: ignore
        return yaml.safe_load(text)
    except ImportError:
        # Fallback to JSON if user provides .json rules
        if path.suffix == ".json":
            return json.loads(text)
        sys.stderr.write("ERROR: PyYAML not installed. Run: pip install pyyaml\n")
        sys.exit(3)


# ---------- categorization ----------

def match_bucket(pkg: str, buckets: list[dict]) -> str | None:
    for b in buckets:
        for pat in b.get("patterns", []) or []:
            if re.search(pat, pkg):
                return b["id"]
    return None


def categorize(deps: dict, dev_deps: dict, buckets: list[dict]) -> dict[str, list[str]]:
    """
    Runtime deps (package.json#dependencies) are matched against bucket patterns;
    unmatched fall through to "utils".
    devDependencies ALWAYS go to "dev" regardless of pattern — they're declared
    as dev by the author, and respecting that declaration beats semantic guessing.
    (e.g. @docusaurus/types matches ^@docusaurus/ but is declared devDep → "dev")
    """
    out: dict[str, list[str]] = {b["id"]: [] for b in buckets}
    for pkg in sorted(deps.keys()):
        bid = match_bucket(pkg, buckets)
        if bid is None:
            bid = "utils"  # unmatched runtime → utils
        out[bid].append(pkg)
    for pkg in sorted(dev_deps.keys()):
        out["dev"].append(pkg)  # respect package.json declaration
    return out


def apply_collapse(categorized: dict[str, list[str]], rules: list[dict]) -> dict[str, list[str]]:
    if not rules:
        return categorized
    for bucket_id, pkgs in list(categorized.items()):
        for rule in rules:
            pat = rule["pattern"]
            members = [p for p in pkgs if re.search(pat, p)]
            min_n = rule.get("min_members", 2)
            if len(members) < min_n:
                continue
            # Build collapsed label
            tmpl = rule["label_template"]
            label = tmpl.format(
                n=len(members),
                members=", ".join(p.split("/")[-1] for p in members),
            )
            # Replace the member entries with a single collapsed entry
            categorized[bucket_id] = [p for p in pkgs if p not in members] + [label]
            pkgs = categorized[bucket_id]  # refresh for subsequent rules
    return categorized


# ---------- layout ----------

def predict_wrap_lines(label: str, box_width: int, font_size: int = 20) -> int:
    """Approximate how many lines a label will wrap to inside an Excalidraw labeled
    box. Conservative — overestimates rather than underestimates to avoid overflow.

    At fontSize 20, average char width is ~12px including ligature padding. The
    container reserves ~20px for left/right padding. So:
      chars_per_line = floor((box_width - 20) / 12)
      lines          = ceil(len(label) / chars_per_line)
    """
    chars_per_line = max(1, (box_width - 20) // 12)
    return max(1, -(-len(label) // chars_per_line))  # ceil division


def predicted_box_height(label: str, box_width: int, font_size: int = 20,
                         default: int = 60) -> int:
    """Box height needed to contain a label that may wrap to multiple lines.
    Each text line at fontSize 20 is ~25px (lineHeight 1.25). Add 10px vertical
    padding around the text. Single- and two-line labels share the same
    default (60); 3+ lines scale up.
    """
    lines = predict_wrap_lines(label, box_width, font_size)
    if lines <= 2:
        return default
    return lines * 25 + 10


def build_elements(name: str, version: str, categorized: dict, buckets: list[dict], lay: dict) -> list[dict]:
    populated = [b for b in buckets if categorized.get(b["id"])]
    elements = []
    canvas_w = lay["canvas_width"]
    zone_x = lay["zone_x"]
    title = f"{name}@{version}"

    # Title centered roughly
    approx_title_w = len(title) * 14
    title_x = zone_x + (canvas_w - approx_title_w) // 2
    elements.append({
        "type": "text", "id": "title",
        "x": title_x, "y": lay["title_y"],
        "text": title, "fontSize": 24, "strokeColor": "#1e1e1e",
    })

    cur_y = lay["title_y"] + 50   # leave room below the title
    for b in populated:
        deps = categorized[b["id"]]
        bid = b["id"]
        n = len(deps)
        cols = min(lay["cols_per_zone"], max(1, n))
        if n <= 3:
            cols = 1
        elif n <= 6:
            cols = 2
        else:
            cols = 3
        # row counts (left-to-right fill, balanced)
        base = n // cols
        rem = n % cols
        col_counts = [base + (1 if i < rem else 0) for i in range(cols)]
        n_rows = max(col_counts) if col_counts else 1

        # Compute col x positions FIRST so we can predict per-label box heights
        usable = canvas_w - 2 * lay["zone_padding"]
        col_w = (usable - lay["internal_gutter"] * (cols - 1)) // cols
        col_xs = [zone_x + lay["zone_padding"] + i * (col_w + lay["internal_gutter"]) for i in range(cols)]

        # Predict each box's height (defensive sizing handles 3+ line wraps)
        # Layout is column-major: column `ci` holds deps at indices
        # [sum(col_counts[:ci]) .. sum(col_counts[:ci+1])]
        box_heights = {}  # (col, row) -> height
        for ci, cn in enumerate(col_counts):
            for ri in range(cn):
                idx = sum(col_counts[:ci]) + ri
                if idx >= n:
                    continue
                box_heights[(ci, ri)] = predicted_box_height(
                    deps[idx], col_w, default=lay["box_height_default"]
                )

        # For each row, the row-height = max of all box heights in that row
        # (row-aligned grid look; uniform horizontal baselines)
        row_heights = []
        for ri in range(n_rows):
            heights_this_row = [box_heights[(ci, ri)] for ci in range(cols)
                                if (ci, ri) in box_heights]
            row_heights.append(max(heights_this_row) if heights_this_row else lay["box_height_default"])

        # Cumulative y offset per row, relative to first row's top
        intra_row_gap = max(10, lay["pitch"] - lay["box_height_default"])  # 10px default
        row_y_offsets = [0]
        for ri in range(1, n_rows):
            row_y_offsets.append(row_y_offsets[-1] + row_heights[ri - 1] + intra_row_gap)

        # Zone height = header + sum(row heights) + (n_rows-1)*gap + safety pad
        zone_h = (lay["header_height"]
                  + sum(row_heights)
                  + (n_rows - 1) * intra_row_gap
                  + lay["bottom_padding"])

        # Zone background
        elements.append({
            "type": "rectangle", "id": f"z_{bid}",
            "x": zone_x, "y": cur_y,
            "width": canvas_w, "height": zone_h,
            "backgroundColor": b["color_zone_bg"],
            "fillStyle": "solid",
            "strokeColor": b["color_zone_stroke"],
            "strokeWidth": 1, "opacity": 40,
            "roundness": {"type": 3},
        })
        # Header
        elements.append({
            "type": "text", "id": f"h_{bid}",
            "x": zone_x + 20, "y": cur_y + 15,
            "text": b["title"], "fontSize": 18,
            "strokeColor": b["color_header"],
        })
        # Place deps (column-major fill) using dynamic per-row heights
        first_box_y = cur_y + 45  # y of the first row's top
        idx = 0
        for ci, cn in enumerate(col_counts):
            for ri in range(cn):
                if idx >= n:
                    break
                label = deps[idx]
                slug = re.sub(r"[^a-zA-Z0-9]", "_", label)[:24]
                elements.append({
                    "type": "rectangle",
                    "id": f"d_{bid}_{idx}_{slug}",
                    "x": col_xs[ci],
                    "y": first_box_y + row_y_offsets[ri],
                    "width": col_w,
                    "height": row_heights[ri],
                    "backgroundColor": b["color_box_bg"],
                    "fillStyle": "solid",
                    "strokeColor": b["color_box_stroke"],
                    "strokeWidth": 1,
                    "roundness": {"type": 3},
                    "label": {"text": label},
                })
                idx += 1
        cur_y += zone_h + lay["row_gap"]

    return elements


# ---------- canvas interaction ----------

def canvas_request(method: str, url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"} if body else {}
    req = Request(url, data=data, headers=headers, method=method)
    with urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def wait_for_count(canvas: str, expected: int, timeout_s: float = 3.0) -> bool:
    deadline = time.time() + timeout_s
    last = -1
    while time.time() < deadline:
        try:
            r = canvas_request("GET", f"{canvas}/api/elements")
            last = r.get("count", -1)
            if last == expected:
                return True
        except URLError:
            pass
        time.sleep(0.1)
    sys.stderr.write(f"WARN: canvas count never reached {expected} (last={last})\n")
    return False


# ---------- verification ----------

def verify(canvas: str) -> tuple[bool, list[str]]:
    r = canvas_request("GET", f"{canvas}/api/elements")
    els = {e["id"]: e for e in r["elements"] if e.get("id")}
    zones = [(eid, e) for eid, e in els.items() if eid.startswith("z_")]
    deps = [(eid, e) for eid, e in els.items() if eid.startswith("d_")]
    problems = []
    # Containment — find each dep's zone by coordinate inclusion (robust to id format changes)
    for did, d in deps:
        bx, by, bw, bh = d["x"], d["y"], d.get("width", 0), d.get("height", 0)
        cx, cy = bx + bw / 2, by + bh / 2
        parent_zone = None
        for zid, z in zones:
            if z["x"] <= cx <= z["x"] + z["width"] and z["y"] <= cy <= z["y"] + z["height"]:
                parent_zone = (zid, z)
                break
        if not parent_zone:
            problems.append(f"dep {did} has no containing zone")
            continue
        zid, z = parent_zone
        zL, zT, zR, zB = z["x"], z["y"], z["x"] + z["width"], z["y"] + z["height"]
        if bx < zL or by < zT or bx + bw > zR or by + bh > zB:
            problems.append(f"dep {did} overflows zone {zid}")
    # Gaps
    sorted_zones = sorted(zones, key=lambda kv: kv[1]["y"])
    for (id1, z1), (id2, z2) in zip(sorted_zones, sorted_zones[1:]):
        gap = z2["y"] - (z1["y"] + z1["height"])
        if gap < 20:
            problems.append(f"zone {id1} → {id2}: gap={gap}px (<20)")
    return (len(problems) == 0, problems)


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--package", required=True, type=Path)
    ap.add_argument("--rules", required=True, type=Path)
    ap.add_argument("--canvas", default="http://127.0.0.1:3030")
    ap.add_argument("--output", type=Path, default=None,
                    help="Path to write canvas snapshot JSON (after POST + verify)")
    ap.add_argument("--clear", action="store_true", default=True)
    args = ap.parse_args()

    name, version, deps, dev_deps = load_package_json(args.package)
    rules = load_rules(args.rules)
    cat = categorize(deps, dev_deps, rules["buckets"])
    cat = apply_collapse(cat, rules.get("collapse_families", []))
    elements = build_elements(name, version, cat, rules["buckets"], rules["layout"])

    # Probe canvas
    try:
        canvas_request("GET", f"{args.canvas}/health")
    except URLError as e:
        sys.stderr.write(f"ERROR: canvas at {args.canvas} unreachable: {e}\n")
        sys.exit(2)

    if args.clear:
        canvas_request("DELETE", f"{args.canvas}/api/elements/clear")

    resp = canvas_request("POST", f"{args.canvas}/api/elements/batch", {"elements": elements})
    expected = resp["count"]
    print(f"[gen] POST accepted, expected count = {expected}")

    if not wait_for_count(args.canvas, expected):
        sys.stderr.write("WARN: canvas sync timeout (continuing anyway)\n")

    ok, problems = verify(args.canvas)
    if not ok:
        print("VERIFICATION FAILED:", file=sys.stderr)
        for p in problems:
            print("  ❌", p, file=sys.stderr)
        sys.exit(1)
    print(f"[gen] verification ✅ ({expected} elements, all contained, all gaps ≥20px)")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        snapshot = canvas_request("GET", f"{args.canvas}/api/elements")
        # Canonicalize: strip volatile fields so git diff reflects real changes only.
        VOLATILE = {"createdAt", "updatedAt", "syncedAt", "syncTimestamp",
                    "seed", "version", "versionNonce", "updated", "source"}
        for e in snapshot.get("elements", []):
            for k in list(e.keys()):
                if k in VOLATILE:
                    del e[k]
        args.output.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
        print(f"[gen] snapshot written: {args.output} ({args.output.stat().st_size} bytes, canonicalized)")


if __name__ == "__main__":
    main()

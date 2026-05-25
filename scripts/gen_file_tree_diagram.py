#!/usr/bin/env python3
"""
gen_file_tree_diagram.py — Render a directory's structure as an indented
hand-drawn tree on a running Excalidraw canvas.

Walks a directory up to --max-depth, filters out junk (.git, node_modules,
__pycache__, .venv, dist, build, target, .next, hidden files), draws each
entry as an indented labeled box. Directories get a distinctive color,
files get extension-based colors.

Usage:
    gen_file_tree_diagram.py \
        --root  /path/to/project \
        --canvas http://127.0.0.1:3030 \
        --output snapshot.excalidraw.json

Optional:
    --max-depth N           limit depth (default 4)
    --max-per-dir N         truncate dirs with > N entries (default 12)
    --include-hidden        show .hidden entries (off by default)

Exit codes:
    0 success
    1 verification failure
    2 canvas unreachable
    3 invalid root / arg
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


# ---------- defaults ----------

SKIP_DIRS = {
    "node_modules", "__pycache__", ".git", ".venv", "venv",
    "dist", "build", "target", ".next", ".turbo", ".cache",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
    "site-packages", ".idea", ".vscode",
}
SKIP_FILE_PATTERNS = (".pyc", ".pyo", ".DS_Store")


# Extension → (zone bg, stroke) for file-type coloring
EXT_COLORS: dict[str, tuple[str, str]] = {
    # Code
    ".py":   ("#fff3bf", "#ca8a04"),    # yellow — Python
    ".js":   ("#fff3bf", "#ca8a04"),    # yellow — JS
    ".ts":   ("#a5d8ff", "#4a9eed"),    # blue — TypeScript
    ".tsx":  ("#a5d8ff", "#4a9eed"),
    ".jsx":  ("#a5d8ff", "#4a9eed"),
    ".rs":   ("#ffd8a8", "#f59e0b"),    # orange — Rust
    ".go":   ("#c3fae8", "#06b6d4"),    # cyan — Go
    ".rb":   ("#ffc9c9", "#ef4444"),    # red — Ruby
    ".java": ("#ffd8a8", "#f59e0b"),
    ".kt":   ("#d0bfff", "#8b5cf6"),    # purple — Kotlin
    ".swift":("#ffd8a8", "#f59e0b"),
    ".cpp":  ("#ffd8a8", "#f59e0b"),
    ".c":    ("#ffd8a8", "#f59e0b"),
    ".h":    ("#ffe1b3", "#f59e0b"),
    ".sh":   ("#b2f2bb", "#22c55e"),    # green — shell
    # Web / markup
    ".html": ("#ffd8a8", "#f59e0b"),
    ".css":  ("#eebefa", "#a21caf"),    # pink — CSS
    ".scss": ("#eebefa", "#a21caf"),
    ".md":   ("#c3fae8", "#0e7490"),    # teal — markdown
    ".rst":  ("#c3fae8", "#0e7490"),
    ".txt":  ("#e5e5e5", "#737373"),    # gray
    # Config
    ".json": ("#d0bfff", "#8b5cf6"),    # purple — JSON
    ".yaml": ("#d0bfff", "#8b5cf6"),
    ".yml":  ("#d0bfff", "#8b5cf6"),
    ".toml": ("#d0bfff", "#8b5cf6"),
    ".env":  ("#d0bfff", "#8b5cf6"),
    ".ini":  ("#d0bfff", "#8b5cf6"),
    ".cfg":  ("#d0bfff", "#8b5cf6"),
    # Images / assets
    ".png":  ("#fbcfe8", "#be185d"),    # pink — images
    ".jpg":  ("#fbcfe8", "#be185d"),
    ".jpeg": ("#fbcfe8", "#be185d"),
    ".svg":  ("#fbcfe8", "#be185d"),
    ".gif":  ("#fbcfe8", "#be185d"),
    ".webp": ("#fbcfe8", "#be185d"),
}
DIR_COLORS = ("#a5d8ff", "#4a9eed")          # directories — bold blue
SPECIAL_FILE_COLORS = ("#fef3c7", "#d97706") # readme / license / dockerfile
DEFAULT_FILE_COLORS = ("#f5f5f5", "#737373") # unknown extension


# ---------- walking ----------

def walk_tree(root: Path, max_depth: int, max_per_dir: int,
              include_hidden: bool) -> list[dict]:
    """Walk `root` to `max_depth` and return a flat list of dicts:
       {depth, name, type, kind, truncated_count}.
    `truncated_count` is set on the last entry of a truncated dir."""

    entries: list[dict] = []

    def _is_skipped(p: Path) -> bool:
        if p.name in SKIP_DIRS:
            return True
        if not include_hidden and p.name.startswith("."):
            return True
        return False

    def _is_skipped_file(p: Path) -> bool:
        if p.name in SKIP_DIRS:
            return True
        if not include_hidden and p.name.startswith("."):
            return True
        for pat in SKIP_FILE_PATTERNS:
            if p.name.endswith(pat):
                return True
        return False

    def _recurse(p: Path, depth: int):
        try:
            children = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except (PermissionError, OSError):
            return
        # Filter
        visible = []
        for c in children:
            if c.is_dir() and _is_skipped(c):
                continue
            if c.is_file() and _is_skipped_file(c):
                continue
            visible.append(c)

        truncated = 0
        if len(visible) > max_per_dir:
            truncated = len(visible) - max_per_dir
            visible = visible[:max_per_dir]

        for c in visible:
            entries.append({
                "depth": depth,
                "name": c.name + ("/" if c.is_dir() else ""),
                "kind": "dir" if c.is_dir() else "file",
                "ext": c.suffix.lower() if c.is_file() else "",
                "is_special": c.is_file() and c.name.lower() in {
                    "readme.md", "license", "license.md", "license.txt",
                    "dockerfile", "makefile", ".gitignore", "package.json",
                    "pyproject.toml", "cargo.toml", "go.mod"},
            })
            if c.is_dir() and depth < max_depth:
                _recurse(c, depth + 1)
        if truncated:
            entries.append({
                "depth": depth,
                "name": f"… {truncated} more",
                "kind": "ellipsis",
                "ext": "",
                "is_special": False,
            })

    # Root entry first
    entries.append({
        "depth": 0,
        "name": root.name + "/",
        "kind": "dir",
        "ext": "",
        "is_special": False,
    })
    _recurse(root, 1)
    return entries


# ---------- layout ----------

def predict_wrap_lines(label: str, box_width: int) -> int:
    chars_per_line = max(1, (box_width - 20) // 12)
    return max(1, -(-len(label) // chars_per_line))


def colors_for(entry: dict) -> tuple[str, str]:
    if entry["kind"] == "dir":
        return DIR_COLORS
    if entry["kind"] == "ellipsis":
        return ("#e5e5e5", "#9ca3af")
    if entry["is_special"]:
        return SPECIAL_FILE_COLORS
    return EXT_COLORS.get(entry["ext"], DEFAULT_FILE_COLORS)


def build_elements(root_path: Path, entries: list[dict],
                   canvas_w: int = 720) -> list[dict]:
    elements = []

    # Title
    title = f"{root_path.name}/  —  file tree"
    title_x = 40 + (canvas_w - len(title) * 14) // 2
    elements.append({
        "type": "text", "id": "title",
        "x": max(40, title_x), "y": 100,
        "text": title, "fontSize": 22, "strokeColor": "#1e1e1e",
    })

    # Layout knobs
    base_x = 60
    base_y = 160
    indent = 28
    row_gap = 8
    max_label_width = 320  # box width per row

    cur_y = base_y
    for idx, e in enumerate(entries):
        x = base_x + e["depth"] * indent
        # Each row's width adapts to available space (canvas right edge minus x)
        box_w = min(max_label_width, canvas_w - (x - 40) - 40)
        lines = predict_wrap_lines(e["name"], box_w)
        h = 30 if lines <= 1 else lines * 25 + 10

        bg, stroke = colors_for(e)
        opacity = 60 if e["kind"] == "ellipsis" else 100
        elem = {
            "type": "rectangle",
            "id": f"t_{idx}",
            "x": x, "y": cur_y, "width": box_w, "height": h,
            "backgroundColor": bg, "fillStyle": "solid",
            "strokeColor": stroke, "strokeWidth": 1,
            "opacity": opacity,
            "roundness": {"type": 3},
            "label": {"text": e["name"]},
        }
        elements.append(elem)
        cur_y += h + row_gap

    # Footer summary
    n_dirs = sum(1 for e in entries if e["kind"] == "dir")
    n_files = sum(1 for e in entries if e["kind"] == "file")
    n_truncated = sum(1 for e in entries if e["kind"] == "ellipsis")
    summary = f"{n_dirs} dirs, {n_files} files"
    if n_truncated:
        summary += f" ({n_truncated} dirs truncated)"
    elements.append({
        "type": "text", "id": "summary",
        "x": base_x, "y": cur_y + 10,
        "text": summary, "fontSize": 14, "strokeColor": "#757575",
    })
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


def verify_no_overlap(canvas: str) -> tuple[bool, list[str]]:
    """Tree items shouldn't overlap each other vertically."""
    r = canvas_request("GET", f"{canvas}/api/elements")
    items = sorted(
        [e for e in r["elements"] if (e.get("id") or "").startswith("t_")],
        key=lambda e: e["y"]
    )
    problems = []
    for a, b in zip(items, items[1:]):
        a_bottom = a["y"] + a.get("height", 0)
        b_top = b["y"]
        if a_bottom > b_top:
            problems.append(f"overlap between {a.get('id')} and {b.get('id')}: "
                            f"a_bottom={a_bottom}, b_top={b_top}")
    return (len(problems) == 0, problems)


# ---------- main ----------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, type=Path,
                    help="Directory to visualize")
    ap.add_argument("--canvas", default="http://127.0.0.1:3030")
    ap.add_argument("--output", type=Path, default=None)
    ap.add_argument("--max-depth", type=int, default=4)
    ap.add_argument("--max-per-dir", type=int, default=12)
    ap.add_argument("--include-hidden", action="store_true",
                    help="Show .hidden files and directories")
    ap.add_argument("--clear", action="store_true", default=True)
    args = ap.parse_args()

    root = args.root.resolve()
    if not root.is_dir():
        sys.stderr.write(f"ERROR: --root '{root}' is not a directory\n")
        sys.exit(3)

    entries = walk_tree(root, args.max_depth, args.max_per_dir, args.include_hidden)
    print(f"[tree] walked {root.name}: {len(entries)} entries (depth ≤ {args.max_depth})")

    elements = build_elements(root, entries)

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
    print(f"[tree] POST accepted, expected count = {expected}")

    if not wait_for_count(args.canvas, expected):
        sys.stderr.write("WARN: canvas sync timeout (continuing anyway)\n")

    ok, problems = verify_no_overlap(args.canvas)
    if not ok:
        print("VERIFICATION FAILED:", file=sys.stderr)
        for p in problems:
            print("  ❌", p, file=sys.stderr)
        sys.exit(1)
    print(f"[tree] verification ✅ ({expected} elements, no overlaps)")

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        snapshot = canvas_request("GET", f"{args.canvas}/api/elements")
        VOLATILE = {"createdAt", "updatedAt", "syncedAt", "syncTimestamp",
                    "seed", "version", "versionNonce", "updated", "source"}
        for e in snapshot.get("elements", []):
            for k in list(e.keys()):
                if k in VOLATILE:
                    del e[k]
        args.output.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
        print(f"[tree] snapshot written: {args.output} ({args.output.stat().st_size} bytes)")


if __name__ == "__main__":
    main()

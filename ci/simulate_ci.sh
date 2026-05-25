#!/usr/bin/env bash
#
# simulate_ci.sh — Dry-run the GitHub Action workflow locally.
#
# Runs the same steps as .github/workflows/diagram-refresh.yml, minus the
# git push and PR creation. Use this to validate the CI loop end-to-end
# before wiring it into an actual repo.
#
# Usage:
#   ./simulate_ci.sh PATH/to/target/repo
#
# What it does:
#   1. Asserts canvas server is running on $CANVAS_URL
#   2. Reads target repo's package.json
#   3. Runs gen_deps_diagram.py against it
#   4. Writes docs/diagrams/deps.excalidraw.json in target repo
#   5. Diffs against any previously-committed version
#   6. Reports "would open PR" or "no change" — does NOT actually commit or push

set -euo pipefail

CANVAS_URL="${CANVAS_URL:-http://127.0.0.1:3030}"
CI_DIR="$(cd "$(dirname "$0")" && pwd)"

TARGET="${1:-}"
if [[ -z "$TARGET" || ! -f "$TARGET/package.json" ]]; then
  echo "Usage: $0 PATH/to/target/repo" >&2
  echo "  (target must have a package.json)" >&2
  exit 1
fi

TARGET="$(cd "$TARGET" && pwd)"
echo "=== Simulating CI for: $TARGET ==="
echo

# Step 1: canvas reachable?
if ! curl -sf "$CANVAS_URL/health" >/dev/null; then
  echo "ERROR: canvas server not reachable at $CANVAS_URL" >&2
  exit 2
fi
echo "[step 1/5] canvas server reachable ✓"

# Step 2: generate diagram
SNAPSHOT="$TARGET/docs/diagrams/deps.excalidraw.json"
mkdir -p "$(dirname "$SNAPSHOT")"

echo "[step 2/5] running generator..."
python3 "$CI_DIR/gen_deps_diagram.py" \
  --package "$TARGET/package.json" \
  --rules   "$CI_DIR/dep_rules.yaml" \
  --canvas  "$CANVAS_URL" \
  --output  "$SNAPSHOT"

# Step 3: diff against committed version
echo
echo "[step 3/5] diff check..."
cd "$TARGET"
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "  (not a git repo — skipping diff)"
  CHANGED="unknown"
elif ! git ls-files --error-unmatch "$SNAPSHOT" >/dev/null 2>&1; then
  echo "  (no previously-committed snapshot — would be first-time addition)"
  CHANGED="true"
elif git diff --quiet "$SNAPSHOT"; then
  echo "  no changes vs committed version"
  CHANGED="false"
else
  echo "  changed vs committed version:"
  git diff --stat "$SNAPSHOT"
  CHANGED="true"
fi

# Step 4: would-open-PR
echo
echo "[step 4/5] PR decision..."
if [[ "$CHANGED" == "true" ]]; then
  echo "  ✓ would open PR: 'docs(diagrams): refresh dependency map'"
elif [[ "$CHANGED" == "false" ]]; then
  echo "  ✓ no PR needed — diagram is current"
else
  echo "  ? non-git target — cannot decide"
fi

# Step 5: summary
echo
echo "[step 5/5] summary"
echo "  target:     $TARGET"
echo "  package:    $TARGET/package.json"
echo "  snapshot:   $SNAPSHOT ($(wc -c < "$SNAPSHOT") bytes)"
echo "  canvas URL: $CANVAS_URL (still showing the generated diagram for visual inspection)"
echo
echo "  Inspect by:"
echo "    open $CANVAS_URL"
echo "    jq '.count, .elements | length' < $SNAPSHOT"
echo "  Roll back changes:"
echo "    cd $TARGET && git restore docs/diagrams/deps.excalidraw.json"
echo
echo "=== Simulation complete ==="

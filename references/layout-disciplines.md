# Layout Disciplines for Excalidraw Canvas

Hard-won rules from drawing 4 real codebases in the original dep-diagram skill's dogfood. **Read this before placing any rectangles.** Skipping these is how diagrams end up overflowing zones, overlapping rows, or being unreadable.

## 1. Pitch & breathing room

- **Default box height:** 30 px for known-short labels (≤15 chars at fontSize 20)
- **Pitch (vertical distance between box tops):** **40 px minimum**. Anything less than 10 px gap between boxes reads as "no breathing room."
- **Horizontal gutter between columns:** **20 px**.
- **Zone-internal padding:** **20 px on each side** of any content.

## 2. Defensive sizing (the wrap-overflow trap)

Excalidraw silently expands a labeled box's height when its label text wraps. A label that wraps to 2 lines makes the box ~60px tall; a 3-line wrap makes it ~85px; a 4-line wrap makes it ~110px. This breaks naive zone-height math AND any layout that assumes uniform row pitch.

**Rules:**
- A label fits on one line if `len(text) * 12 ≤ box_width - 20` at fontSize 20. Predict line count with `ceil(len(text) / floor((box_width - 20) / 12))`.
- **Per-label height formula** (use this exactly):
  ```
  lines = predict_wrap_lines(label, box_width)
  box_height = 60 if lines ≤ 2 else lines * 25 + 10
  ```
  (Each text line ≈ 25px at fontSize 20 with lineHeight 1.25, plus 10px padding.)
- **Row-aligned grid layout** — when boxes sit in a multi-column grid, every box in row R should use the SAME height: `row_height = max(box_height(label) for label in row R)`. Mixed-height columns produce broken visual grids.
- **Dynamic per-row y offsets** — DON'T use uniform pitch. Compute each row's y based on the cumulative height of previous rows:
  ```
  row_y[0] = first_box_y
  row_y[i] = row_y[i-1] + row_height[i-1] + intra_row_gap   # 10px gap between rows
  ```
- **Zone height** = `header_h (25) + sum(row_heights) + (n_rows - 1) * 10 + bottom_pad (30)`.
- **Always verify** containment after POST. Catch wraps you didn't predict.

This is what `scripts/gen_deps_diagram.py` does — see `predict_wrap_lines()` and `predicted_box_height()` for reference implementations.

## 3. Zone height formula

```
n_rows         = ceil(deps_in_zone / cols)
max_row_height = 60 if any wrap predicted else 30
pitch          = 40 (for 30-tall boxes) or 70 (for 60-tall boxes)
zone_height    = 25 (header) + n_rows × pitch + 30 (safety pad)
```

The **30 px safety pad** at the bottom catches wrap-related auto-expansion you missed in prediction.

## 4. Layout is a cascade, not a grid (critical)

Treating each zone's geometry as independent breaks the moment any zone grows. Mental model:

1. Compute every zone's height first.
2. Compute each row's top-y as `prev_row_bottom + 20` (the inter-row gap).
3. Then place zones at those y values.
4. **When fixing an overflow post-hoc, fix the height AND shift the row below by the same delta.**

```
y = title_y_end + 20
for zone in vertical_order:
    zone.y = y
    y += zone.height + 20
```

## 5. Layout shapes by zone count

| Zones | Recommended shape |
|---|---|
| 1 | Full-width single row |
| 2 | Two stacked full-width rows OR side-by-side |
| 3 | One full-width top + two side-by-side (or three vertical full-width) |
| 4–5 | 2-column grid + Dev row full-width at bottom |
| 6+ | All full-width vertical stack (tall canvas, simpler layout) |

## 6. Inter-zone spacing rules

- **Horizontal gap between side-by-side zones: ≥20 px**
- **Vertical gap between stacked zones: ≥20 px**
- If side-by-side zones have different content heights, **normalize to the taller one** (uniform bottom edge looks intentional; mismatched looks broken).

## 7. Internal column rules within a zone

- ≤5 items in zone: **single column**.
- 6–15 items: **2 columns**.
- 16+ items: **3 columns**.
- More than 3 columns gets cramped (cols become <200px wide and even short labels start wrapping).

**Column width:** `(zone_width - 2 × zone_padding - (cols - 1) × gutter) / cols`. For a 720-wide zone with 20-px padding and 20-px gutters and 3 cols: `(720 - 40 - 40) / 3 = 213` px.

## 8. Arrow geometry

- **Axis-aligned arrows:** the simpler `"start": {"id": "..."}, "end": {"id": "..."}` schema works — server auto-trims endpoints to box edges.
- **Diagonal arrows:** REQUIRES the full `startBinding` / `endBinding` with `fixedPoint`. Without it, arrows float in space near (but not on) box edges.
- **Fan-in from multiple sources to one target:** use asymmetric `fixedPoint` (`[0.25, 0]` and `[0.75, 0]`) so arrows don't overlap.

See `canvas-api.md` for the full binding schema.

## 9. Color discipline

- One pastel fill per category — don't mix colors within a single zone.
- Background zone color is always lighter (`opacity: 40`) than the box fills inside it.
- Header text uses the zone's stroke color (or a slightly darker variant).
- White canvas → never use light gray text (`#b0b0b0`, `#999`). Minimum: `#757575`.

## 10. Title placement

- Centered above the diagram.
- `fontSize: 24` for titles, `fontSize: 18` for zone headers, `fontSize: 16` minimum for body labels.
- To center: `x = canvas_center - len(title) * fontSize * 0.5`. (Approximation; close enough for most fonts.)

## 11. Verification checklist (run after every POST)

Before claiming "diagram drawn":

1. **Count matches:** `GET /api/elements` returns the count you POSTed (use poll-until-match — don't just sleep).
2. **Containment:** every box's bounding box is inside its parent zone's bounding box.
3. **Inter-zone gaps:** every pair of stacked/side-by-side zones has ≥20 px clearance.
4. **No double-IDs:** every element has a unique `id`.
5. **Arrow endpoints land on box edges:** if you used `startBinding`/`endBinding` with `fixedPoint`, the rendered arrow attaches cleanly.

If any check fails, fix the geometry and re-POST. Don't ship a broken diagram.

## 12. When in doubt, prefer

| Conflict | Choose |
|---|---|
| Tight layout vs. correct | Correct (always) |
| Many small boxes vs. fewer larger ones | Fewer larger |
| Symmetric grid vs. content-shaped layout | Content-shaped if content is heterogeneous |
| 2 cols × tall vs. 3 cols × short with wrap | 2 cols × tall (no wrap surprises) |
| Inline labels vs. legend | Inline labels |

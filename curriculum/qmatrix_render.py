"""
Render a Q-matrix SVG from a diagnostic item bank JSON.

The renderer is topic-agnostic: it reads `meta.node_order` for the column
order and `meta.short_labels` (optional) for compact column headers. If
no short_labels are provided, headers are auto-abbreviated from the full
skill name. Works for any skill set, not just fractions.

Expected JSON shape:
{
  "meta": {
      "node_order": [skill1, skill2, ...],
      "short_labels": { skill1: "S1", ... }     # optional
  },
  "single_skill_items": [
      {"item_id": ..., "q_vector": [0/1, ...], "target_node": "<skill>", ...},
      ...
  ],
  "multi_skill_items": [
      {"item_id": ..., "q_vector": [0/1, ...], "target_nodes": [<skills>], ...},
      ...
  ]
}

Usage:
    python qmatrix_render.py path/to/bank.json [out.svg]

If out.svg is omitted, writes qmatrix.svg in the current directory.
"""

import json
import sys
from pathlib import Path


# ---- Layout constants (in SVG user units; viewBox is 680 wide) ----
LEFT = 10              # left margin
ID_COL_W = 90          # width of the item-id column
CELL_GAP = 2           # gap between skill columns

SKILLS_LEFT = LEFT + ID_COL_W + 5    # where skill columns begin
USABLE_W = 680 - SKILLS_LEFT - LEFT  # space available for all skill columns

ROW_H = 20
HEADER_Y = 50
FIRST_ROW_Y = 80
CELL_INSET = 2         # vertical padding inside a row

# Colors: dark = primary target, light = supporting skill
TARGET_FILL = "#185FA5"      # Blue 600
SUPPORT_FILL = "#B5D4F4"     # Blue 100


# ---- helpers ----------------------------------------------------------

def auto_short_label(skill: str, max_len: int = 7) -> str:
    """Fallback abbreviation when the bank has no short_labels entry for a
    skill. Tries to be readable without being clever: replace underscores
    with spaces and truncate. Banks should provide explicit short_labels
    for good column headers."""
    return skill.replace("_", " ")[:max_len]


def resolve_short_labels(bank: dict) -> dict:
    """Return skill -> short label for every skill in node_order."""
    nodes = bank["meta"]["node_order"]
    provided = bank["meta"].get("short_labels", {})
    return {n: provided.get(n, auto_short_label(n)) for n in nodes}


# ---- rendering --------------------------------------------------------

def cell_filled(x: int, y: int, w: int, h: int, fill: str) -> str:
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" '
        f'rx="2" fill="{fill}"/>'
    )


def cell_empty(x: int, y: int, w: int, h: int) -> str:
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" '
        f'rx="2" fill="none" stroke="#d0d0d0" stroke-width="0.5"/>'
    )


def render_row(item_id, q_vector, targets, y, col_x_fn, cell_w, cell_h):
    """One matrix row. `targets` is the set of skill indices that are
    primary targets for this item (the rest of the 1s are supporting)."""
    parts = [
        f'<text x="{LEFT}" y="{y + 14}" font-family="sans-serif" '
        f'font-size="12">{item_id}</text>'
    ]
    cy = y + CELL_INSET
    for j, bit in enumerate(q_vector):
        cx = col_x_fn(j)
        if bit == 1:
            fill = TARGET_FILL if j in targets else SUPPORT_FILL
            parts.append(cell_filled(cx, cy, cell_w, cell_h, fill))
        else:
            parts.append(cell_empty(cx, cy, cell_w, cell_h))
    return parts


def render_qmatrix(bank: dict) -> str:
    """Build a complete SVG string for the given item bank."""
    nodes = bank["meta"]["node_order"]
    single_items = bank.get("single_skill_items", [])
    multi_items = bank.get("multi_skill_items", [])
    K = len(nodes)

    # Validate q_vector alignment (cheap sanity check; the full validator
    # in validate_bank.py is more thorough)
    for it in single_items + multi_items:
        if len(it["q_vector"]) != K:
            raise ValueError(
                f"Item {it['item_id']} has q_vector of length "
                f"{len(it['q_vector'])}, expected {K}"
            )

    # Compute column geometry: divide usable width across K skills
    col_w_total = USABLE_W // K
    cell_w = col_w_total - CELL_GAP
    cell_h = ROW_H - 2 * CELL_INSET

    def col_x(j): return SKILLS_LEFT + j * col_w_total
    def col_center(j): return col_x(j) + cell_w / 2

    right_edge = col_x(K) - CELL_GAP
    labels = resolve_short_labels(bank)

    parts = []
    parts.append('<!--SVG_OPEN_PLACEHOLDER-->')
    parts.append(
        '<defs>'
        '<marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" '
        'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
        '<path d="M2 1L8 5L2 9" fill="none" stroke="context-stroke" '
        'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'
        '</marker></defs>'
    )

    # Legend
    parts.append(
        f'<text x="{LEFT}" y="22" font-family="sans-serif" font-size="12" '
        f'fill="#555">\u25a0 target skill   \u25e7 supporting skill   '
        f'\u25a1 not required</text>'
    )

    # Column headers (use short labels from bank, with fallback)
    for j, n in enumerate(nodes):
        parts.append(
            f'<text x="{col_center(j):.1f}" y="{HEADER_Y}" '
            f'font-family="sans-serif" font-size="14" font-weight="500" '
            f'text-anchor="middle">{labels[n]}</text>'
        )

    # Section label + header underline
    parts.append(
        f'<text x="{LEFT}" y="70" font-family="sans-serif" font-size="12" '
        f'fill="#555">Single-skill items</text>'
    )
    parts.append(
        f'<line x1="{LEFT}" y1="60" x2="{right_edge}" y2="60" '
        f'stroke="#bbb" stroke-width="0.5"/>'
    )

    # Single-skill item rows
    y = FIRST_ROW_Y
    for it in single_items:
        target_idx = (
            {nodes.index(it["target_node"])} if it.get("target_node") else set()
        )
        parts.extend(
            render_row(it["item_id"], it["q_vector"], target_idx, y,
                       col_x, cell_w, cell_h)
        )
        y += ROW_H

    # Divider + multi-skill section
    parts.append(
        f'<line x1="{LEFT}" y1="{y + 6}" x2="{right_edge}" y2="{y + 6}" '
        f'stroke="#bbb" stroke-width="0.5"/>'
    )
    multi_label_y = y + 18
    parts.append(
        f'<text x="{LEFT}" y="{multi_label_y}" font-family="sans-serif" '
        f'font-size="12" fill="#555">Multi-skill items</text>'
    )

    y = multi_label_y + 8
    for it in multi_items:
        target_idx = {nodes.index(n) for n in it.get("target_nodes", [])}
        parts.extend(
            render_row(it["item_id"], it["q_vector"], target_idx, y,
                       col_x, cell_w, cell_h)
        )
        y += ROW_H

    # Totals row
    totals = [0] * K
    for it in single_items + multi_items:
        for i, b in enumerate(it["q_vector"]):
            totals[i] += b

    parts.append(
        f'<line x1="{LEFT}" y1="{y + 4}" x2="{right_edge}" y2="{y + 4}" '
        f'stroke="#bbb" stroke-width="0.5"/>'
    )
    total_y = y + 14
    parts.append(
        f'<text x="{LEFT}" y="{total_y + 14}" font-family="sans-serif" '
        f'font-size="14" font-weight="500">Total tags</text>'
    )
    for j, t in enumerate(totals):
        parts.append(
            f'<text x="{col_center(j):.1f}" y="{total_y + 14}" '
            f'font-family="sans-serif" font-size="14" font-weight="500" '
            f'text-anchor="middle">{t}</text>'
        )

    H = total_y + 14 + 18
    parts[0] = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="100%" '
        f'viewBox="0 0 680 {H}" role="img">'
        f'<title>Q-matrix</title>'
        f'<desc>{len(single_items) + len(multi_items)} items by '
        f'{K} skills binary matrix.</desc>'
    )
    parts.append('</svg>')

    return "".join(parts)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    in_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("qmatrix.svg")

    with in_path.open() as f:
        bank = json.load(f)

    svg = render_qmatrix(bank)
    out_path.write_text(svg, encoding="utf-8")

    n_single = len(bank.get("single_skill_items", []))
    n_multi = len(bank.get("multi_skill_items", []))
    n_skills = len(bank["meta"]["node_order"])
    print(f"Wrote {out_path} -- {n_single + n_multi} items by "
          f"{n_skills} skills.")


if __name__ == "__main__":
    main()

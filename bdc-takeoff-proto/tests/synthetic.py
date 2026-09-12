"""Build a small synthetic Revit-style DXF so Slice 1 is runnable and testable before the
real fixtures arrive. NOT a fixture — fixtures/ holds real, hand-verified sets only.

Plan: 20' × 30' exterior, 6" double-line walls on A-WALL, a single-line centerline partition on
A-WALL-CNTR split into gapped pieces (Markiewicz case), a door block with a Revit UID suffix,
a SOLID hatch room fill, room labels, and one aligned dimension. Units = inches.

    python -m tests.synthetic out/synth      → out/synth/plan.dxf
"""

from __future__ import annotations

import sys
from pathlib import Path

import ezdxf

# geometry constants (inches) — tests assert against these
EXT_W, EXT_H, WALL_T = 30 * 12, 20 * 12, 6.0
CNTR_Y = 120.0  # partition centerline y
GAP = 0.02  # < 1/32" default snap → must merge
BLOCK_RAW = "Door_36x80-2345678"
BLOCK_NORM = "DOOR_36X80"
HATCH_W, HATCH_H = 120.0, 100.0  # 10' × 8'4" kitchen fill


def build(path: Path) -> Path:
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 1  # inches
    for name in (
        "A-WALL",
        "A-WALL-CNTR",
        "A-DOOR",
        "A-AREA-PATT",
        "A-ANNO-TEXT",
        "A-ANNO-DIMS",
        "Q-ROOF-Drexel",
    ):
        doc.layers.add(name)
    msp = doc.modelspace()

    # double-line exterior walls: outer + inner rectangles as LWPOLYLINEs
    msp.add_lwpolyline(
        [(0, 0), (EXT_W, 0), (EXT_W, EXT_H), (0, EXT_H)], close=True, dxfattribs={"layer": "A-WALL"}
    )
    t = WALL_T
    msp.add_lwpolyline(
        [(t, t), (EXT_W - t, t), (EXT_W - t, EXT_H - t), (t, EXT_H - t)],
        close=True,
        dxfattribs={"layer": "A-WALL"},
    )

    # single-line partition split into 4 pieces with sub-snap gaps (and one overlap)
    xs = [t, 90, 90 + GAP, 180, 180 + GAP, 260, 258, EXT_W - t]  # 258<260: overlap
    for a, b in zip(xs[0::2], xs[1::2], strict=True):
        msp.add_line((a, CNTR_Y), (b, CNTR_Y), dxfattribs={"layer": "A-WALL-CNTR"})
    # a collinear-but-far line that must NOT merge (gap 24")
    msp.add_line((-100, CNTR_Y), (-24, CNTR_Y), dxfattribs={"layer": "A-WALL-CNTR"})
    # a parallel offset line that must NOT merge (offset 1")
    msp.add_line((t, CNTR_Y + 1.0), (60, CNTR_Y + 1.0), dxfattribs={"layer": "A-WALL-CNTR"})

    # door block with Revit UID suffix, geometry on layer "0" (inherits INSERT layer)
    blk = doc.blocks.new(BLOCK_RAW)
    blk.add_line((0, 0), (36, 0))
    blk.add_arc((0, 0), 36, 0, 90)
    msp.add_blockref(BLOCK_RAW, (120, t), dxfattribs={"layer": "A-DOOR"})
    msp.add_blockref(BLOCK_RAW, (240, EXT_H - t), dxfattribs={"layer": "A-DOOR", "rotation": 180})

    # SOLID hatch room fill on a pattern layer, with an island
    h = msp.add_hatch(color=7, dxfattribs={"layer": "A-AREA-PATT"})
    h.paths.add_polyline_path(
        [(t, t), (t + HATCH_W, t), (t + HATCH_W, t + HATCH_H), (t, t + HATCH_H)], is_closed=True, flags=1
    )
    h.paths.add_polyline_path([(30, 30), (54, 30), (54, 54), (30, 54)], is_closed=True, flags=0)

    # labels + one dimension
    msp.add_text("KITCHEN", dxfattribs={"layer": "A-ANNO-TEXT", "height": 6, "insert": (40, 80)})
    msp.add_mtext(
        "LIVING\\P312 SF", dxfattribs={"layer": "A-ANNO-TEXT", "char_height": 6, "insert": (200, 200)}
    )
    msp.add_aligned_dim(p1=(0, 0), p2=(EXT_W, 0), distance=-24, dxfattribs={"layer": "A-ANNO-DIMS"}).render()

    # Markiewicz naming: project prefix + suffix on a roof layer
    msp.add_line((0, 300), (EXT_W, 300), dxfattribs={"layer": "Q-ROOF-Drexel"})

    # a paperspace sheet with a title so sheet_count == 2
    ps = doc.layouts.new("A101")
    ps.add_text("A101 FLOOR PLAN", dxfattribs={"height": 0.25, "insert": (1, 1)})

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(path)
    return path


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "out/synth") / "plan.dxf"
    print(build(out))

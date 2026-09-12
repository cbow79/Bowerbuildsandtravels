"""Pins Slice 1 behavior on the synthetic DXF. Replace/extend with fixtures/quinn once present."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from takeoff.ingest_dxf import NameRules, ingest_dir, strip_revit_uid
from takeoff.ir import IRDocument, Kind, Rank, json_schema
from tests import synthetic as syn


@pytest.fixture(scope="module")
def dxf_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("synth")
    syn.build(d / "plan.dxf")
    return d


@pytest.fixture(scope="module")
def doc(dxf_dir: Path) -> IRDocument:
    return ingest_dir(dxf_dir, rules=NameRules(strip_prefix=("Q-",), strip_suffix=("-Drexel",)))


def test_run_mode_and_snap_first_in_output(doc: IRDocument) -> None:
    d = json.loads(doc.to_json())
    assert list(d)[:2] == ["run_mode", "snap_tol_in"]
    assert d["run_mode"] == "PROTOTYPE"
    assert d["snap_tol_in"] == pytest.approx(1 / 32)
    assert doc.snap_tol == pytest.approx(1 / 32)  # drawing units are inches here


def test_phase1_units_and_sheets(doc: IRDocument) -> None:
    i = doc.interrogation
    assert i.units.name == "in" and i.units.to_feet == pytest.approx(1 / 12)
    assert i.units.source == "header:$INSUNITS" and i.units.rank == Rank.ANNOTATED
    assert i.sheet_count == 2
    assert {s.space for s in doc.sheets} == {"MODEL", "PAPER"}
    model = next(s for s in doc.sheets if s.space == "MODEL")
    assert model.extents is not None
    assert model.extents[2] - model.extents[0] == pytest.approx(syn.EXT_W + 100)  # far line at x=-100
    assert not doc.rfis


def test_uid_and_project_affix_stripping(doc: IRDocument) -> None:
    assert strip_revit_uid("Basic Wall-Exterior-1234567") == "BASIC WALL-EXTERIOR"
    assert strip_revit_uid("Door_36x80_[2345678]") == "DOOR_36X80"
    assert strip_revit_uid("$0$A-WALL") == "A-WALL"
    assert strip_revit_uid("A-WALL") == "A-WALL"
    names = {ly.name: ly.norm for ly in doc.interrogation.layers}
    assert names["Q-ROOF-Drexel"] == "ROOF"
    blocks = {b.name: b for b in doc.interrogation.blocks}
    assert blocks[syn.BLOCK_RAW].norm == syn.BLOCK_NORM
    assert blocks[syn.BLOCK_RAW].inserts == 2


def test_block_interrogation_expands_children_onto_insert_layer(doc: IRDocument) -> None:
    door = [e for e in doc.entities if e.block == syn.BLOCK_NORM]
    assert len(door) == 4  # 2 inserts × (line + arc)
    assert {e.layer for e in door} == {"A-DOOR"}  # layer "0" geometry inherits INSERT layer
    kinds = sorted(e.kind for e in door)
    assert kinds == [Kind.LINE, Kind.LINE, Kind.POLYLINE, Kind.POLYLINE]
    rot = [e for e in door if e.kind == Kind.LINE and e.attrs["insert_rotation"] == 180][0]
    assert rot.pts[1][0] == pytest.approx(240 - 36)  # rotated 180° about insert point


def test_double_line_walls_lf(doc: IRDocument) -> None:
    walls = doc.entities_on("A-WALL")
    assert len(walls) == 2 and all(w.closed for w in walls)
    outer, inner = sorted(walls, key=lambda w: -w.area())
    assert outer.length() == pytest.approx(2 * (syn.EXT_W + syn.EXT_H))
    assert inner.area() == pytest.approx((syn.EXT_W - 12) * (syn.EXT_H - 12))


def test_gap_merge_centerlines(doc: IRDocument) -> None:
    cl = doc.entities_on("A-WALL-CNTR")
    # 4 gapped pieces → 1 fused; far line and offset line survive untouched
    assert len(cl) == 3
    fused = max(cl, key=lambda e: e.length())
    assert fused.length() == pytest.approx(syn.EXT_W - 2 * syn.WALL_T)
    assert fused.rank == Rank.INFERRED and len(fused.attrs["merged_from"]) == 4
    assert sorted(e.length() for e in cl if e is not fused) == pytest.approx([54.0, 76.0])


def test_no_merge_flag(dxf_dir: Path) -> None:
    raw = ingest_dir(dxf_dir, merge_gaps=False)
    assert len(raw.entities_on("A-WALL-CNTR")) == 6


def test_hatch_branching(doc: IRDocument) -> None:
    loops = [e for e in doc.entities if e.kind == Kind.HATCH]
    assert len(loops) == 2
    ext = [e for e in loops if e.attrs["external"]]
    assert len(ext) == 1 and ext[0].attrs["solid"] is True and ext[0].attrs["pattern"] == "SOLID"
    assert ext[0].attrs["area"] == pytest.approx(syn.HATCH_W * syn.HATCH_H)
    hole = [e for e in loops if not e.attrs["external"]][0]
    assert hole.area() == pytest.approx(24 * 24)


def test_text_and_dimension(doc: IRDocument) -> None:
    texts = {e.text for e in doc.entities if e.kind == Kind.TEXT}
    assert "KITCHEN" in texts and "LIVING\n312 SF" in texts
    assert all(e.rank == Rank.ANNOTATED for e in doc.entities if e.kind == Kind.TEXT)
    dims = [e for e in doc.entities if e.kind == Kind.DIMENSION]
    assert len(dims) == 1
    assert dims[0].attrs["measurement"] == pytest.approx(syn.EXT_W)


def test_json_roundtrip_and_schema(doc: IRDocument) -> None:
    back = IRDocument.from_json(doc.to_json())
    assert len(back.entities) == len(doc.entities)
    assert back.entities[0] == doc.entities[0]
    assert back.interrogation.units == doc.interrogation.units
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(json.loads(doc.to_json()), json_schema())


def test_unitless_dxf_raises_rfi(tmp_path: Path) -> None:
    import ezdxf

    d = ezdxf.new("R2018")
    d.header["$INSUNITS"] = 0
    d.modelspace().add_line((0, 0), (10, 0))
    d.saveas(tmp_path / "x.dxf")
    doc = ingest_dir(tmp_path)
    assert doc.units.source == "assumed" and doc.units.rank == Rank.UNRESOLVED
    assert [r.id for r in doc.rfis] == ["RFI-UNITS"]

"""DXF → IR via ezdxf.

NOTE: the plan says "port, don't rewrite" the existing ezdxf code. That code was not available
when this was written, so this module implements the four behaviors the plan names
(block interrogation, hatch branching, gap-merge centerlines, Revit UID stripping) from
scratch. When the original code arrives, diff behavior against it and keep the original where
they disagree; tests/test_ingest_dxf.py pins the current behavior.

Phase 1 interrogation (units, extents, layers, blocks, sheet count) runs first and is stored on
the document; nothing downstream may run without it.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path as FSPath
from typing import Any

import ezdxf
from ezdxf import path as ezpath
from ezdxf import units as ezunits
from ezdxf.entities import DXFEntity, DXFGraphic, Hatch, Insert
from ezdxf.math import Vec3

from . import DEFAULT_SNAP_IN, RUN_MODE
from .ir import (
    RFI,
    BlockInfo,
    Entity,
    Interrogation,
    IRDocument,
    Kind,
    LayerInfo,
    Point,
    Rank,
    Sheet,
    Units,
)

# --------------------------------------------------------------------------------------------
# Name normalization (Revit UID stripping + per-project prefix/suffix)
# --------------------------------------------------------------------------------------------

# Revit DXF export decorates block/layer names with element ids in a few shapes:
#   "Basic Wall-Exterior-1234567"   "Door_36x80_[2345678]"   "M_Window-2345678-1"   "$0$A-WALL"
_UID_PATTERNS = [
    re.compile(r"\[\d{4,}\]$"),  # trailing [1234567]
    re.compile(r"[-_ ]\d{4,}(?:[-_]\d+)?$"),  # trailing -1234567 or -1234567-1
    re.compile(r"^\$\d+\$"),  # xref/nested prefix $0$
]


@dataclass(slots=True)
class NameRules:
    strip_prefix: tuple[str, ...] = ()
    strip_suffix: tuple[str, ...] = ()

    def norm(self, name: str) -> str:
        n = name.strip()
        for pat in _UID_PATTERNS:
            n = pat.sub("", n)
        for p in self.strip_prefix:
            if p and n.upper().startswith(p.upper()):
                n = n[len(p) :]
        for s in self.strip_suffix:
            if s and n.upper().endswith(s.upper()):
                n = n[: -len(s)]
        return n.strip("-_ ").upper() or name.upper()


def strip_revit_uid(name: str) -> str:
    return NameRules().norm(name)


# --------------------------------------------------------------------------------------------
# Public entry points
# --------------------------------------------------------------------------------------------


def ingest_dir(
    src: str | FSPath,
    snap_in: float = DEFAULT_SNAP_IN,
    flatten_in: float = 0.01,
    merge_gaps: bool = True,
    rules: NameRules | None = None,
) -> IRDocument:
    """Ingest every *.dxf under `src` (a file or a directory) into one IRDocument."""
    src = FSPath(src)
    files = sorted(src.glob("*.dxf")) if src.is_dir() else [src]
    if not files:
        raise FileNotFoundError(f"no .dxf files under {src}")
    rules = rules or NameRules()
    log: list[str] = [f"run_mode={RUN_MODE} source=dxf files={len(files)} snap_in={snap_in}"]

    # Phase 1 for each file: units must agree across the set, else RFI.
    docs = [ezdxf.readfile(f) for f in files]
    unit_facts = [_units_of(d, log) for d in docs]
    units = unit_facts[0]
    rfis: list[RFI] = []
    if len({u.code for u in unit_facts}) > 1:
        rfis.append(
            RFI(
                "RFI-UNITS",
                "Mixed $INSUNITS across DXF set",
                ", ".join(f"{f.name}={u.name}" for f, u in zip(files, unit_facts, strict=True)),
            )
        )
        units = Units(units.code, units.name, units.to_feet, units.source, Rank.UNRESOLVED)

    snap = snap_in / 12.0 / units.to_feet  # inches → drawing units
    flatten = flatten_in / 12.0 / units.to_feet
    log.append(f"snap_tol={snap:.6g} {units.name} flatten_tol={flatten:.6g} {units.name}")

    entities: list[Entity] = []
    sheets: list[Sheet] = []
    block_stats: dict[str, dict[str, Any]] = {}
    header_ext: tuple[float, float, float, float] | None = None
    dxf_version: str | None = None
    warnings: list[str] = []

    for f, doc in zip(files, docs, strict=True):
        dxf_version = dxf_version or doc.dxfversion
        header_ext = header_ext or _header_extents(doc)
        conv = _Converter(f.name, rules, flatten, block_stats, warnings)
        for layout in doc.layouts:
            space = "MODEL" if layout.is_modelspace else "PAPER"
            if space == "PAPER" and len(layout) == 0:
                continue  # empty default "Layout1" is noise, not a sheet
            sheet_id = f"{f.stem}:{layout.name}"
            ents = conv.convert_layout(layout, sheet_id)
            if not ents:
                continue
            sheets.append(
                Sheet(
                    id=sheet_id,
                    file=f.name,
                    layout=layout.name,
                    space=space,
                    extents=_extents(ents),
                    entity_count=len(ents),
                )
            )
            entities.extend(ents)
        log.append(f"{f.name}: {doc.dxfversion} layouts={[s.layout for s in sheets if s.file == f.name]}")

    if merge_gaps:
        before = len(entities)
        entities = merge_collinear_gaps(entities, snap)
        log.append(f"gap-merge: {before} → {len(entities)} entities (tol={snap:.6g})")
        per_sheet = Counter(e.sheet for e in entities)
        for s in sheets:
            s.entity_count = per_sheet.get(s.id, 0)

    interrogation = Interrogation(
        units=units,
        header_extents=header_ext,
        computed_extents=_extents(entities),
        sheet_count=len(sheets),
        layers=_layer_inventory(entities),
        blocks=[
            BlockInfo(name=k, norm=v["norm"], inserts=v["inserts"], children=v["children"])
            for k, v in sorted(block_stats.items())
        ],
        entity_kinds=dict(Counter(e.kind.value for e in entities)),
        dxf_version=dxf_version,
        warnings=warnings,
    )
    if units.source == "assumed":
        rfis.append(RFI("RFI-UNITS", "DXF header has no usable $INSUNITS", f"assumed {units.name}"))
    if not sheets:
        warnings.append("no non-empty layouts found")

    return IRDocument.new(
        snap_tol=snap,
        snap_tol_in=snap_in,
        source_type="dxf",
        source_path=str(src),
        units=units,
        sheets=sheets,
        entities=entities,
        interrogation=interrogation,
        rfis=rfis,
        assumptions=[
            "block children on layer '0' inherit the INSERT's layer (DXF convention)",
            f'curves flattened at {flatten_in}" max deviation',
        ],
        log=log,
    )


# --------------------------------------------------------------------------------------------
# Phase 1 helpers
# --------------------------------------------------------------------------------------------

_FEET_CODE = 2


def _units_of(doc: Any, log: list[str]) -> Units:
    code = int(doc.header.get("$INSUNITS", 0) or 0)
    if code == 0:
        # Revit exports always set $INSUNITS; a 0 here means a re-saved or hand-built file.
        log.append("$INSUNITS=0 (unitless) → assuming inches; RFI raised")
        return Units(code=-1, name="inch", to_feet=1 / 12, source="assumed", rank=Rank.UNRESOLVED)
    name = ezunits.decode(code)
    try:
        to_feet = ezunits.conversion_factor(code, _FEET_CODE)
    except Exception as ex:  # unknown/angular code
        log.append(f"$INSUNITS={code} ({name}) not convertible to feet: {ex}; assuming inches")
        return Units(code=code, name=name, to_feet=1 / 12, source="assumed", rank=Rank.UNRESOLVED)
    return Units(code=code, name=name, to_feet=to_feet, source="header:$INSUNITS", rank=Rank.ANNOTATED)


def _header_extents(doc: Any) -> tuple[float, float, float, float] | None:
    lo = doc.header.get("$EXTMIN")
    hi = doc.header.get("$EXTMAX")
    if lo is None or hi is None:
        return None
    vals = (lo[0], lo[1], hi[0], hi[1])
    if any(abs(v) >= 1e19 for v in vals):  # ezdxf's "unset" sentinel
        return None
    return tuple(float(v) for v in vals)  # type: ignore[return-value]


def _extents(ents: list[Entity]) -> tuple[float, float, float, float] | None:
    boxes = [b for b in (e.bbox() for e in ents) if b]
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


_LF_KINDS = (Kind.LINE, Kind.POLYLINE, Kind.CIRCLE)  # closed wall faces count; hatch loops do not


def _layer_inventory(ents: list[Entity]) -> list[LayerInfo]:
    by: dict[str, list[Entity]] = defaultdict(list)
    for e in ents:
        by[e.layer].append(e)
    out = []
    for name, es in sorted(by.items()):
        out.append(
            LayerInfo(
                name=name,
                norm=es[0].layer_norm,
                count=len(es),
                lf=sum(e.length() for e in es if e.kind in _LF_KINDS),
                kinds=dict(Counter(e.kind.value for e in es)),
            )
        )
    return out


# --------------------------------------------------------------------------------------------
# Entity conversion
# --------------------------------------------------------------------------------------------

_CURVE_TYPES = {"LWPOLYLINE", "POLYLINE", "ARC", "ELLIPSE", "SPLINE"}


class _Converter:
    def __init__(
        self,
        file: str,
        rules: NameRules,
        flatten: float,
        block_stats: dict[str, dict[str, Any]],
        warnings: list[str],
    ) -> None:
        self.file = file
        self.rules = rules
        self.flatten = flatten
        self.block_stats = block_stats
        self.warnings = warnings
        self._n = 0
        self._skipped: Counter[str] = Counter()

    def convert_layout(self, layout: Any, sheet: str) -> list[Entity]:
        out: list[Entity] = []
        for e in layout:
            out.extend(self.convert(e, sheet, block=None, block_raw=None, insert_layer=None))
        if self._skipped:
            self.warnings.append(f"{sheet}: skipped {dict(self._skipped)}")
            self._skipped.clear()
        return out

    # -- dispatch ---------------------------------------------------------------------------
    def convert(
        self,
        e: DXFEntity,
        sheet: str,
        block: str | None,
        block_raw: str | None,
        insert_layer: str | None,
    ) -> list[Entity]:
        t = e.dxftype()
        if not isinstance(e, DXFGraphic):
            self._skipped[t] += 1
            return []
        if t == "INSERT":
            return self._insert(e, sheet)  # type: ignore[arg-type]

        layer = e.dxf.layer
        if layer == "0" and insert_layer:
            layer = insert_layer  # DXF convention: block geometry on "0" takes the INSERT's layer
        base = dict(
            sheet=sheet,
            layer=layer,
            layer_norm=self.rules.norm(layer),
            block=block,
            block_raw=block_raw,
            source={"file": self.file, "handle": e.dxf.get("handle"), "dxftype": t},
        )
        attrs: dict[str, Any] = {}
        color = _attr(e, "color")
        if color is not None and color != 256:
            attrs["color"] = color
        lt = _attr(e, "linetype")
        if lt and lt.upper() not in ("BYLAYER", "CONTINUOUS"):
            attrs["linetype"] = lt

        if t == "LINE":
            return [self._mk(Kind.LINE, [_xy(e.dxf.start), _xy(e.dxf.end)], base, attrs=attrs)]
        if t == "CIRCLE":
            pts = self._flatten(e)
            attrs |= {"radius": float(e.dxf.radius), "center": _xy(e.dxf.center)}
            return [self._mk(Kind.CIRCLE, pts, base, closed=True, attrs=attrs)]
        if t in _CURVE_TYPES:
            closed = bool(getattr(e, "closed", False)) or (
                t == "ELLIPSE" and abs(e.dxf.end_param - e.dxf.start_param - math.tau) < 1e-9
            )
            pts = self._flatten(e)
            if closed and len(pts) > 2 and math.dist(pts[0], pts[-1]) < 1e-9:
                pts = pts[:-1]
            if len(pts) < 2:
                return []
            attrs["src"] = t
            if t == "LWPOLYLINE" and _attr(e, "const_width"):
                attrs["width"] = float(e.dxf.const_width)
            return [self._mk(Kind.POLYLINE, pts, base, closed=closed, attrs=attrs)]
        if t in ("TEXT", "MTEXT", "ATTRIB", "ATTDEF"):
            text = e.plain_text() if hasattr(e, "plain_text") else e.dxf.text
            text = (text or "").strip()
            if not text:
                return []
            attrs |= {
                "height": float(_attr(e, "char_height", "height") or 0.0),
                "rotation": float(_attr(e, "rotation") or 0.0),
                "src": t,
            }
            if t in ("ATTRIB", "ATTDEF"):
                attrs["tag"] = e.dxf.tag
            return [
                self._mk(Kind.TEXT, [_xy(e.dxf.insert)], base, text=text, attrs=attrs, rank=Rank.ANNOTATED)
            ]
        if t == "HATCH":
            return self._hatch(e, base, attrs)  # type: ignore[arg-type]
        if t == "DIMENSION":
            return self._dimension(e, base, attrs)
        if t == "POINT":
            return [self._mk(Kind.POINT, [_xy(e.dxf.location)], base, attrs=attrs)]
        self._skipped[t] += 1
        return []

    # -- block interrogation ----------------------------------------------------------------
    def _insert(self, ins: Insert, sheet: str) -> list[Entity]:
        raw = ins.dxf.name
        norm = self.rules.norm(raw)
        stats = self.block_stats.setdefault(raw, {"norm": norm, "inserts": 0, "children": 0})
        stats["inserts"] += 1
        out: list[Entity] = []
        try:
            children = list(ins.virtual_entities())
        except Exception as ex:  # malformed block; keep the insert point so nothing is silent
            self.warnings.append(f"{sheet}: INSERT {raw!r} not expandable: {ex}")
            children = []
        for c in children:
            if c.dxftype() == "INSERT":
                out.extend(self._insert(c, sheet))  # nested blocks: recurse, keep outer name
                for e in out:
                    e.block = e.block or norm
                    e.block_raw = e.block_raw or raw
                continue
            out.extend(self.convert(c, sheet, block=norm, block_raw=raw, insert_layer=ins.dxf.layer))
        # attributes (door/window tags) attached to the insert
        for att in getattr(ins, "attribs", []):
            out.extend(self.convert(att, sheet, block=norm, block_raw=raw, insert_layer=ins.dxf.layer))
        for e in out:
            e.attrs.setdefault("insert", _xy(ins.dxf.insert))
            e.attrs.setdefault("insert_rotation", float(_attr(ins, "rotation") or 0.0))
        stats["children"] = max(stats["children"], len(out))
        return out

    # -- hatch branching --------------------------------------------------------------------
    def _hatch(self, h: Hatch, base: dict[str, Any], attrs: dict[str, Any]) -> list[Entity]:
        """One HATCH entity per boundary loop.

        Branches: solid fills (wall poche / room fills) vs pattern fills (materials) are kept
        apart via attrs.solid so areas.py can treat them differently. External vs internal
        loops are flagged (attrs.external) so holes are not double-counted.
        """
        pattern = (_attr(h, "pattern_name") or "").upper()
        solid = bool(_attr(h, "solid_fill")) or pattern == "SOLID"
        out: list[Entity] = []
        try:
            paths = list(ezpath.from_hatch(h))
        except Exception as ex:
            self.warnings.append(f"{base['sheet']}: HATCH {base['source'].get('handle')} unreadable: {ex}")
            return out
        flags = [p.path_type_flags for p in h.paths]
        for i, p in enumerate(paths):
            pts = [_xy(v) for v in p.flattening(self.flatten)]
            if len(pts) > 2 and math.dist(pts[0], pts[-1]) < 1e-9:
                pts = pts[:-1]
            if len(pts) < 3:
                continue
            f = flags[i] if i < len(flags) else 0
            a = dict(attrs)
            a |= {
                "pattern": pattern or ("SOLID" if solid else ""),
                "solid": solid,
                "loop": i,
                "external": bool(f & 1),
                "outermost": bool(f & 16),
            }
            ent = self._mk(Kind.HATCH, pts, base, closed=True, attrs=a)
            ent.attrs["area"] = ent.area()
            out.append(ent)
        return out

    # -- dimensions (kept for scale verification in scale.py) ---------------------------------
    def _dimension(self, d: Any, base: dict[str, Any], attrs: dict[str, Any]) -> list[Entity]:
        pts = [_xy(_attr(d, k)) for k in ("defpoint", "defpoint2", "defpoint3") if _attr(d, k) is not None]
        meas = _attr(d, "actual_measurement")
        if meas is None and len(pts) >= 3 and d.dimtype in (0, 1):
            meas = math.dist(pts[1], pts[2])  # linear/aligned: distance between defpoint2/3
        attrs |= {
            "dimtype": int(d.dimtype),
            "measurement": None if meas is None else float(meas),
            "text": _attr(d, "text") or "<>",
        }
        return [self._mk(Kind.DIMENSION, pts, base, attrs=attrs, rank=Rank.ANNOTATED)]

    # -- helpers ------------------------------------------------------------------------------
    def _flatten(self, e: DXFGraphic) -> list[Point]:
        try:
            p = ezpath.make_path(e)
        except Exception as ex:
            self.warnings.append(f"{e.dxftype()} {e.dxf.get('handle')} unflattenable: {ex}")
            return []
        return _dedupe([_xy(v) for v in p.flattening(self.flatten)])

    def _mk(
        self,
        kind: Kind,
        pts: list[Point],
        base: dict[str, Any],
        closed: bool = False,
        text: str | None = None,
        attrs: dict[str, Any] | None = None,
        rank: Rank = Rank.GEOMETRIC,
    ) -> Entity:
        self._n += 1
        return Entity(
            id=f"{base['sheet']}#{self._n}",
            kind=kind,
            pts=pts,
            closed=closed,
            text=text,
            rank=rank,
            attrs=attrs or {},
            **base,
        )


def _attr(e: DXFEntity, *names: str) -> Any:
    """First supported+set DXF attribute among `names`; dxf.get raises on unsupported keys."""
    for n in names:
        if e.dxf.is_supported(n) and e.dxf.hasattr(n):
            return e.dxf.get(n)
    return None


def _xy(v: Any) -> Point:
    v = Vec3(v)
    return (float(v.x), float(v.y))


def _dedupe(pts: list[Point], eps: float = 1e-9) -> list[Point]:
    out: list[Point] = []
    for p in pts:
        if not out or math.dist(out[-1], p) > eps:
            out.append(p)
    return out


# --------------------------------------------------------------------------------------------
# Gap-merge centerlines
# --------------------------------------------------------------------------------------------


def merge_collinear_gaps(entities: list[Entity], tol: float) -> list[Entity]:
    """Merge LINE entities on the same sheet+layer that are collinear and touch/overlap/gap
    within `tol`. Single-line wall exports (Markiewicz) split centerlines at every opening
    and intersection; downstream polygonization needs them continuous.

    Only Kind.LINE participates. Merged entities keep the first member's id/source and record
    the merged member handles in attrs.merged_from.
    """
    from shapely import STRtree
    from shapely.geometry import LineString

    keep: list[Entity] = []
    groups: dict[tuple[str, str], list[Entity]] = defaultdict(list)
    for e in entities:
        if e.kind == Kind.LINE and len(e.pts) == 2 and e.length() > 1e-9:
            groups[(e.sheet, e.layer)].append(e)
        else:
            keep.append(e)

    ang_tol = math.radians(0.5)
    for _key, lines in groups.items():
        n = len(lines)
        geoms = [LineString(e.pts) for e in lines]
        tree = STRtree(geoms)
        parent = list(range(n))
        find = _finder(parent)

        for i, g in enumerate(geoms):
            for j in tree.query(g.buffer(tol)):
                j = int(j)
                if j <= i:
                    continue
                if _collinear(lines[i], lines[j], tol, ang_tol) and _within_gap(geoms[i], geoms[j], tol):
                    parent[find(i)] = find(j)

        clusters: dict[int, list[int]] = defaultdict(list)
        for i in range(n):
            clusters[find(i)].append(i)
        for members in clusters.values():
            if len(members) == 1:
                keep.append(lines[members[0]])
                continue
            keep.append(_fuse([lines[m] for m in members]))
    # stable order: by sheet then original id order
    order = {e.id: i for i, e in enumerate(entities)}
    keep.sort(key=lambda e: order.get(e.id, 1 << 30))
    return keep


def _finder(parent: list[int]) -> Any:
    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    return find


def _dir(e: Entity) -> tuple[float, float]:
    (x1, y1), (x2, y2) = e.pts
    L = math.hypot(x2 - x1, y2 - y1)
    return ((x2 - x1) / L, (y2 - y1) / L)


def _collinear(a: Entity, b: Entity, tol: float, ang_tol: float) -> bool:
    da, db = _dir(a), _dir(b)
    cross = abs(da[0] * db[1] - da[1] * db[0])
    if cross > math.sin(ang_tol):
        return False
    # perpendicular offset of b's endpoints from a's infinite line
    ax, ay = a.pts[0]
    for px, py in b.pts:
        off = abs((px - ax) * da[1] - (py - ay) * da[0])
        if off > tol:
            return False
    return True


def _within_gap(ga: Any, gb: Any, tol: float) -> bool:
    return ga.distance(gb) <= tol


def _fuse(members: list[Entity]) -> Entity:
    first = members[0]
    d = _dir(first)
    ox, oy = first.pts[0]
    ts = []
    for e in members:
        for px, py in e.pts:
            ts.append((px - ox) * d[0] + (py - oy) * d[1])
    t0, t1 = min(ts), max(ts)
    fused = Entity(
        id=first.id,
        sheet=first.sheet,
        layer=first.layer,
        layer_norm=first.layer_norm,
        kind=Kind.LINE,
        pts=[(ox + d[0] * t0, oy + d[1] * t0), (ox + d[0] * t1, oy + d[1] * t1)],
        closed=False,
        block=first.block,
        block_raw=first.block_raw,
        rank=Rank.INFERRED,  # geometry was altered by a heuristic
        attrs=dict(first.attrs),
        source=dict(first.source),
    )
    fused.attrs["merged_from"] = [m.source.get("handle") or m.id for m in members]
    return fused

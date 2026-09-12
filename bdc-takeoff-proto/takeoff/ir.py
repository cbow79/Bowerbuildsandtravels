"""Intermediate representation shared by every ingester (DXF, PDF, later CSV joins).

PROVISIONAL: written without spec/bower-takeoff-module-spec.md v1 §2.1 in hand. Field names
were chosen to be flat and JSON-obvious; reconcile against §2.1 before Slice 2 starts.

Design rules baked in:
- Coordinates stay in *drawing units* (see IRDocument.units). Scaling to feet is a Phase 1
  fact recorded once (units.to_feet), never applied silently to geometry.
- Every Entity carries a rank. Ingested geometry is rank 3 GEOMETRIC by default.
- Unknowns are explicit: `rfis` and `assumptions` are first-class on the document.
- Run mode and snap tolerance are the first keys written to every output.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from enum import IntEnum, StrEnum
from typing import Any

from . import IR_VERSION, RUN_MODE

Point = tuple[float, float]


class Rank(IntEnum):
    SCHEDULE_VERIFIED = 1  # matched to a Revit schedule row
    ANNOTATED = 2  # confirmed by on-sheet text / dimension
    GEOMETRIC = 3  # derived from geometry only
    INFERRED = 4  # heuristic (layer name, proximity)
    UNRESOLVED = 5  # needs RFI


class Kind(StrEnum):
    LINE = "LINE"  # 2 pts
    POLYLINE = "POLYLINE"  # n pts, closed flag; arcs/splines/ellipses are flattened to this
    CIRCLE = "CIRCLE"  # flattened polygon, closed=True, attrs.radius/center
    TEXT = "TEXT"  # 1 pt (insert), text
    HATCH = "HATCH"  # one entity per boundary loop, closed=True, attrs.pattern/solid/area
    DIMENSION = "DIMENSION"  # pts = definition points, attrs.measurement/text/dimtype
    POINT = "POINT"


@dataclass(slots=True)
class Entity:
    id: str
    sheet: str
    layer: str  # raw layer name from source
    layer_norm: str  # Revit UID / project prefix+suffix stripped
    kind: Kind
    pts: list[Point]
    closed: bool = False
    text: str | None = None
    block: str | None = None  # normalized block name if this came out of an INSERT
    block_raw: str | None = None
    rank: Rank = Rank.GEOMETRIC
    attrs: dict[str, Any] = field(default_factory=dict)
    source: dict[str, Any] = field(default_factory=dict)  # handle, file, owner, etc.

    # -- geometry helpers (drawing units) --------------------------------------------------
    def length(self) -> float:
        if self.kind in (Kind.TEXT, Kind.POINT, Kind.DIMENSION):
            return 0.0
        pts = self.pts + ([self.pts[0]] if self.closed and len(self.pts) > 2 else [])
        return sum(math.dist(a, b) for a, b in zip(pts, pts[1:], strict=False))

    def area(self) -> float:
        """Shoelace area (absolute). Only meaningful for closed entities."""
        if not self.closed or len(self.pts) < 3:
            return 0.0
        s = 0.0
        n = len(self.pts)
        for i in range(n):
            x1, y1 = self.pts[i]
            x2, y2 = self.pts[(i + 1) % n]
            s += x1 * y2 - x2 * y1
        return abs(s) / 2.0

    def bbox(self) -> tuple[float, float, float, float] | None:
        if not self.pts:
            return None
        xs = [p[0] for p in self.pts]
        ys = [p[1] for p in self.pts]
        return (min(xs), min(ys), max(xs), max(ys))


@dataclass(slots=True)
class Units:
    code: int  # DXF $INSUNITS code, or -1 when inferred
    name: str  # "inch", "foot", "millimeter", ...
    to_feet: float  # multiply drawing units by this to get feet
    source: str  # "header:$INSUNITS" | "assumed" | "titleblock" | "two-point"
    rank: Rank


@dataclass(slots=True)
class Sheet:
    id: str  # "<file-stem>:<layout>"
    file: str
    layout: str  # "Model" or paperspace layout name
    space: str  # "MODEL" | "PAPER"
    extents: tuple[float, float, float, float] | None
    entity_count: int


@dataclass(slots=True)
class LayerInfo:
    name: str
    norm: str
    count: int
    lf: float  # total open-entity length, drawing units
    kinds: dict[str, int]


@dataclass(slots=True)
class BlockInfo:
    name: str
    norm: str
    inserts: int
    children: int  # entities emitted per insert (after recursive expansion)


@dataclass(slots=True)
class RFI:
    id: str
    subject: str
    detail: str
    sheet: str | None = None
    blocking: bool = True


@dataclass(slots=True)
class Interrogation:
    """Phase 1 output. Runs before any extraction; nothing downstream may run without it."""

    units: Units
    header_extents: tuple[float, float, float, float] | None
    computed_extents: tuple[float, float, float, float] | None
    sheet_count: int
    layers: list[LayerInfo]
    blocks: list[BlockInfo]
    entity_kinds: dict[str, int]
    dxf_version: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class IRDocument:
    run_mode: str
    snap_tol: float  # drawing units (converted from the 1/32" CLI arg)
    snap_tol_in: float  # the CLI value, inches at drawing scale
    source_type: str  # "dxf" | "pdf"
    source_path: str
    ir_version: str
    units: Units
    sheets: list[Sheet]
    entities: list[Entity]
    interrogation: Interrogation
    rfis: list[RFI] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    log: list[str] = field(default_factory=list)

    @classmethod
    def new(cls, **kw: Any) -> IRDocument:
        kw.setdefault("run_mode", RUN_MODE)
        kw.setdefault("ir_version", IR_VERSION)
        return cls(**kw)

    # -- serialization ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # keep run_mode first in the file: "run mode declared at the top of every output"
        ordered = {"run_mode": d.pop("run_mode"), "snap_tol_in": d.pop("snap_tol_in")}
        ordered.update(d)
        return ordered

    def to_json(self, indent: int | None = 1) -> str:
        return json.dumps(self.to_dict(), indent=indent, default=_json_default)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> IRDocument:
        d = dict(d)
        d["units"] = _units(d["units"])
        d["sheets"] = [Sheet(**_tup(s, "extents")) for s in d["sheets"]]
        d["entities"] = [_entity(e) for e in d["entities"]]
        i = d["interrogation"]
        d["interrogation"] = Interrogation(
            units=_units(i["units"]),
            header_extents=_t4(i["header_extents"]),
            computed_extents=_t4(i["computed_extents"]),
            sheet_count=i["sheet_count"],
            layers=[LayerInfo(**x) for x in i["layers"]],
            blocks=[BlockInfo(**x) for x in i["blocks"]],
            entity_kinds=i["entity_kinds"],
            dxf_version=i.get("dxf_version"),
            warnings=i.get("warnings", []),
        )
        d["rfis"] = [RFI(**r) for r in d.get("rfis", [])]
        return cls(**d)

    @classmethod
    def from_json(cls, s: str) -> IRDocument:
        return cls.from_dict(json.loads(s))

    # -- convenience --------------------------------------------------------------------
    def entities_on(self, layer_norm: str, sheet: str | None = None) -> list[Entity]:
        return [
            e for e in self.entities if e.layer_norm == layer_norm and (sheet is None or e.sheet == sheet)
        ]


def _json_default(o: Any) -> Any:
    if isinstance(o, IntEnum | StrEnum):
        return o.value
    raise TypeError(f"not JSON serializable: {type(o).__name__}")


def _t4(v: Any) -> tuple[float, float, float, float] | None:
    return None if v is None else (float(v[0]), float(v[1]), float(v[2]), float(v[3]))


def _tup(d: dict[str, Any], key: str) -> dict[str, Any]:
    d = dict(d)
    d[key] = _t4(d.get(key))
    return d


def _units(d: dict[str, Any]) -> Units:
    d = dict(d)
    d["rank"] = Rank(d["rank"])
    return Units(**d)


def _entity(d: dict[str, Any]) -> Entity:
    d = dict(d)
    d["kind"] = Kind(d["kind"])
    d["rank"] = Rank(d["rank"])
    d["pts"] = [(float(p[0]), float(p[1])) for p in d["pts"]]
    return Entity(**d)


def json_schema() -> dict[str, Any]:
    """Minimal JSON Schema for out/*.ir.json. Hand-maintained; keep in sync with dataclasses."""
    pt = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}
    ext = {"type": ["array", "null"], "items": {"type": "number"}, "minItems": 4, "maxItems": 4}
    units = {
        "type": "object",
        "required": ["code", "name", "to_feet", "source", "rank"],
        "properties": {
            "code": {"type": "integer"},
            "name": {"type": "string"},
            "to_feet": {"type": "number"},
            "source": {"type": "string"},
            "rank": {"type": "integer", "minimum": 1, "maximum": 5},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": f"bdc-takeoff IR {IR_VERSION}",
        "type": "object",
        "required": [
            "run_mode",
            "snap_tol_in",
            "snap_tol",
            "source_type",
            "source_path",
            "ir_version",
            "units",
            "sheets",
            "entities",
            "interrogation",
            "rfis",
            "assumptions",
            "log",
        ],
        "properties": {
            "run_mode": {"type": "string"},
            "snap_tol_in": {"type": "number"},
            "snap_tol": {"type": "number"},
            "source_type": {"enum": ["dxf", "pdf"]},
            "source_path": {"type": "string"},
            "ir_version": {"const": IR_VERSION},
            "units": units,
            "sheets": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "file", "layout", "space", "extents", "entity_count"],
                    "properties": {
                        "id": {"type": "string"},
                        "file": {"type": "string"},
                        "layout": {"type": "string"},
                        "space": {"enum": ["MODEL", "PAPER"]},
                        "extents": ext,
                        "entity_count": {"type": "integer"},
                    },
                },
            },
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "sheet", "layer", "layer_norm", "kind", "pts", "closed", "rank"],
                    "properties": {
                        "id": {"type": "string"},
                        "sheet": {"type": "string"},
                        "layer": {"type": "string"},
                        "layer_norm": {"type": "string"},
                        "kind": {"enum": [k.value for k in Kind]},
                        "pts": {"type": "array", "items": pt},
                        "closed": {"type": "boolean"},
                        "text": {"type": ["string", "null"]},
                        "block": {"type": ["string", "null"]},
                        "block_raw": {"type": ["string", "null"]},
                        "rank": {"type": "integer", "minimum": 1, "maximum": 5},
                        "attrs": {"type": "object"},
                        "source": {"type": "object"},
                    },
                },
            },
            "interrogation": {
                "type": "object",
                "required": [
                    "units",
                    "header_extents",
                    "computed_extents",
                    "sheet_count",
                    "layers",
                    "blocks",
                    "entity_kinds",
                ],
                "properties": {
                    "units": units,
                    "header_extents": ext,
                    "computed_extents": ext,
                    "sheet_count": {"type": "integer"},
                    "layers": {"type": "array"},
                    "blocks": {"type": "array"},
                    "entity_kinds": {"type": "object"},
                    "dxf_version": {"type": ["string", "null"]},
                    "warnings": {"type": "array", "items": {"type": "string"}},
                },
            },
            "rfis": {"type": "array"},
            "assumptions": {"type": "array", "items": {"type": "string"}},
            "log": {"type": "array", "items": {"type": "string"}},
        },
    }

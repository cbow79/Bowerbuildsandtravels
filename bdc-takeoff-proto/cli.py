"""bdc-takeoff CLI. Slice 1 implements `ingest`; other commands are scope-guarded stubs."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from takeoff import DEFAULT_SNAP_IN
from takeoff.ingest_dxf import NameRules, ingest_dir
from takeoff.ir import IRDocument


@click.group()
def main() -> None:
    """BDC takeoff engine prototype."""


@main.command()
@click.argument("src", type=click.Path(exists=True, path_type=Path))
@click.option("--out", "out_dir", type=click.Path(path_type=Path), default=Path("out"), show_default=True)
@click.option("--name", default=None, help="output stem; default <dirname>_<type>")
@click.option(
    "--snap",
    "snap_in",
    type=float,
    default=DEFAULT_SNAP_IN,
    show_default=True,
    help="snap tolerance, inches at drawing scale",
)
@click.option(
    "--flatten",
    "flatten_in",
    type=float,
    default=0.01,
    show_default=True,
    help="curve flattening max deviation, inches",
)
@click.option("--merge/--no-merge", default=True, show_default=True, help="gap-merge collinear lines")
@click.option("--strip-prefix", multiple=True, help="layer/block prefix to strip (e.g. Q-)")
@click.option("--strip-suffix", multiple=True, help="layer/block suffix to strip (e.g. -Drexel)")
def ingest(
    src: Path,
    out_dir: Path,
    name: str | None,
    snap_in: float,
    flatten_in: float,
    merge: bool,
    strip_prefix: tuple[str, ...],
    strip_suffix: tuple[str, ...],
) -> None:
    """Ingest a DXF file/dir → out/<name>.ir.json + Phase 1 interrogation report."""
    kind = _detect(src)
    if kind != "dxf":
        raise click.ClickException("PDF ingest is Slice 2; only DXF is implemented in this slice.")
    rules = NameRules(strip_prefix=tuple(strip_prefix), strip_suffix=tuple(strip_suffix))
    doc = ingest_dir(src, snap_in=snap_in, flatten_in=flatten_in, merge_gaps=merge, rules=rules)
    stem = name or f"{(src if src.is_dir() else src.parent).name}_{kind}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{stem}.ir.json"
    out.write_text(doc.to_json())
    click.echo(report(doc))
    click.echo(f"\nwrote {out}  ({out.stat().st_size / 1024:.0f} KB)")
    if doc.rfis:
        sys.exit(2)


def _detect(src: Path) -> str:
    if src.is_file():
        return src.suffix.lower().lstrip(".")
    if any(src.glob("*.dxf")):
        return "dxf"
    if any(src.glob("*.pdf")):
        return "pdf"
    raise click.ClickException(f"no .dxf or .pdf under {src}")


def report(doc: IRDocument) -> str:
    """Phase 1 interrogation report. Dense; run mode first."""
    i = doc.interrogation
    u = doc.units
    ft = u.to_feet
    L: list[str] = []
    L.append(f"RUN MODE: {doc.run_mode}   source={doc.source_type}   ir={doc.ir_version}")
    L.append(f'snap_tol={doc.snap_tol_in:.5g}" ({doc.snap_tol:.5g} {u.name})   src={doc.source_path}')
    L.append("")
    L.append("PHASE 1 INTERROGATION")
    L.append(f"  units     : {u.name} (code {u.code}) ×{ft:.6g}→ft   src={u.source}   rank={u.rank.value}")
    L.append(f"  dxf ver   : {i.dxf_version}")
    L.append(f"  extents   : header={_ext(i.header_extents, ft)}")
    L.append(f"              computed={_ext(i.computed_extents, ft)}")
    L.append(f"  sheets    : {i.sheet_count}")
    for s in doc.sheets:
        L.append(f"              {s.id:<40} {s.space:<5} n={s.entity_count:<6} {_ext(s.extents, ft)}")
    L.append(f"  entities  : {sum(i.entity_kinds.values())}  {i.entity_kinds}")
    L.append(f"  layers    : {len(i.layers)}")
    L.append(f"              {'layer':<32} {'norm':<24} {'n':>6} {'LF(ft)':>10}  kinds")
    for ly in i.layers:
        L.append(f"              {ly.name:<32} {ly.norm:<24} {ly.count:>6} {ly.lf * ft:>10.1f}  {ly.kinds}")
    L.append(f"  blocks    : {len(i.blocks)}")
    for b in i.blocks:
        L.append(f"              {b.name:<40} {b.norm:<24} inserts={b.inserts:<5} children={b.children}")
    if i.warnings:
        L.append("  warnings  :")
        L.extend(f"              {w}" for w in i.warnings)
    if doc.assumptions:
        L.append("  assumptions:")
        L.extend(f"              {a}" for a in doc.assumptions)
    L.append(f"  RFIs      : {len(doc.rfis)}")
    for r in doc.rfis:
        L.append(f"              {r.id} {r.subject}: {r.detail}")
    L.append("  log       :")
    L.extend(f"              {x}" for x in doc.log)
    return "\n".join(L)


def _ext(e: tuple[float, float, float, float] | None, ft: float) -> str:
    if e is None:
        return "none"
    w, h = (e[2] - e[0]) * ft, (e[3] - e[1]) * ft
    return f"({e[0]:.2f},{e[1]:.2f})–({e[2]:.2f},{e[3]:.2f})  {w:.1f}ft × {h:.1f}ft"


@main.command(name="compare-ir")
@click.argument("a", type=click.Path(exists=True))
@click.argument("b", type=click.Path(exists=True))
def compare_ir(a: str, b: str) -> None:
    """Slice 2: per-pseudo-layer diff of two IR files (Q1)."""
    raise click.ClickException("compare-ir is Slice 2 — not built yet (scope guard).")


@main.command()
@click.argument("ir", type=click.Path(exists=True))
def areas(ir: str) -> None:
    """Slice 3: polygonize → rooms."""
    raise click.ClickException("areas is Slice 3 — not built yet (scope guard).")


@main.command()
@click.argument("ir", type=click.Path(exists=True))
def roof(ir: str) -> None:
    """Slice 4: roof planes → pitch bind → derived LF (Q2)."""
    raise click.ClickException("roof is Slice 4 — not built yet (scope guard).")


@main.command()
def test() -> None:
    """Slice 5: run all fixtures against truth.json."""
    raise click.ClickException("test harness is Slice 5 — not built yet (scope guard).")


if __name__ == "__main__":
    main()

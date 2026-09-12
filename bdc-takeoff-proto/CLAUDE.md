# bdc-takeoff-proto

Standalone prototype of BDC's plan takeoff engine. NOT the production module.
Read spec/bower-takeoff-module-spec-v2.md and spec/bower-takeoff-module-spec.md (v1) first.

## Non-negotiables
- One IR for all inputs. Never fork extraction logic by source type.
- Quantities never contain waste, coverage, or pricing. Markers are measured quantities only.
- Unknowns are explicit blank line items + RFI entries. Never estimate silently.
- Every detection carries a rank (1 SCHEDULE_VERIFIED … 5 UNRESOLVED).
- Run mode declared at the top of every output.
- Roof pitch is never defaulted. Unresolved → RFI.
- Phase 1 interrogation (units, scale, extents, layers, sheet count) runs before any extraction.

## Scope guard
Do not build: UI, viewer, database, auth, assemblies, set-compare, raster CV, review queue.
If a task seems to need one of these, stop and say so.

## Existing code
takeoff/ingest_dxf.py is ported from working ezdxf code. Preserve its behavior; tests in
tests/test_ir_parity.py compare against it.

## Conventions
- Python 3.12, pyproject, ruff. Shapely 2.x. PyMuPDF for PDF (get_drawings, get_text("dict")).
- Excel via openpyxl with tabs: Summary, Areas, Roof, Confidence, RFI, Assumptions.
- Overlays via matplotlib to out/*.png. Always produce one when geometry is ambiguous.
- Fixtures in fixtures/<project>/{dxf,pdf,csv,truth.json}. Never modify fixtures.
- Dense technical output. No generic construction explanations.

## Snap tolerance
Default 1/32" at drawing scale. It is a per-project CLI arg. Log the value used in every output.

## Build order
Slice 1 ingest+IR (done) → 2 PDF ingest + compare-ir → 3 areas → 4 roof → 5 export + test harness.
Work one slice per session. Commit after each slice passes its check.

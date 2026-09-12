# BDC Takeoff Engine — Prototype & Test Plan

**Purpose:** prove the extraction engine on real BDC plan sets *before* any bower.build work.
**Form:** standalone Python CLI repo, built with Claude Code, no UI, no database, no auth.
**Duration:** 2–3 weeks. If it can't be proven in that window, the v2 spec needs revisiting, not more time.
**Handoff:** Jack lifts `takeoff/` as a package into bower.build. The CLI and fixtures stay as the regression suite.

---

## 1. What the prototype must prove

Only two questions. Everything else is assumed buildable.

| # | Question | Pass condition |
|---|---|---|
| Q1 | Can a Revit-exported **vector PDF** be normalized to the same IR as the DXF export of the same sheet, closely enough that one extraction engine works on both? | Wall-line geometry from PDF and DXF of the same Quinn sheet agree within 1% on total LF and within 2% on every polygonized room area |
| Q2 | Can **roof planes** be detected, bound to a pitch, and turned into true SF + eave/ridge/hip/valley/rake LF without hand digitization? | Markiewicz roof (the RFI-3 case) produces a plane list where every plane has a pitch or an explicit RFI, and derived quantities match a roofer's bid breakdown within 3% |

Secondary (nice to know, not gating):
- Q3: Does the pseudo-layer clustering on vector PDFs separate roof linework, dimension strings, and text cleanly on all three sets?
- Q4: How often does polygonization need the snap tolerance adjusted per project?

**Not in the prototype:** viewer, review queue, assemblies, set-compare, raster path, bower.build anything. Resist adding them.

---

## 2. Fixtures

Use the three sets already hand-verified. They are the ground truth.

```
fixtures/
  quinn/           # happy path — double-line paired-face Revit-Enscape
    dxf/           # existing DXF exports
    pdf/           # SAME sheets exported from Revit as vector PDF
    csv/           # Revit Room/Door/Window schedules
    truth.json     # hand takeoff: room areas by level, wall LF, roof planes if available
  markiewicz/      # adversarial — single-line centerlines, Q- prefix, -Drexel suffix
    dxf/ pdf/ csv/ truth.json
  richbower/       # supersession case — 9-4-26 set only for now
    dxf/ pdf/ truth.json
```

**Action for Chris before anything is coded:** get the vector PDF exports of the Quinn sheets from the drafting team, from the same Revit issue as the DXFs. Q1 cannot be tested without them. Also pull one roofer's itemized bid on any completed job whose roof plan you have — that's the Q2 truth.

`truth.json` format — keep it flat:
```json
{
  "issue_date": "2026-09-04",
  "rooms": [{"level": "Main", "name": "Kitchen", "sf": 312.5}],
  "walls": {"ext_lf": 214.0, "int_lf": 388.5},
  "roof": {
    "planes": [{"id": "P1", "plan_sf": 753.7, "pitch": "8:12"}],
    "eave_lf": 168.0, "ridge_lf": 42.0, "hip_lf": 0, "valley_lf": 28.0, "rake_lf": 36.0
  }
}
```

---

## 3. Repo structure

```
bdc-takeoff-proto/
  CLAUDE.md                 # standing instructions for Claude Code (see §6)
  pyproject.toml            # pymupdf, ezdxf, shapely, openpyxl, matplotlib, click
  takeoff/
    ir.py                   # IR dataclasses + JSON schema (v1 spec §2.1)
    ingest_dxf.py           # wraps EXISTING ezdxf code — port, don't rewrite
    ingest_pdf.py           # PyMuPDF get_drawings() → IR, pseudo-layer clustering
    scale.py                # native / titleblock / two-point + dimension-string verification
    areas.py                # polygonize → rooms (v1 spec §4)
    roof.py                 # skeleton classify → planes → pitch bind → derived LF (v1 spec §5)
    confidence.py           # rank assignment
    export.py               # Excel workbook + JSON markers, existing tab conventions
    overlay.py              # matplotlib PNGs for eyeballing
  cli.py                    # bdc-takeoff ingest|areas|roof|compare-ir|test
  tests/
    test_ir_parity.py       # Q1: DXF vs PDF on same sheet
    test_areas.py           # rooms vs truth.json
    test_roof.py            # planes vs truth.json
  fixtures/                 # per §2
  out/                      # gitignored
```

Port the existing ezdxf logic (block interrogation, hatch branching, gap-merge centerlines, Revit UID stripping) into `ingest_dxf.py` first. It's the known-good half of Q1.

---

## 4. Build order (thin slices, each one runnable)

**Slice 1 (days 1–3): IR + DXF ingest + Phase 1 report.**
`bdc-takeoff ingest fixtures/quinn/dxf` → `out/quinn_dxf.ir.json` + interrogation report (units, extents, layer inventory, sheet count). This should reproduce what the current Claude.ai skill does, locally.

**Slice 2 (days 3–6): PDF ingest + IR parity.**
`bdc-takeoff ingest fixtures/quinn/pdf` → `out/quinn_pdf.ir.json`.
`bdc-takeoff compare-ir out/quinn_dxf.ir.json out/quinn_pdf.ir.json` → per-pseudo-layer diff: entity counts, total LF, bbox. **This is the Q1 answer.** If parity is bad, stop and diagnose before going further — likely causes are CTM handling, curve flattening tolerance, or Revit PDF export settings (vector vs raster, line merge).

**Slice 3 (days 6–9): areas.**
`bdc-takeoff areas out/quinn_pdf.ir.json --snap 0.03125` → rooms with SF, labels, rank, overlay PNG per level. Run against truth.json. Then run Markiewicz and expect it to raise RFI-1 rather than produce confident wrong numbers.

**Slice 4 (days 9–15): roof.**
`bdc-takeoff roof out/markiewicz_pdf.ir.json` → plane list, pitch source per plane (annotation / vision / UNRESOLVED), true SF, derived LF, overlay PNG with planes colored and ridge/hip/valley/eave/rake labeled. **This is the Q2 answer** and the slice most likely to overrun. Budget it accordingly.

**Slice 5 (days 15–18): export + test harness.**
Excel workbook (Summary / Areas / Roof / Confidence / RFI / Assumptions tabs), JSON markers in the v1 §7 format, `bdc-takeoff test` runs all three sets and prints a pass/fail table against truth.json.

---

## 5. Decision after the prototype

| Result | Decision |
|---|---|
| Q1 pass, Q2 pass | Jack starts v2 Phase 1 with `takeoff/` as the seed package. Roof engine is de-risked. |
| Q1 pass, Q2 fail | Ship areas + assemblies + set-compare to bower.build; roof stays assisted (estimator draws planes, engine does pitch + derived LF). Still worth building. |
| Q1 fail, Q2 pass | Vector PDF path is dropped; module becomes DXF/CSV-only + raster assisted. Ask drafting for DXF on every set. |
| Both fail | Subscribe to STACK or Togal for takeoff, build only the marker import + assemblies + unit-cost loop in bower.build. |

Every outcome still produces something worth building. The prototype decides *which* something.

---

## 6. CLAUDE.md for the prototype repo

Paste this as the repo's `CLAUDE.md` so Claude Code holds the rules across sessions.

```markdown
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
```

---

## 7. Kickoff prompt for Claude Code

```
Read CLAUDE.md, spec/bower-takeoff-module-spec-v2.md, and spec/bower-takeoff-module-spec.md.
We are building Slice 1 only: the IR dataclasses in takeoff/ir.py, the DXF ingester in
takeoff/ingest_dxf.py, and `bdc-takeoff ingest <dir>` in cli.py that writes out/<name>.ir.json
plus a Phase 1 interrogation report to stdout. Port the ezdxf logic from [path to existing code].
Do not touch PDF, areas, or roof yet. When done, run it on fixtures/quinn/dxf and show me the
interrogation report.
```

Work one slice per session. Commit after each slice passes its check.

---

## 8. What Chris does vs. what Claude Code does

**Chris:**
- Get Quinn vector PDFs from drafting (same issue as the DXFs)
- Pull one roofer's itemized bid + matching roof plan
- Fill `truth.json` for each fixture from the existing hand takeoffs / Excel workbooks
- Provide the current ezdxf code (from the Claude.ai project or wherever it lives)
- Review overlay PNGs at each slice — the eyeball check is the real test

**Claude Code:**
- Everything in §3–4

**Jack:**
- Nothing until §5 decides. Send him the spec now for awareness; don't ask for effort yet.

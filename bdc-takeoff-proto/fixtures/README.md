Ground-truth fixtures. NEVER modify files under fixtures/ once dropped in.

fixtures/
  quinn/       dxf/ pdf/ csv/ truth.json   happy path — double-line paired-face Revit-Enscape
  markiewicz/  dxf/ pdf/ csv/ truth.json   adversarial — single-line centerlines, Q- prefix, -Drexel suffix
  richbower/   dxf/ pdf/     truth.json    supersession case — 9-4-26 set only

Still needed (Chris): Quinn vector PDFs from the same Revit issue as the DXFs; one roofer's
itemized bid + matching roof plan; truth.json per project; the existing ezdxf code.

truth.json (flat):
{
  "issue_date": "2026-09-04",
  "rooms": [{"level": "Main", "name": "Kitchen", "sf": 312.5}],
  "walls": {"ext_lf": 214.0, "int_lf": 388.5},
  "roof": {
    "planes": [{"id": "P1", "plan_sf": 753.7, "pitch": "8:12"}],
    "eave_lf": 168.0, "ridge_lf": 42.0, "hip_lf": 0, "valley_lf": 28.0, "rake_lf": 36.0
  }
}

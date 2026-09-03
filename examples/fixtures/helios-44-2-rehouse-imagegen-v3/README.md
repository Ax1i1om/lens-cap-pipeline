# Helios-44-2 REHOUSE ImageGen v3 fixture

This fixture is a newly generated, independently reviewed REHOUSE test. It
exercises the complete provider boundary: a saved raster and prompt are bound
by hash to a source-backed brief, then the public deterministic pipeline
creates the relief, SCAD, and native one-piece 3MF. The artwork is a
style/specification match, not a promise of pixel-identical ImageGen output.

The design reads `58` first and `F2` second. It uses broad black, charcoal,
gray, and warm-ivory fields, continuous optical arcs, calibration marks, and
stepped contour bands. There is no lens barrel, glass, iris, photograph,
halftone texture, or film still. `REHOUSED CINEMA` is a qualified family-level
cue, not an official production mark.

## Source and cultural anchor

The [Zenit-E manual](https://www.zenitcamera.com/mans/zenit-e/zenit-e-eng.html)
identifies the Helios-44-2 as a 58 mm f/2 lens. The [ASC account of *Dune:
Part Two*](https://theasc.com/article/expanding-view-dune-part-two/) reports
IronGlass rehoused Helios-44-2 lenses in testing and production; the [ASC
account of *The Batman*](https://theasc.com/article/greig-fraser-batman/)
describes the related Soviet-lens rehousing context. Those are qualified
family-level references. The graphic translates them into original geometry,
not a copied frame, character, logo, or claim about every original specimen.

## Mechanical scenario

This smoke fixture uses a synthetic 95 mm rehoused front envelope, no foam,
0.2 mm nozzle, and the default `light_tapered` inner friction ribs. The 95 mm
value is a mating-cylinder test input, not a filter-size assumption. The model
report labels the ribs as a guide/clearance aid when nominal interference is
not demonstrated; a same-material coupon is still required before relying on
retention.

## Retained result

`artifacts/helios-44-2-rehouse-imagegen-v3-95mm-native.3mf` is the retained
native OpenSCAD Manifold Core package produced from this exact raster and job.
Its sidecar manifest records hashes and package verification, while the
adjacent `*-3mf-release.json` proves the strict design-brief and same-canvas
gates were run by the canonical bridge. Its mesh-level rib audit detects all
12 expected positions at the start, three interior sections, and end of the rib
span, plus 15 full-height axial contact columns per rib and connected full-width
tip faces; this proves the ribs survived into the integrated mesh, not that they
grip correctly.
The material audit also requires non-empty black, gray, and ivory triangle
assignments and rejects any used colour outside the current job palette. It is
one integrated mesh, unsliced; inspect it in Bambu Studio and print a coupon
before using it on hardware.

The adjacent `*-bambu-project.3mf` is the current Bambu **export-mode** profile
provenance fixture. Its manifest records the exact machine/process/filament
input and post-run hashes, every resolved inheritance file/hash, and a passed
`effective_profile_audit` against the project settings. It deliberately has no
G-code (`gcode_bytes = 0`) and is not slice evidence. A G-code claim requires a
separate current `--bambu slice` run whose manifest also contains a passed
`verification.slice_audit`.

## Reproduce

```sh
python3 scripts/smoke_rehouse.py \
  --fixture examples/fixtures/helios-44-2-rehouse-imagegen-v3 \
  --bambu never --require-external --json

# Reproduce the retained, unsliced Bambu profile-provenance project:
python3 scripts/smoke_rehouse.py \
  --fixture examples/fixtures/helios-44-2-rehouse-imagegen-v3 \
  --bridge-job jobs/95mm/job.toml --bambu export --require-external \
  --machine-profile /path/to/machine.json \
  --process-profile /path/to/process.json \
  --filament-profile /path/to/filament.json --json
```

To refresh the retained package intentionally, use
`--artifact-dir examples/fixtures/helios-44-2-rehouse-imagegen-v3/artifacts
--force-artifacts` on a host with OpenSCAD. The runner copies only the approved
source packet into a temporary clean fixture and never reuses old `out/`
directories.

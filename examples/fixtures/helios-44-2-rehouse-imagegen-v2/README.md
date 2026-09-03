# Helios-44-2 REHOUSE ImageGen v2 fixture

This is a fresh ImageGen-to-3MF acceptance fixture. The source artwork was
generated during the clean-room audit with the committed `prompt.txt`, then
visually approved before any raster processing. It is a style/specification
match rather than a promise of pixel-identical provider output: `58` is the
first read, `F2` the second, and the remaining labels are restrained. The
composition is a complete black/charcoal/gray/warm-ivory circle made from
broad fields, lines, searchlight geometry, contour bands, and registration
blocks. It intentionally contains no lens barrel, glass, iris, film still,
character, or official movie mark.

## Why these motifs are allowed

The [Zenit-E manual](https://www.zenitcamera.com/mans/zenit-e/zenit-e-eng.html)
identifies the supplied Helios-44-2 as a 58 mm f/2 lens. The [American Society
of Cinematographers account of *Dune: Part Two*](https://theasc.com/article/expanding-view-dune-part-two/)
reports IronGlass rehoused Helios-44-2 lenses in testing and production and
connects that rehousing family to earlier *The Batman* work. The [ASC Batman
account](https://theasc.com/article/greig-fraser-batman/) describes the
Soviet-lens/IronGlass production context. These are qualified family-level
history cues, not a claim that this original still-lens specimen appears in a
particular shot. The art translates them into abstract geometry and does not
copy a film frame or logo.

## Adapter-envelope matrix

The jobs exercise the measurement rule that a cap grips the actual outside
cylinder, not a nearby filter-thread number. For the two adapter cases the
derived diameter is `nominal ring + 2 × radial wall`:

| job | synthetic handoff | measured mating diameter |
| --- | --- | ---: |
| `95mm` | nominal rehoused front | 95.0 mm |
| `82mm` | 77 mm ring + 2.5 mm radial adapter wall | 82.0 mm |
| `77mm` | 72 mm ring + 2.5 mm radial adapter wall | 77.0 mm |

All jobs use a 0.2 mm nozzle, no foam, and the neutral `light_tapered` inner
wall retention ribs enabled by default (12 shallow integrated ribs). A real
user must replace the synthetic values with a measured mating surface. The
face and relief scale follow that measured diameter; there is no second relief
diameter or structure prompt.

## Retained outputs

The `artifacts/` directory contains the outputs from the same source art and
job TOMLs:

- `*-native.3mf`: OpenSCAD Manifold native Core packages, one integrated mesh
  part, unsliced. Each current native package has an adjacent
  `*-3mf-release.json` proving the job passed `scripts/build_3mf.py`.
- `*-assembly-standard.3mf`: explicit STL-to-Core diagnostic packages; their
  internal relief interfaces are retained as a topology diagnostic, not a
  printability claim.
- `95mm-bambu-slice.3mf`: a historical Bambu Studio compatibility snapshot
  with embedded G-code. Its stored print-settings label names the 0.10 mm
  Standard profile, but the embedded effective/G-code layer height is 0.20 mm.
  Do not use it as evidence that the named 0.10 mm process was applied.

Every package has a `.manifest.json` sidecar containing input/output hashes,
tool information, and the checks recorded when that snapshot was created. The
historical Bambu sidecar predates profile input/post-run hashes, inheritance
resolution, `effective_profile_audit`, and the persisted semantic
`verification.slice_audit`; G-code presence/byte count in that sidecar is not
current release evidence. Use the v3 fixture's Bambu export manifest for the
current multipart profile-provenance contract and
`tools/3mf_adapter/fixtures/fixture-cube-bambu-sliced.3mf` for current semantic
G-code-gate evidence; create a fresh job-specific slice with all three profiles
before making a current claim about this fixture. A valid
package or slicer result does not establish physical fit; print a same-material
coupon and measure it.

## Reproduce

From a fresh checkout (after installing the test extras):

```sh
python3 scripts/smoke_rehouse.py \
  --fixture examples/fixtures/helios-44-2-rehouse-imagegen-v2 \
  --bambu never --require-external --json

# On a host with compatible Bambu Studio profiles, create new current evidence:
python3 scripts/smoke_rehouse.py \
  --fixture examples/fixtures/helios-44-2-rehouse-imagegen-v2 \
  --bambu slice --require-external \
  --machine-profile /path/to/machine.json \
  --process-profile /path/to/process.json \
  --filament-profile /path/to/filament.json --json
```

To refresh retained files intentionally, add
`--artifact-dir examples/fixtures/helios-44-2-rehouse-imagegen-v2/artifacts
--force-artifacts`. The runner copies only the approved art, prompt, brief,
and jobs into a temporary clean fixture, so old `out/` directories and chat
history cannot influence the build.

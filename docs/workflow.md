# Reproducible workflow

The repository separates deterministic artwork processing from mechanical CAD
and slicer adapters. This keeps a cloud image tool or a desktop slicer from
silently changing the approved composition.

```text
approved PNG + job.toml
          │
          ▼
 lens-cap process ──► process-master + role masks + colour masks + SVG + report
          │
          ▼
  CAD/relief adapter ──► SCAD/STL/3MF (explicit tool/version/command)
          │
          ▼
  slicer preview + fit coupon ──► independently recorded release evidence
```

From a fresh checkout, run `./scripts/bootstrap.py --dev` (or
`py -3 scripts/bootstrap.py --dev` on Windows) to create a
project-local `.venv` and install the CLI without touching the system Python.
Activate `.venv/bin/activate` on macOS/Linux or
`.venv\Scripts\Activate.ps1` in Windows PowerShell; the script is idempotent
and may be run again after dependency changes.
For release comparisons, run `./scripts/bootstrap.py --dev --locked`
with `uv` installed; this uses the committed `uv.lock`. The unlocked helper
uses the compatible version ranges and is convenient for ordinary development,
but it is not a byte-level environment pin.

## 1. Declare one job

Run `lens-cap init jobs/name/job.toml --source ...` and edit the generated
file. For a standalone relief, add `--face-diameter <mm>`; for a fitted cap,
`--measured-diameter <mm>` is required. Add `--foam-thickness <mm>` only when
an uncompressed foam liner (including adhesive) is actually planned; for bare
plastic, declare `foam_liner_status = "none"` (and the bare clearance) in the
`[fit]` table. Add `--no-friction-ribs` only when a smooth wall is explicitly
chosen; otherwise ribs remain enabled by the documented default (use
`--friction-ribs` to record an explicit enabled choice). The measured
mating diameter automatically becomes the face diameter.
Record the current lens identity and artwork
provenance in the accompanying manifest. For a fitted cap, the face diameter
must come from the actual gripping outside diameter; do not copy a nominal
filter standard or an older job's 95 mm value. Foam status, thickness and
compression are mechanical inputs for the downstream cap generator. Inner-wall
friction ribs are enabled by default; ask whether the user wants to retain
them, record the answer, and use `--no-friction-ribs` (or
`fit.friction_ribs_enabled = false`) for a smooth wall. With foam, the default
ribs are only a light extra-grip aid, so a fit coupon remains mandatory.
Choose the neutral `fit.friction_rib_profile` when the rib silhouette matters:
`light_tapered` is the compatibility default, while `wide_tapered` gives six
broad, tapered wedges for a reference-like result. Explicit rib dimensions
override a profile's omitted defaults. A user-supplied SCAD/3MF or platform
page is reference material only; record provenance and regenerate geometry from
the current measurements rather than copying its mesh.

An opaque square source needs an explicit `[circle]` center/radius. A
transparent source may use alpha as its exclusion mask only when that alpha is
approved and documented.

## 2. Process the artwork

```sh
lens-cap process jobs/name/job.toml --force
```

The core performs only deterministic transformations:

1. read the source without modifying it;
2. apply the declared `none`, `median`, or `gaussian` prefilter;
3. classify pixels against the declared palette using `int32` differences and
   `int64` squared-distance accumulation;
4. apply explicit detector rules, if declared;
5. sample labels onto a normalized square canvas with nearest-neighbour maps;
6. clip the circle and reserve a base-only safe border;
7. remove only isolated components below the declared area/dimension limits;
8. emit an RGBA process master, role masks, per-colour masks, and same-canvas
   SVGs.

Use a lossless PNG/PPM source when the output must compare byte-for-byte across
machines. JPEG is accepted, but its Pillow/libjpeg decoder version is part of
the effective environment and is therefore recorded as a release assumption.

The process report includes source/config hashes, pixel counts, component
changes, SVG rectangle counts, polarity samples from the raw source, and
boolean safety checks. A failed check stops the pipeline before CAD.
It also records the Python, NumPy, Pillow and byte-order runtime used for
decoding/classification; retain this block when comparing artifacts across
hosts.

## 3. Model and print (explicit adapter)

The generated SVGs are the stable handoff to OpenSCAD, ChromaCanvas/HueForge,
Blender, or another relief adapter. The bundled model adapter can be invoked
with `lens-cap model`; it imports every mask on the same canvas and writes a
parameterized SCAD plus `geometry-report.json`. `lens-cap export-openscad` asks
the explicitly installed OpenSCAD executable to export selectors, while
`lens-cap bambu-handoff` writes a version-neutral Bambu manifest. (`mesh` and
`handoff` are compatibility aliases.) Never redraw or
retype artwork in a CAD script. Keep the mechanical body parameterized by the
current measured diameter and liner plan. Generate and measure a short fit
ring before a full multicolour print. The bundled `wide_tapered` profile is
mechanical geometry only and must not alter the approved focal-length/aperture
artwork or introduce brand marks.

For binary STL exports, the OpenSCAD adapter sorts complete triangle records
into a deterministic order after the renderer finishes. Geometry, normals and
attribute bytes are preserved, but renderer-native raw byte order is not; the
report's `format_check.canonicalization` and the resulting file hash document
this normalization. A reproducibility check must compare the canonicalized
file, not an unprocessed OpenSCAD byte stream.

The Bambu handoff exposes mutually exclusive `print_sets`: choose
`integrated_monochrome` or `multicolor_components` for the cap, and print
`fit_coupon` separately for measurement. Do not import both cap sets at once;
the legacy flat `stl_inputs` array is retained only for compatibility.

For each external tool, save its exact version, command, profile and output
hash in the job manifest. `lens-cap doctor --json` reports local
OpenSCAD/Bambu availability without creating an artifact. The project does
not silently upload to MakerWorld, launch a browser, or mutate a cloud project.

## 4. Validate and publish

```sh
lens-cap build jobs/name/job.toml --force --export-openscad --bambu-handoff
lens-cap validate jobs/name/job.toml
```

Validation checks source/artifact hashes, alpha semantics, mask overlap and
coverage, safe-border containment, SVG dimensions, model geometry when
present, and config validity. It must follow a successful `process`/`build`
run; it is not a config-only lint. Add `--external` to run the OpenSCAD compile
probe; missing desktop tools are reported as `UNVERIFIABLE`, not as a pass.
Physical fit is also `UNVERIFIABLE` until a coupon is measured.

Publish a job only with its normalized config, process report, source lock,
provenance/licence notes, generated CAD source, slicer preview and fit result.
Keep private or unlicensed artwork out of the repository; use release assets
or a reproducible download record for large STL/3MF/G-code files.

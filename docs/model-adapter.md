# Model adapter contract

The process core stops at explicit, same-canvas masks and SVGs. A model
adapter may turn those assets into OpenSCAD/STL/3MF, but it must preserve the
following contract:

## Inputs

* `process-master.png` and `process-report.json` from the same run;
* all `masks/*.png`/`vector/*.svg` files named in the report;
* `config.normalized.json` and the mechanical manifest;
* a declared adapter version and external-tool versions.

The adapter must reject a missing or mismatched source hash, a changed canvas,
or a palette file not named in the report. It must never load a neighbouring
job's asset by filename convention.

## Geometry rules

* Import every mask with one common canvas origin and viewBox.
* Treat `base` as a continuous substrate; only `relief` roles become positive
  heights. Do not turn `outside` pixels into geometry.
* Keep face diameter, measured mating diameter, liner thickness, compression,
  wall and bottom dimensions as explicit parameters.
* Generate a fit ring/coupon and report its parameters before a full cap.
* Preserve text/motif positions. Any mirror or print-orientation transform must
  be applied at the CAD/build layer and recorded, never baked into the art.

## Outputs and gates

An adapter should emit:

```text
model/<job>.scad or .blend
model/mesh/<material>.stl (optional)
model/geometry-report.json
model/external-openscad-report.json (optional)
model/bambu-handoff.json (optional)
preview-top.png / preview-iso.png
```

`geometry-report.json` must include input hashes, dimensions, relief heights,
material-slot bindings, and geometry/mesh validation status. A 3MF handoff must
also record slicer version, printer/nozzle, layer height, orientation, support,
purge tower and color preview. If an external tool is unavailable, write
`UNVERIFIABLE` rather than a guessed pass.

When the OpenSCAD adapter writes a binary STL, it validates the byte-level
triangle records and deterministically sorts those complete records before
hashing/handing off the file. This preserves geometry, normals, and attribute
bytes but intentionally does not preserve the renderer's raw triangle order.
The per-part report records the result under
`format_check.canonicalization` (including whether the order changed); use the
post-canonicalization SHA-256 for provenance.

Before a printable release, run the public
scripts/audit_stl_projection.py (or an equivalent recorded adapter) once for
each positive-relief STL:

~~~sh
./bin/audit-stl-projection \
  --mesh ivory=model/mesh/job-ivory_relief.stl \
  --expected-mask ivory=masks/ivory.png \
  --canvas-size-mm 95 --tolerance-pixels 1 \
  --output-report model/projection-report.json \
  --output-dir model/projection-diff
~~~

The report compares the top-view XY footprint and writes a red/green diff for
missing or extra pixels. Use the same canvas size as the process/model report,
and record any intentional mirror in the command and report. This gate catches
the common white-border, translation, mirror, and wrong-color-mesh failures;
it does not prove manifoldness, slicing, or physical fit.

## Suggested adapter CLI

The bundled adapter exposes this stable shape without changing the process
core:

```sh
lens-cap model jobs/name/job.toml
lens-cap export-openscad jobs/name/job.toml --force
lens-cap bambu-handoff jobs/name/job.toml
lens-cap validate jobs/name/job.toml --external
```

The Bambu handoff lists mutually exclusive print sets. Choose
`integrated_monochrome` for one single-material STL, or
`multicolor_components` when assigning a filament to the shared-canvas base
and relief STLs; use `fit_coupon` only for the short physical test ring. Do
not import the integrated assembly and its component set together, even
though the compatibility `stl_inputs` list contains every generated file.

`mesh` and `handoff` are retained as compatibility aliases. The modelling and
export stages require a measured mating diameter; use `lens-cap process` when
you only need audited artwork masks.

The model adapter remains replaceable: a future Blender or ChromaCanvas
implementation can consume the same audited art contract and write an
equivalent geometry report.

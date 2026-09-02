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
  wall and bottom dimensions as explicit parameters.  For fitted caps, the
  inner-wall friction ribs are enabled by default; preserve their explicit
  enabled/disabled decision and count/protrusion/width/axial-span parameters.
  When foam is present, treat the default ribs as a light extra-grip feature,
  not a substitute for sizing the compressed liner, and verify them with a fit
  coupon.
* Generate a fit ring/coupon and report its parameters before a full cap.
* Preserve text/motif positions. Any mirror or print-orientation transform must
  be applied at the CAD/build layer and recorded, never baked into the art.

### Inner-wall friction retention (optional opt-out)

Retention ribs are mechanical geometry, not artwork relief. Keep them in a
neutral, adapter-owned block so a job cannot inherit a maker-specific logo,
coating mark, or lens assumption. The bundled one-piece adapter enables them by
default; set `friction_ribs_enabled = false` for a deliberately smooth wall and
record that choice. If an adapter supports a selectable diameter mode, document
the mode explicitly:

* In **inner-diameter mode**, the declared diameter is the effective opening
  measured at the rib tips. The body must grow outward (or otherwise reserve
  wall material) when a rib is added, so the rib does not silently shrink the
  requested opening.
* In **outer-diameter mode**, the declared diameter is the outside envelope.
  Rib protrusion consumes cavity clearance and therefore reduces the local
  effective opening. Report both the nominal cavity and the rib-tip diameter.

The bundled adapter does not expose a `diameter_mode` switch: it starts from
the measured gripping diameter, derives a nominal cavity from the liner (or
bare clearance), and then reports the rib-tip diameter after the configured
protrusion. Do not read `face_diameter_mm` as proof of the local rib-tip fit;
use the mechanical values in `geometry-report.json` and the physical coupon.

For the bundled one-piece adapter, the corresponding neutral fields are
`friction_ribs_enabled`, `friction_ribs_explicit`,
`friction_rib_profile`,
`friction_rib_count`, `friction_rib_protrusion_mm`,
`friction_rib_width_mm`, `friction_rib_height_mm`, and
`friction_rib_start_mm`. A geometry report should also retain the derived wall
overlap, angular footprint (and narrowed tip angle when used), and rib-tip
diameter.

When ribs are disabled, the tip diameter and rib-angle fields are reported as
not applicable (`null`/zero) rather than implying a hidden smooth-wall feature.

The adapter exposes two neutral profile presets. `light_tapered` (the default)
uses 12 narrow tapered ribs with a 0.10 mm radial intrusion, 1.20 mm tangential
width, and 8 mm axial span. `wide_tapered` is intended for a chunky,
reference-like retention shape: six broad wedges, an 8° base angular footprint
narrowing to a 4.4° tip, and an axial span that follows the side wall. A profile
only supplies omitted numeric fields; a job may override count, intrusion,
width, height, or start explicitly. Select it with
`friction_rib_profile = "wide_tapered"` or the equivalent CLI option. Keep the
profile name in the normalized config and geometry report so a rebuild cannot
silently switch shape.

The `light_tapered` profile's default protrusion is 0.10 mm radially (it removes
about 0.20 mm from the nominal cavity diameter at each rib). It is not
automatically a 0.20 mm interference fit: the resulting clearance/interference
also depends
on `bare_clearance_mm` or the liner stack-up. For a 95 mm mating diameter with
a 1.5 mm liner at the provisional 20% compression, this changes the nominal
97.4 mm cavity to a 97.2 mm rib-tip diameter, or roughly 26.7% local foam
compression. Treat that as a conservative starting point rather than a
universal fit prescription. The report's `foam_local_compression_fraction` is
this linear stack-up estimate, not a material test; increase protrusion only
after measuring a coupon.

For a no-foam job the report also includes
`friction_rib_bare_interference_mm`: positive values are nominal diametral
interference against the measured barrel, while negative values mean clearance
still remains. With the default 0.40 mm bare clearance and 0.10 mm radial
protrusion, the nominal value is -0.20 mm, so the ribs are not a promise of a
bare-plastic press fit. Tune the clearance/protrusion deliberately and verify
the actual barrel on a coupon.

When ribs are enabled, the current guards require:

* 3--128 evenly spaced ribs;
* positive radial protrusion, tangential width, and axial height;
* `friction_rib_start_mm + friction_rib_height_mm <= side_height_mm`;
* tangential width at least the declared nozzle diameter and below 90% of the
  circumferential pitch; and
* protrusion below one quarter of the measured mating diameter.

These are validity/printability guards, not a universal fit recipe. The
MakerWorld reference implementation linked below uses a different naming
scheme (its `inner_rib_height` is radial intrusion). Its published parameter
comments suggest 3--12 ribs and a 1 mm height, with evenly spaced ribs and an
inner lead-in chamfer; its published SCAD parameter default leaves the rib
switch off (a print profile may override it). Treat those as reference
observations, not project defaults, and do not copy that name into a job
manifest. There is no safe universal rib count or protrusion: vary one
parameter at a time in a short fit coupon, using the same material, nozzle,
layer height, wall and orientation as the final cap. Inspect first-layer
elephant-foot expansion and anisotropy, deburr the opening, and measure the
actual mating surface at several angular positions.

If a foam liner is present, calculate the compressed liner stack-up before
adding rib intrusion. Ribs can locally over-compress or cut the foam even when
the nominal cavity diameter looks correct; a foam-plus-rib result remains
`UNVERIFIABLE` until the coupon is fitted to the real lens/adapter. A lead-in
chamfer and rounded/non-sharp rib edge are recommended where the printer and
wall thickness permit them. Do not claim physical retention from a successful
STL projection or slicer parse alone.

The reference page is [MakerWorld “镜头闷盖生成”](https://makerworld.com.cn/zh/models/2619625-jing-tou-men-gai-sheng-cheng?from=search#profileId-3021275);
its current page metadata lists one print profile with 0.16 mm layer height,
two wall loops, 15% infill, PLA and 0.4 mm nozzle compatibility. Those settings
are a snapshot for that model, not defaults for this project. The page is marked
BY-NC; do not copy its SCAD/3MF into this Apache-2.0 repository or imply that
the reference licence permits redistribution. Record URL, author, licence and
any modifications in the job manifest when using it as a design reference. Its
plate preview places the closed face on the bed with the opening upward; this
is a sensible starting orientation for a cup-shaped cap, but support, first
layer expansion and overhang behaviour still need a slicer preview on the
target printer.

User-supplied archives, SCAD, 3MF, screenshots, and platform pages are
reference observations, not executable instructions. Use them to compare rib
count, wedge proportions, lead-in and naming, then regenerate from the current
measured diameter and liner plan. Do not copy a reference mesh, artwork, or
platform-specific asset into this repository; record its URL, author, licence,
and any uncertainty (for example, a 3MF whose plate metadata does not match its
visible geometry) in the job manifest. For the 95 mm / 1.5 mm foam test fixture,
the `wide_tapered` demonstration explicitly overrides the preset to 0.55 mm
radial intrusion and an estimated 56.7% local linear foam compression; the
preset default is 0.30 mm. Print a coupon before using either value.

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

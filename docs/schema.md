# Job schema (version 1)

The stable core accepts TOML (`.toml`) or JSON (`.json`). Paths are resolved
relative to the config file, so a job directory can be moved or archived.

## Top-level fields

| Key | Required | Meaning |
| --- | --- | --- |
| `schema_version` | no | currently `1` |
| `job_slug` | yes | filename-safe identifier |
| `source_art` | yes | immutable approved raster (PNG/PPM recommended for byte-level reproducibility; JPEG and other Pillow-readable formats are accepted) |
| `output_dir` | no | generated derivatives, default `build` |
| `face_diameter_mm` | process/model | finished circular face/relief diameter; derived from `measured_diameter_mm` when omitted |
| `measured_diameter_mm` | model/fitted cap | actual outside diameter of the surface the cap grips |
| `grid_size` | no | square output grid, 64–4096, default 1000 |
| `nozzle_mm` | no | minimum feature reference, default 0.2; record whether explicit |
| `safe_border_mm` | no | base-only outer border, default 0 |
| `source_sha256` | no | expected SHA-256 for the immutable source (the process writes its own lock) |
| `assembly_mode` | no | `auto`, `integrated_part`, `separate_parts`, or `inlay`; model-stage hint |
| `metadata` | no | provenance, permissions, and physical verification notes; retained with derived flags |

`art_master` and `source` are accepted as migration aliases for `source_art`;
`face_target_mm` (including the legacy `[face]` table spelling) is accepted as
an alias for `face_diameter_mm`. The canonical normalized config is written to
`config.normalized.json`.

In that normalized file, absolute OpenSCAD/Bambu executable settings are
reduced to their basenames so the core config digest remains clone-portable;
the original TOML is still used to locate the tool, and the external adapter
report records its basename, version and output hashes. Other provenance
metadata should avoid machine-specific absolute paths when a cross-machine
digest is required.

In `[print]`, a path-like executable value (one containing a separator, an
explicit relative marker, or a filename extension) is resolved relative to the
job file. A bare command name such as `openscad` or `BambuStudio` is looked up
on `PATH`; any explicitly configured value is authoritative and a missing one
is reported as `UNVERIFIABLE` rather than replaced by another installation.

For a fitted cap, set `measured_diameter_mm` and omit
`face_diameter_mm` unless an explicit face-size override is intended. The core
does not infer either value from a nominal filter thread.

For release-grade byte comparisons, prefer a lossless PNG or PPM master and
the committed dependency lock. JPEG decoding depends on the Pillow/libjpeg
versions on the host; the source lock still detects substitution, but it cannot
make two different decoders produce identical pixels.

`fit` and `print` are optional nested tables. Their complete mechanical and
printer fields are shown below; omitting `fit` uses conservative defaults. The
20% provisional compression default applies only when a foam liner is present;
a bare-wall job defaults to zero compression. An actual fitted-cap release
should still record the liner decision explicitly.
Configs created before the friction-rib fields were introduced inherit the new
default (`friction_ribs_enabled=true`) when rebuilt; set it explicitly to
`false` if you need to reproduce a legacy smooth-wall model, then rerun the
process/model stages and record the migration.

## Circle and image processing

```toml
[circle]
center_px = [627, 624]       # required for an opaque master; alpha may infer it
radius_px = 619              # required for an opaque master; alpha may infer it
allow_outside = false        # keep false unless clipping is intentional

[prefilter]
name = "median"              # none, median, or gaussian
size = 5                     # odd integer, 3–15
radius = 0.8

[cleanup]
enabled = true
max_area_px = 8
max_dimension_px = 3
ring_px = 2
dominance = 0.6
apply_to = ["relief"]        # base, relief, or all
```

The circle is sampled onto a normalized square canvas with nearest-neighbour
labels. For an opaque source, declare both `center_px` and `radius_px`; the
core refuses to guess and therefore cannot silently recenter the artwork. A
binary-alpha source may infer a circle from its non-zero alpha bounding box,
but that inference is recorded in the process report and should still be
reviewed. `center_px` is `[x, y]` in the source pixel coordinate system and
`radius_px` is measured in source pixels; fractional values are allowed. Unless
`allow_outside = true` is explicitly documented, the declared circle must lie
inside the source bounds. The source image itself is never resized or
overwritten. Cleanup only changes isolated components meeting all declared
limits.

## Palette and geometry roles

Declare one TOML table per colour under `[palette.<name>]`:

```toml
[palette.black]
index = 0
rgb = [17, 18, 17]
role = "base"
height_mm = 0.0
required = true

[palette.ivory]
index = 1
rgb = [242, 231, 211]
role = "relief"
height_mm = 0.6
required = true
```

Indices must be contiguous from zero. Exactly one colour has `role = "base"`;
at least one has `role = "relief"`. `height_mm` is the positive extrusion
height for a downstream relief modeller (`base` must remain `0`); this core
stage does not invent a height map. Optional
`[palette.red.detect]` (channel, minimum and channel deltas) is an explicit
accent detector, never an inferred brand rule.

The generated `outside.png`, `base.png`, `relief.png`, `safe-border.png`,
per-colour masks and `role-overlay.png` are the semantic handoff to a CAD or
ChromaCanvas adapter. Their reports must show disjoint coverage and zero
positive relief in the base-only border.

## Stage requirements

The schema intentionally supports two scopes:

* `lens-cap process` needs a valid source, circle/palette and either
  `face_diameter_mm` or `measured_diameter_mm`; it emits only audited artwork
  derivatives.
* `lens-cap model`, `lens-cap export-openscad`, `lens-cap bambu-handoff`, and
  the modelling portion of `lens-cap build` require
  `measured_diameter_mm`. This is the actual cylindrical mating diameter, not
  a nominal filter-thread or a value copied from another job.

`lens-cap mesh` and `lens-cap handoff` remain aliases for the two explicit
adapter commands. `lens-cap validate` checks artifacts from a completed
process/model run and is not a config-only linter; use any stage command to
surface configuration parse errors before generating a release.

## Fitted-cap metadata

The process core accepts the mechanical fields below so a downstream model
adapter can use one normalized manifest:

```toml
measured_diameter_mm = 95.0
face_diameter_mm = 95.0             # omit to derive from measured diameter
assembly_mode = "auto"

[fit]
foam_liner_status = "foam"         # none or foam
liner_material = "closed-cell foam"
liner_thickness_mm = 1.5            # required with foam
compression_fraction = 0.20         # provisional when not measured
compression_is_assumption = true
wall_thickness_mm = 2.4
bottom_thickness_mm = 2.0
side_height_mm = 14.0
bare_clearance_mm = 0.40            # used when no foam
friction_ribs_enabled = true        # default; set false for a smooth inner wall
friction_ribs_explicit = false      # true when the user answered this choice
friction_rib_profile = "light_tapered" # light_tapered or wide_tapered
friction_rib_count = 12
friction_rib_protrusion_mm = 0.10   # radial intrusion; conservative light default
friction_rib_width_mm = 1.20        # tangential width of each rib
friction_rib_height_mm = 8.0        # axial rib span
friction_rib_start_mm = 1.0         # height above the open edge
retention_strategy = "auto"

[print]
layer_height_mm = 0.10
printer = "Bambu Lab A1 mini"
filament_slots = ["black", "gray", "ivory", "red"]
```

The fitted-cap intake asks for the actual mating outside diameter, whether/how
thick the foam liner is, and whether to keep the inner-wall friction ribs. The
rib choice defaults to enabled and is recorded in `friction_ribs_explicit` when
the user answers it; `--no-friction-ribs` produces a smooth wall.
`friction_rib_profile` selects a neutral parameter preset: `light_tapered` is
the compatibility default (12 ribs, 0.10 mm radial intrusion, 1.20 mm width,
8 mm axial span), while `wide_tapered` is a reference-like broad-wedge option
(6 ribs, 8° base angle narrowing to 4.4°, near-full side-wall span). Omitted
numeric rib fields are derived from the selected profile; explicit numeric
values win. Structure is resolved as `auto` by the model adapter. Physical fit
is `UNVERIFIABLE` until a test ring/coupon is printed and measured. The model
report also records the derived rib-tip diameter, signed bare-wall interference
(when no foam is used), and local foam compression; an intrusion that consumes
the entire compressed foam gap is rejected before geometry generation. When
ribs are disabled, rib-tip geometry is reported as not applicable rather than
as an implied hidden feature.

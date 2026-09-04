# Job schema (version 1)

The stable core accepts TOML (`.toml`) or JSON (`.json`). Paths are resolved
relative to the config file, so a job directory can be moved or archived.

The public `bin/lens-cap-3mf` release bridge has an additional handoff input:
an approved design-brief schema v2 `design-brief.json` (separate from the stable
job schema v1 described below). Create a release-bound job with paired
`lens-cap init --lens-identity ... --display-text FOCAL APERTURE ...` options,
then run `lens-cap handoff-init JOB.toml ... --provider PROVIDER` to create a
review-required scaffold. Verify anchor sources and evidence states, replace
its placeholders, complete provenance licences, review the full text set and
circular composition, and pass the candidate-bound `design_review` before
setting `generation.approved=true`.
`lens-cap handoff-check JOB.toml` and the bridge
validate the brief's source hash, focal-length/aperture order, source-backed
culture/rehousing anchor, anti-generic/completion review, and licence fields.
The review hash must equal the exact candidate and job source. Every anchor has
a unique stable `anchor_id`; the review's hero index/id must select the same
`motif_commitment=structural` anchor, and every one of its at least two
observable consequences must bind that same id while using distinct systems.
It also requires full-resolution text-off,
nearest-neighbour swap, composition-resolution, visual-grammar, finish-target,
and printable-reduction checks. `structural_thesis`, `finish_target_note`, and
`reviewer_note` are substantive evidence, not scaffold defaults. Any
An approved reference always uses a canonical `roles` array. A
`quality_reference` role requires a local hashed snapshot, at least two observable
transferable traits, inclusion in `generation.reference_hashes`, and exactly
one passing comparison record; singular or comma-packed `role` strings are
rejected. This semantic gate is separate
from the deterministic config schema; `process`/`model` remain available for
private experiments without a brief. The brief is an approval/provenance
snapshot for one raster. A `job.toml` is the authoritative mechanical record
for each size variant, so a brief's physical summary never overrides its
measured diameter, liner, rib, body geometry, or print values. The strict
`physical_fit` snapshot includes wall thickness, bottom thickness, side height,
bare clearance, liner/compression, every resolved rib dimension, adapter
decomposition, face/measurement values, and nozzle; every declared value must
match the active job. Only the documented size-dependent diameter fields may
be null when one approved artwork is intentionally shared across sizes.
The init metadata owns the complete ordered closed text set. `handoff-init`
copies every entry into both brief `display_text` and `allowed_text`, validates
the first two entries against its focal-length/aperture arguments, and the
strict validator rejects any later secondary-text drift among those three
lists. The handoff provider is mandatory and is recorded without invoking a
provider SDK. The free-text `metadata.lens_identity` is also structurally
bound: after punctuation, focal-unit, and F-number normalization it must contain
the brief brand (one slash-delimited alias), model, focal token(s), and maximum
aperture. Editing both `job_binding.metadata_lens_identity` and the TOML to the
same different lens therefore cannot detach the job from the structured brief.
`job_binding.circle` and `job_binding.palette` are exact normalized snapshots
of the approved job. They include circle center/radius/allow-outside and every
palette name, index, RGB value, role, height, required flag, and detector;
shared-artwork mode does not exempt them. Any change requires a new reviewed
snapshot before approval. `job_binding.artwork_process` similarly binds
`grid_size`, `safe_border_mm`, normalized prefilter/cleanup settings, and
`assembly_mode`. Anchor sources must be http(s) URLs or explicit
`archive:`/`urn:` identifiers, every claim/scope/evidence/visual-mapping field
must be substantive, and provenance must include substantive artwork licence,
brand-mark licence, film/history permissions, and notes. `evidence_state` must
begin with an explicit positive status such as `verified` or `sourced`; negated
phrases do not pass by substring. A provenance permission may be semantically
not applicable only when the entry explains why (for example, `not applicable
— no third-party mark rendered`), never as a bare `NONE`/`N/A` token.

Variable-aperture zooms use the same anchor/display split as focal ranges.
`lens_identity.maximum_aperture` keeps the first endpoint (for example
`F3.5`), while optional `maximum_aperture_display` keeps the normalized full
range (`F3.5-5.6`). `handoff-init` accepts either
`--maximum-aperture F3.5-5.6` (including en-dash input) or
`--maximum-aperture F3.5 --maximum-aperture-display F3.5-5.6`; in both cases
the complete range must remain the second item in job/brief `display_text`.
This schema currently supports F-numbers only. T-stop notation is intentionally
rejected rather than silently relabelled as an F-number.

## Top-level fields

| Key | Required | Meaning |
| --- | --- | --- |
| `schema_version` | no | currently `1` |
| `job_slug` | yes | filename-safe identifier |
| `source_art` | yes | immutable approved raster (PNG/PPM recommended for byte-level reproducibility; JPEG and other Pillow-readable formats are accepted) |
| `output_dir` | no | generated derivatives, default `build` |
| `face_diameter_mm` | process/model | finished circular face/relief diameter; derived from `measured_diameter_mm` when omitted |
| `measured_diameter_mm` | model/fitted cap | actual outside diameter of the surface the cap grips |
| `grid_size` | no | square output grid, 64–4096; a hand-authored omission loads as 1000, while `lens-cap init` writes two geometry samples per nozzle width, capped at 1600 (95/0.2 → 950); printability remains governed by `nozzle_mm`, not by treating one pixel as one extrusion line |
| `nozzle_mm` | no | minimum feature reference, default 0.2; record whether explicit |
| `safe_border_mm` | no | base-only outer border; a hand-authored omission loads as 0, while `lens-cap init` explicitly writes the safer 0.4 mm starter value |
| `source_sha256` | no | expected SHA-256 for the immutable source (the process writes its own lock) |
| `assembly_mode` | no | `auto`, `integrated_part`, `separate_parts`, or `inlay`; model-stage hint |
| `metadata` | no | provenance, permissions, and physical verification notes; retained with derived flags |

`art_master` and `source` are accepted as migration aliases for `source_art`;
`face_target_mm` (including the legacy `[face]` table spelling) is accepted as
an alias for `face_diameter_mm`. The canonical normalized config is written to
`config.normalized.json`.

For an adapter-derived envelope, `[metadata]` may carry this optional, checked
decomposition:

```toml
[metadata]
adapter_nominal_ring_mm = 80.0
adapter_radial_wall_mm = 2.5
adapter_derived_mating_diameter_mm = 85.0
```

The nominal and radial-wall fields must be declared together. The loader
requires
`adapter_nominal_ring_mm + 2 × adapter_radial_wall_mm = measured_diameter_mm`
within 0.05 mm; the derived value, when declared, must equal that formula.
`lens-cap init` writes all three when passed
`--adapter-nominal-ring 80 --adapter-radial-wall 2.5`. These are optional
provenance fields rather than another required mechanical input:
`measured_diameter_mm` remains the value that drives fit geometry.

In a `design-brief.json`, `lens_identity.focal_length_mm` remains the required
positive numeric machine anchor. The optional sibling
`lens_identity.focal_length_display` is the exact human-facing first text token;
use it for a zoom range such as `28–70mm` (or a unit-bearing prime such as
`50mm`). Its normalized form must be a number or number-range, and
`display_text[0]`/`allowed_text` must contain the same token. A range must be
positive, ascend from its first number, and start at `focal_length_mm` (the
machine anchor is the wide-end value). Omitting it keeps the prime-lens
behavior: the numeric focal length is rendered and checked.

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
  derivatives. This is a flat artwork/relief handoff only; it does not create
  a fitted cup or a printable 3MF.
* `lens-cap model`, `lens-cap export-openscad`, `lens-cap bambu-handoff`, and
  the modelling portion of `lens-cap build` require
  `measured_diameter_mm`. This is the actual cylindrical mating diameter, not
  a nominal filter-thread or a value copied from another job.

`lens-cap mesh` and `lens-cap handoff` remain aliases for the two explicit
adapter commands. `lens-cap validate` checks artifacts from a completed
process/model run and is not a config-only linter; use any stage command to
surface configuration parse errors before generating a release.

If the requested deliverable is an actual 3MF, no stage above is terminal.
Completion requires an existing `.3mf` emitted through the canonical bridge
and passing its package and projection checks. A generated SCAD/STL/handoff or
a passed deterministic preflight must not be reported as a successful 3MF.

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
friction_rib_profile_derived = true  # init-generated values may be recomputed
friction_rib_profile_reference_cavity_mm = 97.4

[print]
layer_height_mm = 0.10
printer = "Bambu Lab A1 mini"
filament_slots = ["black", "gray", "ivory", "red"]
```

The fitted-cap intake asks for the actual mating outside diameter, whether/how
thick the foam liner is, and whether to keep the inner-wall friction ribs. The
rib choice defaults to enabled and is recorded in `friction_ribs_explicit` when
the user answers it; `--no-friction-ribs` produces a smooth wall.
The optional adapter nominal/radial-wall pair may be recorded when the user
knows it, but it is not a fourth required question because the actual measured
diameter is sufficient and authoritative.
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

`lens-cap init` writes resolved profile numbers for readability and marks them
with `friction_rib_profile_derived=true` plus the reference cavity. If the
measured diameter or liner stack is later changed without editing those
numbers, the loader recomputes the profile. Editing any numeric rib field turns
it into an explicit override; the normalized report records that distinction.
For a bare wall, the default light profile may leave nominal clearance rather
than plastic interference. The geometry report then says
`friction_rib_retention_status=guide_only_clearance` and emits a warning; tune
the profile on a same-material coupon before relying on retention.

## Clean-room interaction scenario

`scripts/rehearse_user_agent.py` accepts a small JSON transcript in addition to
the TOML job. The transcript is an acceptance record for a fresh host, not a
prompt that the runner executes as code. Its stable fields are:

```json
{
  "schema_version": 1,
  "workspace": {"conversation_history": "none", "filesystem": "isolated_temp"},
  "conversation": {"turns": [{"speaker": "user", "text": "..."}]},
  "route": {
    "skills": ["lens-cap-imagegen", "lens-cap-production"],
    "sequence": "sequential", "generic_parallel_skills": []
  },
  "intake": {
    "asked_once": true, "persisted_in_job_toml": true,
    "questions": [
      "mating_outside_diameter_mm",
      "foam_liner_plan_and_uncompressed_thickness_mm",
      "friction_rib_preference"
    ],
    "answer": {
      "mating_outside_diameter_mm": 77,
      "adapter_nominal_ring_mm": 72,
      "adapter_radial_wall_mm": 2.5,
      "foam_liner_status": "none", "liner_thickness_mm": null,
      "friction_ribs_enabled": true, "friction_ribs_explicit": false,
      "friction_rib_profile": "light_tapered"
    },
    "answer_turn_index": 2
  },
  "fixture": {
    "path": "examples/fixtures/...", "primary_job": "jobs/77mm/job.toml",
    "identity_terms": ["optional translated brand/model alias"]
  },
  "production": {"endpoint": "./bin/lens-cap-3mf"}
}
```

The validator requires the selected job's measured diameter, foam status,
rib enabled/explicit flags, and rib profile to equal the answer; it also
checks that the answer text agrees with the foam/rib polarity. When the
optional adapter-envelope pair is present it checks
`nominal ring + 2 × radial wall = mating diameter` and
matches both values against the job metadata. This catches a dropped handoff
even when the conversation text sounds correct. A scenario may live outside the checkout
when passed by absolute path. For such a scenario, a relative `fixture.path`
is resolved beside the scenario first; the bundled examples continue to use
checkout-relative paths. The fixture itself may be external, but
`fixture.primary_job` is always constrained to that fixture after resolution.

The rehearsal replays the recorded conversation boundary and approved fixture;
it does not invoke ImageGen or reproduce provider pixels. It records
`provider_generation_status=recorded_fixture_not_replayed`. On a host without
OpenSCAD, routing/intake and deterministic preflight can still be `passed`, but
the top-level status is `unverifiable`, the canonical bridge is
`unverifiable`, and no actual 3MF completion may be claimed. Use
`--require-external` when a missing native 3MF must fail the run.

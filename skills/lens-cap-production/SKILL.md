---
name: lens-cap-production
description: Turn an approved lens-cap artwork and current fit measurements into a reproducible mask, relief, OpenSCAD, and printer-handoff package. Use for modelling or printable production; use an image-design skill for concept art only.
metadata:
  short-description: Reproducible lens-cap production
---

# Lens-cap production

This skill is the decision layer for the open-source lens-cap-pipeline
repository. Keep research, approved artwork, deterministic processing,
mechanical fit, and slicer review as separate, auditable stages.

For the Chinese translation, see [SKILL.zh-CN.md](SKILL.zh-CN.md); both files
describe the same gates and CLI, and the localized wording must not weaken the
production checks.

## Intake contract

For concept art only, do not invent mechanical dimensions. For a fitted cap,
ask one compact grouped question before generating geometry:

1. What is the actual outside diameter of the cylindrical surface the cap
   grips (前口径, in millimetres)?
2. Will the inner wall receive foam? If yes, ask for the uncompressed thickness
   including adhesive.

Use the confirmed mating diameter as the face/relief diameter by default; do
not ask for a second relief-diameter value. Do not ask the user to choose a
structure: set assembly_mode to auto. If compression is not supplied, use the
documented provisional 20% assumption and require a fit coupon. A nominal
filter thread is not a mating measurement. A standalone relief also needs an
explicit face diameter and nozzle/minimum-feature limit.

## Per-job identity and provenance

Keep a small manifest beside the TOML with `lens_identity`, `allowed_text`,
`allowed_marks`, `forbidden_legacy_tokens`, approved source/reference hashes,
and brand/film provenance plus licence notes. These fields are the semantic
fidelity lock: if they are absent, mark artwork identity `UNVERIFIABLE` rather
than importing wording, motifs, or dimensions from another lens job. The
pipeline may process pixels without that manifest, but it must not claim that
brand text or historical references were independently verified.
Every concept job should choose at least one design anchor tied to the lens
maker or lens culture (for example a documented technical history, craft
tradition, or cinematography connection) to give the piece a deliberate tone.
Label verified fact, sourced industry folklore, and visual inspiration
separately. Folklore may inform the design, but without a source it must remain
`UNVERIFIABLE` and must not be phrased as confirmed lens usage.

## Immutable artwork and provenance

Only process an approved source image named by the current job. Preserve its
hash, exact text, positions, circle, and orientation. The production stage may
make named alpha, palette, mask, and scale derivatives, but never redraw,
retype, recenter, mirror, content-crop, or regenerate the interior design.
For an opaque master, declare both `[circle].center_px` and `[circle].radius_px`;
only a reviewed binary-alpha boundary may use deterministic circle inference.
Keep brand/history/film claims in a provenance brief with their evidence and
licence; a cultural motif is not proof that this exact lens was used.

## Canonical runner

From a clean clone:

    python scripts/bootstrap.py --dev
    . .venv/bin/activate                 # Windows: .venv\Scripts\Activate.ps1
    lenscap init jobs/name/job.toml --source art/master.png \
      --measured-diameter 95 --foam-thickness 1.5
    # edit circle and palette, then:
    lenscap build jobs/name/job.toml --force --export-openscad --bambu-handoff
    lenscap validate jobs/name/job.toml

For release comparisons, use `python scripts/bootstrap.py --dev --locked`
when `uv` is installed; the unlocked helper is fine for ordinary development
but does not pin dependency versions byte-for-byte.

Use lens-cap as an equivalent command name. The stable process stage emits a
binary-alpha process master, role masks, one same-canvas mask and SVG per
palette colour, source/config locks, and JSON reports. The bundled model stage
imports those SVGs into one parameterised integrated cap; it never types the
artwork again. OpenSCAD export and Bambu handoff are optional adapters:
missing desktop tools are reported as UNVERIFIABLE, never as success. The
Bambu handoff is a version-neutral manifest, not a claim that a 3MF was sliced.
The model stage re-hashes the current source and every relief SVG before
writing SCAD, so a substituted source or hand-edited mask cannot pass merely
because an old report says `passed`. If a forced model rebuild leaves
same-named STL files behind, Bambu handoff accepts them only when the current
OpenSCAD report's model and per-part hashes match; manual STLs without that
report remain explicitly unverified.

Run stages independently when debugging:

    lenscap process jobs/name/job.toml --force
    lenscap model jobs/name/job.toml
    lenscap export-openscad jobs/name/job.toml --force
    lenscap bambu-handoff jobs/name/job.toml

Read docs/schema.md for fields, docs/model-adapter.md for adapter contracts,
and docs/release-checklist.md before publishing. Do not use old job-local
scripts or hand-edit a generated SCAD/3MF without recording a new adapter
version and hashes.

## Non-negotiable checks

- RGB distance arithmetic uses int32 differences and int64 accumulation;
  polarity and overflow checks must pass.
- Keep the process report's Python/NumPy/Pillow runtime block with the source
  lock; use the committed dependency lock for byte-level release comparisons.
- Outside, base, and positive-relief roles partition the face; per-colour
  masks do not overlap; the safe border is base-only.
- Every derivative links to the current source/config hash. Existing output is
  reused only when its hashes match; use --force to intentionally rebuild.
- The model stage must match the current source lock and per-colour SVG hashes;
  a same-named stale STL is never silently treated as current when an export
  report exists.
- The face diameter and foam/cavity calculation are recorded separately from
  optical identity. Fit is UNVERIFIABLE until a printed coupon is measured.
- Report artwork, geometry, external-tool, slicer, and physical-fit status
  independently. Never turn an unavailable preview into a PASS.

## Status vocabulary

The human-facing gate names below are uppercase. JSON reports use the stable
lowercase spellings `passed`, `failed`, and `unverifiable`; treat them as the
same three states when scripting, and do not silently coerce one into another.
Auxiliary values such as `not_requested`, `not_required`, and `available` are
informational metadata only; they never satisfy a required `PASS` gate.

- `PASS`: every declared deterministic check for that stage completed and its
  hashes/semantics match the current job.
- `FAIL`: a required check, input, hash, or semantic invariant failed; stop and
  inspect the report before continuing.
- `UNVERIFIABLE`: an external tool, slicer preview, or physical coupon is not
  available. It is an honest pending state, never a synonym for `PASS`.

## Delivery

Return the job directory or its reproducible archive, the source lock,
normalized config, process/model reports, and any external-tool manifests.
State assumptions (especially foam compression), licence/provenance boundaries,
and which checks remain UNVERIFIABLE. Private artwork, credentials,
STL/3MF/G-code, and third-party brand/film assets do not inherit the
Apache-2.0 code licence.

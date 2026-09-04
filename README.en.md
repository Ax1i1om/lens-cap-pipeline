# lens-cap-pipeline

An auditable, reproducible pipeline from approved lens-cap artwork to relief
masks/SVGs, followed by an explicit modelling and printer handoff.

> **ALPHA · v0.1.0-alpha.2**: This is the first public preview's routing
> revision. The config
> schema, modelling adapters, and CLI may still change incompatibly; file
> checks do not prove physical fit or a Bambu 3MF slice.

The central rule is **approved artwork is read-only; modelling never redraws
it**. “High quality” at the image stage means equivalence of the declared lens
specification, text hierarchy, and visual style—not pixel-identical generation;
the approved raster and its hash are the exact deterministic boundary. The
stable core emits a same-canvas process master, one mask and SVG per material,
role masks, and JSON safety reports. OpenSCAD/3MF are explicit downstream
adapters rather than hidden image-processing steps.

## Quick start

Python 3.11 or newer is required:

```sh
cd lens-cap-pipeline
./scripts/bootstrap.py --dev
# macOS/Linux alternative: python3 scripts/bootstrap.py --dev
# Windows alternative: py -3 scripts/bootstrap.py --dev
# macOS/Linux:
. .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
```

`bootstrap.py` writes only the project-local `.venv` and never installs into
the system interpreter. If you prefer a manual setup on macOS/Linux, run
`python3 -m venv .venv`, activate it, then install with `python3 -m pip install
-e '.[test]'`; on Windows use `py -3 -m venv .venv`, activate it, and then use
`python -m pip ...` so packages stay inside the project environment.
On macOS/Linux, `make bootstrap` is an equivalent convenience entry point;
on Windows without GNU Make, run the Python script directly.

For release-grade byte-for-byte reproduction, use the committed lockfile (after
installing [uv](https://docs.astral.sh/uv/)):

```sh
./scripts/bootstrap.py --dev --locked
# equivalent to: uv sync --locked --extra dev
# Windows alternative: py -3 scripts/bootstrap.py --dev --locked
```

Without `--locked`, the helper is a compatibility-first convenience and uses
the supported dependency ranges from `pyproject.toml`; pin the lockfile when
comparing artifacts across machines and retain the recorded tool versions.

The companion Skills are source-controlled separately from the installed
wheel. Check their manifest and hashes with
`./scripts/install_skills.py check --json`. `sync` is a dry-run by default;
pass an explicit `--dest` and then `--apply` to install into a project or
container. The command records a `.lens-cap-skills.json` receipt with the
project version, manifest hash, and per-file SHA-256 values, so repeated runs
are idempotent and version/content drift is visible. User edits are protected
unless `--force` is supplied; `--force --prune` is additionally required to
remove stale files. Use `--environment codex|claude` for host defaults, with
`--allow-global` required before writing an inferred global directory.
`bin/lens-cap-skills` is a repository-local alias.

For a host that only scans static metadata (or for a quick preflight of a
natural-language request), run the dependency-free manifest resolver:

```sh
python3 scripts/resolve_skill_route.py \
  "Design a circular lens-cap front for Sigma 28-70mm F2.8 and export a 3MF"
# Only an established lens-cap task may omit the cap/front surface:
python3 scripts/resolve_skill_route.py "test Sigma 28-70" \
  --lens-cap-context
```

It checks the exclusive `lens-cap-imagegen -> lens-cap-production` order but
does not generate artwork or CAD. Use `--named-lens` when the lens identity is
provided by an attachment/catalog rather than text. It is a host shim, not a
replacement for loading the Skills. A clean task rejects terse “test this
lens” wording; pass `--lens-cap-context` only when the caller knows the current
task has already established lens-cap intent.

> **Alpha distribution boundary:** installing only the CLI wheel does not
> include the two companion Skills, `bin/lens-cap-3mf`, or
> `tools/3mf_adapter`. Use a Git checkout, or unpack the source distribution
> and run the repository Skill sync and bridge from the unpacked tree for the
> complete “approved artwork → 3MF” route; `pip install` alone does not install
> those companion files. The wheel covers the core Python processing API only.

Codex commonly caches its Skill catalog when a task starts. After applying a
sync, start a new task or reload the host before testing automatic routing. If
the catalog is still stale, invoke `$lens-cap-imagegen` and then
`$lens-cap-production` explicitly; the clean-room runner validates the file
route, not every host's catalog or image-provider save API.
The checked-in frontmatter follows the current Codex validator and keeps
trigger vocabulary under `metadata.triggers`; a legacy host that scans only a
top-level `triggers` key needs an explicit Skill invocation or a host adapter.

On Windows, use `py -3 scripts/install_skills.py ...` or the companion
`bin/lens-cap-skills.cmd` launcher.

```sh
lens-cap init jobs/my-lens/job.toml \
  --source jobs/my-lens/art/master.png \
  --measured-diameter 85 \
  --adapter-nominal-ring 80 \
  --adapter-radial-wall 2.5 \
  --foam-thickness 1.5 \
  --job-slug mamiya-sekor-c-80-f1-9-cap \
  --lens-identity "Mamiya-Sekor C 80mm F1.9" \
  --display-text 80 F1.9 "MAMIYA-SEKOR C" 645 "REHOUSED MEDIUM FORMAT"
# Edit the circle and palette, then process:
lens-cap build jobs/my-lens/job.toml --force
```

`--adapter-nominal-ring` and `--adapter-radial-wall` are an optional provenance
decomposition and must be supplied together. The CLI checks
`80 + 2 × 2.5 = 85 mm` within 0.05 mm and records the values under `[metadata]`.
The measured mating diameter remains the only required mechanical dimension;
when it was measured directly, the adapter decomposition need not be supplied.

The output directory contains `process-master.png`, same-canvas
`masks/*.png` and `vector/*.svg`, role masks/overlay, `process-report.json`
(including the pixel-processing runtime), `config.normalized.json`, and
`source-lock.json`. After `model`/`build`, it
also contains `model/<job-slug>.scad` and `model/geometry-report.json`; STL
and Bambu handoff reports appear only when those stages are explicitly asked
for.

`build` is the one-command path: it reuses or creates a passed process,
generates parameterised SCAD, can export STL/write a Bambu handoff, and writes
a validation report. Run stages independently when debugging:

```sh
lens-cap process jobs/my-lens/job.toml --force
lens-cap model jobs/my-lens/job.toml
lens-cap export-openscad jobs/my-lens/job.toml --force
lens-cap bambu-handoff jobs/my-lens/job.toml
lens-cap validate jobs/my-lens/job.toml
./bin/lens-cap process jobs/my-lens/job.toml --force
```

`mesh` and `handoff` remain compatibility aliases; new scripts should use the
explicit command names above.

To export meshes and write the handoff in one invocation:

```sh
lens-cap build jobs/my-lens/job.toml --force --export-openscad --bambu-handoff
```

The CLI never uploads files, publishes a cloud project, or overwrites the
approved `source_art`; explicit `--force` is required when replacing process,
model, or mesh derivatives. `bambu-handoff` deterministically rewrites its
JSON manifest but does not launch the slicer. OpenSCAD and Bambu Studio are
optional. Missing tools are reported as `unverifiable`, never as a false
success. OpenSCAD export uses the `Manifold` backend so an integrated STL
retains imported relief solids; an older OpenSCAD without that backend should
use the separate component STL set instead of treating a CGAL assembly as a
complete one-piece relief. Inspect the final 3MF in Bambu Studio and record
its version, profile, and preview separately.

In Bambu Studio, choose one cap print set from the handoff: use
`integrated_monochrome` for a single-material one-piece STL, or
`multicolor_components` to assign filaments to the shared-canvas base/relief
STLs. Print `fit_coupon` separately before claiming fit. Do not import both
cap sets together; the legacy flat `stl_inputs` list intentionally contains
all generated files for compatibility and is not an instruction to print all
of them.

`model`, `export-openscad`, `bambu-handoff`, and the modelling part of `build`
require `measured_diameter_mm` (the actual mating outside diameter). A
relief-only process can stop after `process` with `face_diameter_mm` alone.

`lens-cap doctor --json` reports optional-tool availability without creating
job artifacts. `validate` is a full artifact audit and therefore requires a
successful process run; it is not a config-only lint command.

When the requested deliverable is an actual 3MF, use the repository's
one-command bridge instead of stopping at the SCAD/handoff stage. It reruns the
public build path, audits the same-canvas relief, exports an integrated native
3MF, and verifies the package with the dependency-free adapter. When ribs are
enabled, it checks every expected angular position at the rib start, interior
cross-sections, and end, plus connected full-height axial contact columns and
full-width tip faces directly in the final 3MF mesh. It also requires non-empty triangle assignments
for every required palette colour and rejects used colours outside the active
job. OpenSCAD is the only external requirement for the native one-piece output.
An actual 3MF
request is complete only when the target `.3mf` exists and passes this
verification; a PNG, SCAD, STL, handoff JSON, command recipe, or passed
preflight is not a successful 3MF delivery:

```sh
./bin/lens-cap-3mf jobs/my-lens/job.toml --force --json
# Optional printer-project output (requires local Bambu profiles):
./bin/lens-cap-3mf jobs/my-lens/job.toml --force --bambu slice \
  --machine-profile /path/to/machine.json \
  --process-profile /path/to/process.json \
  --filament-profile /path/to/filament.json --json
```

The `lens-cap-3mf` bridge is a release endpoint and validates the current
schema-v2 `design-brief.json` before creating any derivative. The brief must
set `generation.approved=true`, bind the current `source_art` SHA-256,
preserve focal-length/aperture order, and include source-backed anchors,
passed `design_review` anti-generic/completion checks, and licence records.
The review must bind the same candidate hash, select a structural hero anchor
by matching index and stable id, bind at least two observable consequences in
distinct systems back to that same anchor id, and
compare every local hashed quality reference individually. Discovery checks
`[metadata].design_brief`, then the nearest
ancestor `design-brief.json`; pass `--brief PATH` when the brief is elsewhere.
Missing, unapproved, or current-job-mismatched briefs return `FAILED` with a non-zero exit code,
so a bare TOML/PNG cannot be mistaken for an approved 3MF. Private
`process`/`model` experiments may still use the core CLI directly.

To bootstrap the handoff after saving a provider result, create a review-only
scaffold (no image provider is called and approval is never implied):

```sh
lens-cap handoff-init jobs/my-lens/job.toml \
  --brand Mamiya --model "Mamiya-Sekor C 80mm F1.9" \
  --focal-length 80 --maximum-aperture F1.9 \
  --provider "OpenAI built-in image_gen" \
  --anchor-source https://www.suaudeau.eu/memo/Manuels/Mamiya_M645_Service_Manual.pdf
# After reviewing the source, start anchor.evidence_state with a positive
# sourced/verified status. Complete every REPLACE, all three provenance licence
# fields, and notes; when one is inapplicable, give a reasoned sentence such as
# "not applicable — no third-party mark rendered". Then review the complete
# closed text set and circular composition. At full resolution, fill the exact
# reviewed_candidate_sha256, a structural hero_anchor_index plus matching
# hero_anchor_id, and at least two anchor_system_consequences that carry that
# same anchor_id while using distinct systems. Set
# full_resolution_reviewed only after passing text_off_anchor_recognizable,
# identity_swap_requires_redesign, anchor_drives_primary_composition,
# composition_resolved, visual_grammar_consistent, finish_target_met, and
# production_reduction_preserves_authorship with a substantive structural_thesis,
# finish_target_note, and reviewer_note.
# Pass every quality-reference comparison before setting generation.approved=true.
lens-cap handoff-check jobs/my-lens/job.toml --json
./bin/lens-cap-3mf jobs/my-lens/job.toml --force --json
```

The scaffold records candidate hashes, mechanical values, and an alpha-circle
suggestion. Opaque artwork still needs a human-reviewed `[circle]` center and
radius in the TOML. Pass `init --lens-identity` and `--display-text` together.
Because handoff-init is the printable-artwork bridge, it marks every valid
face job as `printable_front`; a face-diameter-only relief may pass artwork
handoff while a fitted cap still remains blocked until `measured_diameter_mm`
is supplied.
The first two text entries must agree with the handoff focal length/aperture,
and every secondary model/system line is copied verbatim into `display_text`
and `allowed_text`. The strict gate requires that complete ordered set to equal
the job metadata. It also requires the job's normalized identity text to
contain the brief brand, model, focal length, and F-number.
For a variable-aperture zoom, pass either `--maximum-aperture F3.5-5.6` or a
first-end anchor plus `--maximum-aperture-display F3.5-5.6`; en-dash input is
accepted and normalized, while the complete range remains `display_text[1]`.
This brief schema intentionally rejects T-stop notation for now.
`handoff-init --provider` is required, and its `next` output lists the remaining
evidence-state, licence, text, circle, design-review, and approval work. The
scaffold deliberately leaves the candidate-bound review hash, hero index/id,
structural thesis, finish target, reviewer note, and all review booleans
unapproved; placeholder or generic notes do not satisfy the release gate.
The scaffold also freezes the current `[circle]`, complete palette (roles, RGB,
relief heights, and related fields), grid, safe border, prefilter, cleanup, and
assembly mode under `job_binding`; any post-approval job drift fails. Review an
opaque source's TOML circle before scaffolding. If one of those values changes
afterward, rerun the same `handoff-init --force` command to refresh the snapshot
before filling the human-review fields. Every
anchor needs a unique lowercase `anchor_id`, a reviewable http(s) source or
explicit `archive:` identifier plus
substantive summary/context/motif/recognition fields; a bare `x` cannot pass.
Any image used as a quality or finish floor must be recorded with the
`quality_reference` value in its canonical `roles` array, a local snapshot
inside the brief directory, a
matching SHA-256 included in `generation.reference_hashes`, and at least two
transferable traits. `design_review.quality_reference_checks` must cover every
such reference exactly once with `met=true` and a substantive comparison note.
Use a `roles` array even for one role; packed comma-separated role strings are
invalid and cannot bypass the quality gate.
`handoff-init` refuses to overwrite an existing brief
unless `--force` is explicit; `handoff-check` and the 3MF bridge share the
same strict validator. The brief is a semantic, provenance, and human-approval
snapshot for one artwork. Each `job.toml` remains the mechanical authority for
its size variant—diameter, liner, ribs, and print settings in a brief summary
must not override the job.

On Windows, use `py -3 scripts/build_3mf.py jobs/my-lens/job.toml --force --json`
or `bin/lens-cap-3mf.cmd`; the remaining options are identical.

Without OpenSCAD, the bridge returns `unverifiable` and exits non-zero; it
never presents a SCAD or handoff JSON as a 3MF. If an external CAD tool has
already produced an assembly STL, the standard-library route remains available:
`python3 tools/3mf_adapter/three_mf_adapter.py standard INPUT.stl OUTPUT.3mf`.

The bridge gives `--openscad` / `--bambu-path` precedence. If omitted, it
reads `openscad_executable` / `bambu_executable` from the job's `[print]`
table (path-like values are resolved beside `job.toml`; bare command names use
the host PATH), then checks the default PATH and macOS app bundles. Tool
placement can therefore vary between Codex hosts without changing artwork or
model semantics.

To rehearse the complete route as a new user with no conversation context, run
the clean-room fixture runner. The default fixture is the fresh, actually
ImageGen-produced and human-approved Helios-44-2 REHOUSE v3 artwork. It has one
95 mm job and sends that job through the canonical bridge. The v2 fixture is
retained as the legacy 77/82/95 mm multi-diameter matrix; the independent
Mamiya-Sekor C 80mm F1.9 fixture covers cross-brand, adapter-wall, and foam
combinations. The runner invokes the public CLI and audits same-canvas relief
projections, but emits and verifies native 3MF packages only when a compatible
OpenSCAD is installed:

```sh
python3 scripts/smoke_rehouse.py \
  --fixture examples/fixtures/helios-44-2-rehouse-imagegen-v3 \
  --bridge-job jobs/95mm/job.toml --bambu never --json
# legacy 77/82/95 mm multi-diameter matrix
python3 scripts/smoke_rehouse.py \
  --fixture examples/fixtures/helios-44-2-rehouse-imagegen-v2 \
  --bambu never --json
# cross-brand adapter-wall and foam matrix
python3 scripts/smoke_rehouse.py \
  --fixture examples/fixtures/mamiya-sekor-c-80-f1-9-rehouse \
  --bambu never --json
# host-level verification (strictly requires OpenSCAD, Bambu Studio, and all profiles)
python3 scripts/smoke_rehouse.py \
  --fixture examples/fixtures/helios-44-2-rehouse-imagegen-v3 \
  --bridge-job jobs/95mm/job.toml --bambu auto \
  --require-external --keep-workdir \
  --machine-profile /path/to/machine.json \
  --process-profile /path/to/process.json \
  --filament-profile /path/to/filament.json --json
```

On a portable host without OpenSCAD, the smoke runner may still exit zero so CI
can retain diagnostics, but its top-level `status` is `unverifiable`.
`deterministic_preflight_status=passed` means only that the approved packet,
deterministic image stages, and projection preflight passed;
`native_3mf_complete=false` means no actual 3MF delivery was completed. Add
`--require-external` to turn that boundary into a hard failure.

To audit the conversation boundary as well as the files, run the structured
new-user rehearsal. It checks the exclusive Skill sequence, the one grouped
diameter/foam/rib intake, and persistence into the job TOML before invoking
the same production runner in an isolated temporary fixture:

```sh
python3 scripts/rehearse_user_agent.py \
  examples/rehearsals/helios-44-2-imagegen-v3-95mm-clean-room.json \
  --bambu never --json
# legacy Helios multi-diameter interaction transcript
python3 scripts/rehearse_user_agent.py \
  examples/rehearsals/helios-44-2-rehouse-clean-room.json \
  --bambu never --json
# independent Mamiya-Sekor C + foam/adapter-wall interaction sample
python3 scripts/rehearse_user_agent.py \
  examples/rehearsals/mamiya-sekor-c-80-f1-9-rehouse-clean-room.json \
  --bambu never --json
```

For one portable gate covering route resolution, all three fixtures, and all
three interaction transcripts, run `make smoke-all` (or `make acceptance`) from
the repository root. It may report external stages as `unverifiable`; a 3MF
completion claim still requires the documented `--require-external` run plus an
existing, verified artifact.

Copy the JSON scenario for another lens or adapter envelope; its
`fixture.primary_job` must agree with the answered diameter, liner thickness,
and rib profile. Add `--artifact-dir PATH --force-artifacts` when a rehearsal's
3MF snapshots should be retained. The transcript runner does not invoke
ImageGen again: it treats the candidate, prompt record, and human approval in
the fixture as the provider boundary and rehearses only the deterministic
route after that point. It therefore does not claim pixel-identical provider
output, turn an interaction `passed` into a 3MF success, or prove physical fit.

The smoke gate also rejects an unapproved artwork handoff, a missing
source-backed culture/rehousing anchor, or a prompt/artwork path that escapes
the fixture. This keeps the approved-artwork → 3MF boundary machine-checkable
without relying on conversation history.

For a zoom lens, keep the positive numeric
`lens_identity.focal_length_mm` machine anchor and optionally add the sibling
`focal_length_display` (for example `28–70mm`). Use that exact string as the
first `display_text`/`allowed_text` item; a range must start at the numeric
anchor and increase. Prime briefs may omit it and retain the existing behavior.

Use `--artifact-dir PATH` to explicitly copy generated 3MF files and sidecars,
or `--keep-workdir` to inspect the isolated run. The runner never reuses old
`out/`/3MF files or writes back into the approved fixture. Its `fit_status`
remains `UNVERIFIABLE` until a same-material coupon is printed and measured.

The dependency-free adapter can verify a Core package or require embedded
slicer G-code:

```sh
python3 tools/3mf_adapter/three_mf_adapter.py verify path/to/model.3mf
python3 tools/3mf_adapter/three_mf_adapter.py verify path/to/sliced.3mf --require-slice
```
This validates package/XML/mesh structure and, for slices, unique ordered
G-code blocks, config/slice metadata, monotonic Z layers, per-layer extrusion,
and non-degenerate XY motion whose range and diversity are plausible for the
model envelope. A ZIP containing a few forged G-code lines is rejected. It is
not a substitute for a slicer preview or a physical fit coupon.

After exporting meshes, use the public projection audit to compare each relief
STL with its same-canvas mask and catch translation, mirroring, white borders,
or a color-mesh swap:

~~~sh
./bin/audit-stl-projection \
  --mesh ivory=build/model/mesh/my-lens-cap-ivory_relief.stl \
  --expected-mask ivory=build/masks/ivory.png \
  --canvas-size-mm 95 --tolerance-pixels 1 \
  --output-report build/model/projection-report.json \
  --output-dir build/model/projection-diff
~~~

This is a file/footprint audit only; it does not prove physical fit or a
successful slicer preview.

## Fitted-cap intake

Ask for the actual outside diameter of the cylindrical surface being gripped,
the liner plan (foam or no foam, plus uncompressed thickness when foam is
used), and whether to retain the inner-wall friction ribs. Ribs are enabled by
default; record an explicit smooth-wall choice with `--no-friction-ribs` or
`fit.friction_ribs_enabled = false` (use `--friction-ribs` to make the enabled
choice explicit). A fitted job derives the face/relief
diameter from `measured_diameter_mm` unless an explicit `face_diameter_mm` override is
documented. Two neutral rib profiles are available. `light_tapered` (the
default) keeps 12 narrow, shallow tapered ribs with 0.10 mm radial intrusion
for compatibility and light extra grip. `wide_tapered` uses six broad wedge
ribs with an 8° base angle, a 4.4° tip angle, and near-full side-wall span when
a reference-like chunky profile is desired. Select it with
`--friction-rib-profile wide_tapered` or `[fit].friction_rib_profile`; explicit
count, intrusion, width, and height values override profile defaults. Profiles
are neutral mechanical geometry, not brand artwork. With foam, either profile
may locally compress the liner, so mechanical fit remains unverified until a
same-material coupon is printed and measured. For a 95 mm mating diameter,
1.5 mm foam, and provisional 20% compression, the reference test explicitly
overrides `wide_tapered` to about 0.55 mm intrusion, 6.8 mm width, and 12.5 mm
height (estimated local compression 56.7%); the preset's own default intrusion
is 0.30 mm, and neither value is a universal fit prescription.
Pre-rib configurations inherit the new enabled default when rebuilt; to
reproduce a legacy smooth wall, set `friction_ribs_enabled = false`, rerun the
process/model stages, and do not reuse an old model as if it matched the new
configuration. User-supplied SCAD/3MF archives and MakerWorld pages are
reference observations only: record their provenance and licence, but regenerate
current geometry from measured inputs instead of copying meshes or assets.

If the user also knows the adapter's nominal diameter and one-side radial wall,
record the optional `--adapter-nominal-ring` and `--adapter-radial-wall` pair.
Both must be present and satisfy
`nominal diameter + 2 × radial wall = measured_diameter_mm` within 0.05 mm.
These values audit where the envelope came from; they are not a fourth required
intake answer, and the measured mating diameter remains the sole required
mechanical value.

## Fidelity and safety gates

Colour-distance arithmetic is fixed at `int32` deltas and `int64` accumulation;
`int16` squaring is forbidden. The report must show `overflow_guard=true` and a
raw-source polarity check. Only nearest-neighbour label scaling, explicit
circle/alpha exclusion, a declared base-only border, and declared isolated
sub-nozzle cleanup are allowed. Text, motifs, positions, and orientation are
never silently retyped, recentered, mirrored, or cropped. Masks share one
`viewBox`, do not overlap, and must partition the process master’s alpha area.

Missing external tools produce `unverifiable`, not a false pass. Physical fit
is always reported separately from file and mesh validation.

For archive portability, `config.normalized.json` reduces absolute OpenSCAD
and Bambu executable settings to their basenames so the core config digest is
not tied to one machine. The original TOML remains the runtime source, while
the adapter report records the resolved tool basename, version, and output
hashes.

Native 3MF publication also removes OpenSCAD's volatile creation timestamp,
replaces generated UUID attributes with content-derived UUIDv5 values, and
rewrites ZIP entries in a fixed order with fixed metadata. Repeated output can
therefore be compared byte-for-byte under the same OpenSCAD/Python/zlib
toolchain. Across versions, compare source/config hashes, mesh semantics,
bounds, projection, materials, and rib gates; different OpenSCAD tessellation
or compression-library bytes are not promised to match.

For byte-level release comparisons, prefer a lossless PNG/PPM master and use
`bootstrap.py --locked` to pin dependencies. JPEG decoding can vary with the
Pillow/libjpeg versions on a host; the source lock detects substitution but
cannot make different decoders yield identical pixels.

The repository examples use a tiny redistributable geometric PPM fixture;
replace `source_art` with artwork you are authorised to use and re-measure its
circle. See [`docs/workflow.md`](docs/workflow.md), [`docs/schema.md`](docs/schema.md),
[`docs/concept-art.md`](docs/concept-art.md), [`docs/model-adapter.md`](docs/model-adapter.md),
and [`docs/release-checklist.md`](docs/release-checklist.md) for the full contract.

Copy [`examples/job-manifest.template.json`](examples/job-manifest.template.json)
beside a job and fill in the allowed text/marks, legacy-token deny-list,
artwork hash, brand/film provenance, licences, and fit evidence. The brief or
manifest is a semantic/provenance snapshot for the approved artwork; each job
TOML is the authority for pixel processing and that size variant's mechanical
geometry. One brief may serve multiple sizes, so its mechanical summary must
not override a job's measured diameter, liner, or rib settings. The
current `validate` command primarily audits deterministic files: a private
experiment may run without a completed manifest, but a public release must
mark missing identity/licence evidence `UNVERIFIABLE` rather than treating a
core `PASS` as verified brand or film history.

If `examples/build/` exists in a checkout, treat it as local derived output:
old reports may contain machine-specific paths and STL files. Do not publish
it as a fixture; rebuild from a clean checkout and retain only audited,
reproducible, authorised assets.

## Licensing

Pipeline code and first-party templates are [Apache-2.0](LICENSE). Brand marks, film
references, approved artwork, and downloaded cap sources keep their own
copyright/trademark/licence terms; record provenance in each job manifest.
The repository ignores private artwork and large printer outputs by default.
Local `jobs/` workspaces are ignored as well because they commonly contain
artwork or meshes that are not cleared for redistribution. Publish a specific
job only after a provenance/licence review, using `git add -f` deliberately.
See [`CONTRIBUTING.md`](CONTRIBUTING.md), [`SECURITY.md`](SECURITY.md), and
[`THIRD_PARTY.md`](THIRD_PARTY.md) for contribution and release boundaries.

This repository does not log into MakerWorld, call ChromaCanvas, or upload a
cloud project. When the user explicitly installs OpenSCAD (and optionally
provides Bambu profiles), `scripts/build_3mf.py` / `bin/lens-cap-3mf` generates
and verifies the corresponding 3MF locally. Missing desktop tools are reported
honestly rather than guessed; slicer preview and physical fit still require
the user's review and records.

## Skill integration

When a user asks to design or generate a lens-cap, the repository routing
contract makes `lens-cap-imagegen` the only creative design Skill for that turn.
Requests involving printable geometry, relief, fit, SCAD, STL, or 3MF route to
`lens-cap-production`. Do not invoke generic graphic-design, logo, poster,
product-visual, UI, CAD, or 3D-design Skills in parallel. See
[`AGENTS.md`](AGENTS.md) and [`skills/RESOLVER.md`](skills/RESOLVER.md) for the
portable resolver contract.

For concept research and artwork, load
[`skills/lens-cap-imagegen/SKILL.md`](skills/lens-cap-imagegen/SKILL.md)
(`SKILL.zh-CN.md` is the Chinese translation). For deterministic processing
and modelling, load
[`skills/lens-cap-production/SKILL.md`](skills/lens-cap-production/SKILL.md).
These companion Skills only route decisions and gates; the repository's
`lens-cap` CLI remains the implementation and artifact writer. The Skills,
docs, and examples are distributed from the source repository/source
distribution; the CLI wheel intentionally does not bundle a proprietary image
SDK or require the Skill files at runtime.

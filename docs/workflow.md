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

When the deliverable is an actual 3MF, the public one-command bridge is:

```sh
./bin/lens-cap-3mf jobs/name/job.toml --force --json
```

It requires a local OpenSCAD with the Manifold backend for the integrated
one-piece package. Add `--bambu slice` and explicit machine/process/filament
profiles only when a printer-specific 3MF with embedded G-code is wanted.
The bridge prefers explicit --openscad / --bambu-path, then the job's
[print] executable fields (path-like relative values are relative to the job
file; bare command names use PATH), then host discovery. Explicit values are
authoritative: a missing configured path does not silently select another app.
It runs the same-canvas relief projection audit before writing the native 3MF.
When the user requested an actual 3MF, the route is complete only after that
`.3mf` exists and the package/projection gates pass. A process report, SCAD,
STL, handoff JSON, or passed preflight is useful evidence but is not a 3MF
delivery.

### Keep companion Skills in sync

The checked-in `skills/manifest.json` is the source of truth for the two
first-party routing Skills. Check the source tree from any host without
writing a global directory:

```sh
./scripts/install_skills.py check --json
```

To inspect a Codex/Claude-compatible destination, pass it explicitly. A
`sync` without `--apply` is also a read-only plan:

```sh
./scripts/install_skills.py check --dest .agents/skills --json
./scripts/install_skills.py sync --dest .agents/skills
```

Apply only after reviewing the plan. The synchronizer writes a
`.lens-cap-skills.json` receipt containing the project version, manifest hash,
and per-file SHA-256 values; rerunning an unchanged sync is a no-op. It never
overwrites a locally edited Skill unless `--force` is explicit. Removing
source files additionally requires `--force --prune`:

```sh
./scripts/install_skills.py sync --dest .agents/skills --apply
./scripts/install_skills.py sync --environment codex --apply --allow-global
```

Use `--environment claude` for `~/.claude/skills`, or `--dest` for a
project/container-specific path. Inferred global destinations are always
dry-run unless `--allow-global` is supplied. For `check`, exit status `1`
means a destination is missing or drifted; status `2` means the manifest or
requested target is invalid. A `sync` plan exits successfully while showing
the pending actions. `bin/lens-cap-skills` and `scripts/sync_skills.py` are
portable aliases for the same entry point.

Skill discovery is host state, not a production-file gate: Codex commonly
loads the catalog at task start. After an applied sync, start a new task or
reload the host; if automatic matching still does not show the two Skills,
invoke `$lens-cap-imagegen` or `$lens-cap-production` explicitly. The
clean-room rehearsal below validates the repository route, not every host's
catalog cache.

## 1. Declare one job

Run `lens-cap init jobs/name/job.toml --source ... --lens-identity ...
--display-text FOCAL APERTURE ...` and edit the generated file. The two
identity options form the release binding: supply them together, keep focal
length and aperture first, and include every secondary model/system line that
may appear in the artwork. For a standalone relief, add `--face-diameter <mm>`;
for a fitted cap,
`--measured-diameter <mm>` is required. Add `--foam-thickness <mm>` only when
an uncompressed foam liner (including adhesive) is actually planned; for bare
plastic, declare `foam_liner_status = "none"` (and the bare clearance) in the
`[fit]` table. Add `--no-friction-ribs` only when a smooth wall is explicitly
chosen; otherwise ribs remain enabled by the documented default (use
`--friction-ribs` to record an explicit enabled choice). The measured
mating diameter automatically becomes the face diameter.

If the adapter construction is known, `init` also accepts the optional pair
`--adapter-nominal-ring <mm> --adapter-radial-wall <mm>`. They must be supplied
together and satisfy
`nominal ring + 2 × one-side radial wall = measured diameter` within 0.05 mm;
the values are persisted under `[metadata]`. They explain the measured
envelope but are not another mandatory intake question. The actual measured
mating diameter remains the sole required mechanical dimension.

When the starter artwork does not exist yet, a relative `--source` is kept
relative to the new job directory and its source-parent placeholder (normally
`art/`) is created there.
This makes `init` safe from an empty checkout: it does not serialize a path
that accidentally points at the caller's current directory. Restore or copy
the approved artwork before running `process`.
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

After the approved raster is copied into the job, first review the job's circle,
complete palette, grid, safe border, prefilter, cleanup, and assembly mode. The
provider-neutral handoff scaffold then freezes those values and fills the
mechanical and hash boilerplate:

```sh
lens-cap init jobs/name/job.toml \
  --source jobs/name/art/master.png \
  --measured-diameter 85 \
  --lens-identity "Mamiya-Sekor C 80mm F1.9" \
  --display-text 80 F1.9 "MAMIYA-SEKOR C" 645 "REHOUSED MEDIUM FORMAT"
lens-cap handoff-init jobs/name/job.toml \
  --brand Mamiya --model "Mamiya-Sekor C 80mm F1.9" \
  --focal-length 80 --maximum-aperture F1.9 \
  --provider "OpenAI built-in image_gen" \
  --anchor-source https://www.suaudeau.eu/memo/Manuels/Mamiya_M645_Service_Manual.pdf
```

Review the cited source, change each anchor's `evidence_state` from `to_verify`
to a truthful value beginning with a positive `sourced`, `verified`,
`documented`, `attested`, or `archived` status, replace every `REPLACE`, and
complete all provenance licence fields and notes. `not verified` and `not
sourced` are failures, not positive substring matches. When a provenance field
does not apply, use a reasoned sentence such as `not applicable — no
third-party mark rendered`; bare `NONE`/`N/A` remains insufficient. Confirm that `display_text` and
`allowed_text` exactly preserve the complete job metadata text list, review
the circular composition, then set `generation.approved=true` only after a
human review and run `lens-cap handoff-check jobs/name/job.toml --json`.
The scaffold never invokes an image provider and never assumes approval. For
opaque artwork, set an explicitly reviewed `[circle]` rather than letting a
downstream stage recenter the image. If any bound artwork-process setting
changes after the scaffold was written, rerun the same `handoff-init --force` command before
filling its review fields so `job_binding` captures the final values.

Every anchor source must be an http(s) URL or an explicit `archive:`/`urn:`
identifier. Claim/scope/evidence/source-role/summary/render-role/context/motif/
recognition fields must all be substantive and non-placeholder; a qualifier may
be null only when none is needed. The strict gate also requires artwork,
brand-mark, film/history, and notes provenance entries.

The design brief is the semantic/provenance/approval snapshot for an artwork.
The job TOML is the mechanical authority for each size variant, including the
measured diameter, liner, ribs, and print settings. A shared brief's mechanical
summary is informative and must never overwrite a variant's job values.

### Clean-room rehearsal

The repository includes a deterministic-input integration rehearsal that does
not depend on the current conversation or any old generated directory. Its
default is the fresh, actually ImageGen-produced and human-approved
Helios-44-2 REHOUSE v3 fixture: one 95 mm job is sent through the canonical
bridge.

```sh
python3 scripts/smoke_rehouse.py \
  --fixture examples/fixtures/helios-44-2-rehouse-imagegen-v3 \
  --bridge-job jobs/95mm/job.toml --bambu never --json
```

The v2 Helios fixture remains the legacy 77/82/95 mm multi-diameter matrix. The
Mamiya-Sekor C 80mm F1.9 fixture is the cross-brand matrix for adapter-wall and
foam cases; both can be selected explicitly with `--fixture`. On a host with
OpenSCAD, add `--require-external` (and optionally `--bambu auto` plus local
profiles) to require actual native 3MF export. `--artifact-dir` is an explicit
opt-in for retaining those outputs.

```sh
python3 scripts/smoke_rehouse.py \
  --fixture examples/fixtures/helios-44-2-rehouse-imagegen-v2 \
  --bambu never --json
python3 scripts/smoke_rehouse.py \
  --fixture examples/fixtures/mamiya-sekor-c-80-f1-9-rehouse \
  --bambu never --json
```

Without OpenSCAD, the portable runner may exit zero to preserve its diagnostic
report, but the top-level `status` is `unverifiable` and
`native_3mf_complete=false`; only `deterministic_preflight_status` may be
`passed`. Likewise, the conversation rehearsal may report
`interaction_status=passed` while its top-level production status remains
`unverifiable`. It never calls ImageGen again: the fixture's candidate, prompt
record, and human approval are the provider boundary. Physical fit remains
pending until a same-material coupon is measured.

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
`handoff` are compatibility aliases.) The `bin/lens-cap-3mf` bridge first
requires a passing `design-brief.json` (approved flag, current raster hash,
identity/text hierarchy, sourced anchor, and licence fields), then chains
these gates, exports the integrated native 3MF, and verifies its Core package;
missing, unapproved, or mismatched briefs return `FAILED` rather than creating a
publishable release. Never redraw or retype artwork in a CAD script. Keep the
mechanical body parameterized by the current measured diameter and liner plan.
Generate and measure a short fit ring before a full multicolour print. The
bundled `wide_tapered` profile is mechanical geometry only and must not alter
the approved focal-length/aperture artwork or introduce brand marks.

The OpenSCAD adapter uses the `Manifold` backend for exports so an integrated
assembly retains the imported relief solids as well as the cap body. A machine
with an older OpenSCAD that lacks this backend should use the separate
component STL set rather than treating a `CGAL` assembly export as a complete
one-piece relief.

For binary STL exports, the OpenSCAD adapter sorts complete triangle records
into a deterministic order after the renderer finishes. Geometry, normals and
attribute bytes are preserved, but renderer-native raw byte order is not; the
report's `format_check.canonicalization` and the resulting file hash document
this normalization. A reproducibility check must compare the canonicalized
file, not an unprocessed OpenSCAD byte stream.

Native OpenSCAD 3MF output is normalized separately: volatile creation times
are removed, generated UUIDs become content-derived UUIDv5 values, and ZIP
entry order/metadata are fixed. Same-toolchain reruns are byte-stable; across
OpenSCAD/Python/zlib versions, the release contract compares semantic geometry,
bounds, source/config hashes, projection, material assignments, and rib gates
rather than promising identical tessellation bytes.

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

A release claiming an actual 3MF must additionally point to the existing
verified `.3mf` and its reports. Do not promote an interaction/preflight pass,
SCAD, STL, or handoff-only result to a successful 3MF release.

Publish a job only with its normalized config, process report, source lock,
provenance/licence notes, generated CAD source, slicer preview and fit result.
Keep private or unlicensed artwork out of the repository; use release assets
or a reproducible download record for large STL/3MF/G-code files.

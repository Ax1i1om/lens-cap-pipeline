# lens-cap-pipeline

An auditable, reproducible pipeline from approved lens-cap artwork to relief
masks/SVGs, followed by an explicit modelling and printer handoff.

The central rule is **approved artwork is read-only; modelling never redraws
it**. The stable core emits a same-canvas process master, one mask and SVG per
material, role masks, and JSON safety reports. OpenSCAD/3MF are explicit
downstream adapters rather than hidden image-processing steps.

## Quick start

Python 3.11 or newer is required:

```sh
cd lens-cap-pipeline
python scripts/bootstrap.py --dev
# macOS/Linux:
. .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
```

`bootstrap.py` writes only the project-local `.venv` and never installs into
the system interpreter. If you prefer a manual setup, run `python -m venv
.venv`, activate it, then install with `python -m pip install -e '.[test]'`.
On macOS/Linux, `make bootstrap` is an equivalent convenience entry point;
on Windows without GNU Make, run the Python script directly.

For release-grade byte-for-byte reproduction, use the committed lockfile (after
installing [uv](https://docs.astral.sh/uv/)):

```sh
python scripts/bootstrap.py --dev --locked
# equivalent to: uv sync --locked --extra dev
```

Without `--locked`, the helper is a compatibility-first convenience and uses
the supported dependency ranges from `pyproject.toml`; pin the lockfile when
comparing artifacts across machines and retain the recorded tool versions.

```sh
lens-cap init jobs/my-lens/job.toml \
  --source jobs/my-lens/art/master.png \
  --measured-diameter 95 \
  --foam-thickness 1.5 \
  --job-slug my-lens-cap
# Edit the circle and palette, then process:
lens-cap build jobs/my-lens/job.toml --force
```

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
success. Inspect the final 3MF in Bambu Studio and record its version, profile,
and preview separately.

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

## Fitted-cap intake

Ask only for the actual outside diameter of the cylindrical surface being
gripped and the liner plan (foam or no foam, plus uncompressed thickness when
foam is used). A fitted job derives the face/relief diameter from
`measured_diameter_mm` unless an explicit `face_diameter_mm` override is
documented. Mechanical fit remains unverified until a coupon is printed and
measured.

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

For byte-level release comparisons, prefer a lossless PNG/PPM master and use
`bootstrap.py --locked` to pin dependencies. JPEG decoding can vary with the
Pillow/libjpeg versions on a host; the source lock detects substitution but
cannot make different decoders yield identical pixels.

The repository examples use a tiny redistributable geometric PPM fixture;
replace `source_art` with artwork you are authorised to use and re-measure its
circle. See [`docs/workflow.md`](docs/workflow.md), [`docs/schema.md`](docs/schema.md),
[`docs/model-adapter.md`](docs/model-adapter.md), and
[`docs/release-checklist.md`](docs/release-checklist.md) for the full contract.

Copy [`examples/job-manifest.template.json`](examples/job-manifest.template.json)
beside a job and fill in the allowed text/marks, legacy-token deny-list,
artwork hash, brand/film provenance, licences, and fit evidence. The manifest
locks semantic identity; the TOML locks pixel processing and geometry.

If `examples/build/` exists in a checkout, treat it as local derived output:
old reports may contain machine-specific paths and STL files. Do not publish
it as a fixture; rebuild from a clean checkout and retain only audited,
reproducible, authorised assets.

## Licensing

Pipeline code and first-party templates are [Apache-2.0](LICENSE). Brand marks, film
references, approved artwork, and downloaded cap sources keep their own
copyright/trademark/licence terms; record provenance in each job manifest.
The repository ignores private artwork and large printer outputs by default.
See [`CONTRIBUTING.md`](CONTRIBUTING.md), [`SECURITY.md`](SECURITY.md), and
[`THIRD_PARTY.md`](THIRD_PARTY.md) for contribution and release boundaries.

## Skill integration

For Codex-assisted production, load
[`skills/lens-cap-production/SKILL.md`](skills/lens-cap-production/SKILL.md)
(`SKILL.zh-CN.md` is the Chinese translation). The Skill only routes decisions
and gates; the repository's `lens-cap` CLI remains the implementation and
artifact writer.

# Open-source release checklist

Use this checklist for each tagged release and for each published lens job.

## Repository

- [ ] `LICENSE`, `NOTICE`, `THIRD_PARTY.md`, `CONTRIBUTING.md` and
      `SECURITY.md` are present.
- [ ] `pyproject.toml` metadata and license classifier match Apache-2.0.
- [ ] From a clean checkout, build the intended sdist/wheel with an isolated
      build environment (install the `build` frontend in that environment when
      needed; for example `python -m build --sdist --wheel`), then
      inspect both archives: no private/generated job output is included,
      `skills/`, `docs/`, and `uv.lock` are present in the source archive, and the wheel's
      import/entry-point metadata is intact. Rebuild (do not hand-edit) any
      `*.egg-info` metadata before tagging.
- [ ] For byte-level artifact comparisons, build and test with
      `./scripts/bootstrap.py --dev --locked`; record the Python,
      NumPy/Pillow, OpenSCAD and slicer versions in the release note.
- [ ] `python -m pytest`, `python -m compileall -q lens_cap_pipeline` and CI
      pass on all supported Python versions.
- [ ] For a fitted/model release (with `measured_diameter_mm`),
      `lens-cap build <job.toml> --force` completes; if OpenSCAD is available,
      `--export-openscad` and `--external` are recorded.
- [ ] No private source art, credentials, printer identifiers, or unlicensed
      film/logo material is committed.
- [ ] A changelog/release note names breaking config or output changes.

## Open-source sanitization gate

- [ ] Review the staged file list (including ignored/generated files that may
      be accidentally force-added); no `.env`, token, private key, cloud URL,
      local username/path, or printer credential is present.
- [ ] Run a secret/PII scanner when available (for example `gitleaks` or
      `trufflehog`) and manually inspect any finding. A scanner warning is not
      proof that artwork or a model is redistributable.
- [ ] Build from a clean checkout and confirm reports do not leak a developer's
      absolute path; if an absolute path is required locally, keep that report
      out of the release or regenerate a portable fixture.
- [ ] For every logo, film reference, historical image, and downloaded cap
      body, record URL/author/licence/changes in the job manifest and retain
      required attribution. The Apache code licence does not grant those
      rights.

## Job provenance

- [ ] The approved source image has a SHA-256 lock and explicit permission to
      redistribute (or is kept outside the repository).
- [ ] Brand/model text and historical references are listed as allowed marks;
      no stale tokens from another lens leaked into the job.
- [ ] The source/model URL, author, licence and modifications are recorded for
      every downloaded cap body or third-party asset.

## Deterministic processing

- [ ] `process-report.json` shows `overflow_guard=true`, `int32` arithmetic,
      `int64` distance accumulation, and a passed source-polarity check.
- [ ] Role masks and per-colour masks are disjoint and cover exactly the
      approved face area; the safe border contains no positive relief.
- [ ] A second run from the same config produces identical hashes.
- [ ] The original artwork file is unchanged and its hash matches the manifest.

## Geometry and printing

- [ ] Face diameter equals the current measured gripping diameter unless an
      explicit override is documented.
- [ ] Foam thickness/compression or bare-plastic retention is documented;
      compression assumptions are labelled provisional.
- [ ] A fit ring/coupon was printed and physically measured before claiming fit.
- [ ] `lens-cap model` emits the SCAD and geometry report;
      `lens-cap export-openscad` and `lens-cap bambu-handoff` outputs are
      present when those stages are claimed (`mesh`/`handoff` aliases are
      allowed for legacy scripts).
- [ ] CAD/3MF import uses one shared canvas and preserves text/motif positions.
- [ ] Slicer name/version, nozzle, layer height, material slots, orientation,
      support, purge tower and preview are recorded.
- [ ] STL projection/geometry audit passes, or the job is clearly marked
      `UNVERIFIABLE` with the reason.

The public same-canvas projection audit (or an equivalent adapter) must be
run for every relief STL, with its diff/report archived beside the job. A
model/validation PASS without this footprint check is not a claim that T* or
any other text stayed in place.

## Publication

- [ ] Generated binaries are either reproducible from the manifest or attached
      as release assets with checksums.
- [ ] The job README states what is `PASS`, `FAIL`, and `UNVERIFIABLE`.
- [ ] Trademark and copyright notices are retained; Apache-2.0 is not claimed
      for third-party artwork or downloaded models.

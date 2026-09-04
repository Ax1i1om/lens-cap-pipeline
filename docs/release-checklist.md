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
- [ ] Publish the distribution boundary clearly: a wheel-only install does not
      provide the companion Skills or `bin/lens-cap-3mf`; a complete route uses
      the source checkout/sdist. Confirm the target Codex host has loaded the
      synced Skill catalog (or record an explicit `$lens-cap-*` fallback).
- [ ] For byte-level artifact comparisons, build and test with
      `./scripts/bootstrap.py --dev --locked`; record the Python,
      NumPy/Pillow, OpenSCAD and slicer versions in the release note.
- [ ] `python -m pytest`, `python -m compileall -q lens_cap_pipeline` and CI
      pass on all supported Python versions.
- [ ] For any release that claims an actual 3MF,
      `./bin/lens-cap-3mf <job.toml> --force --json` returns `passed`, the
      reported `.3mf` exists, and the adjacent release report records
      `scripts/build_3mf.py` as its runner. A successful `lens-cap build`, SCAD,
      STL, or handoff JSON alone is not the release endpoint.
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
- [ ] The design brief is schema v2; `design_review` binds the exact candidate
      hash, selects one structural anchor with matching stable index/id, binds
      two distinct-system consequences back to that same id, and passes full-resolution text-off, nearest-neighbour
      swap, composition, visual-grammar, finish-target, and printable-reduction
      checks with substantive thesis/finish/reviewer evidence.
- [ ] Every user-supplied quality benchmark has a local hashed snapshot,
      observable transferable traits, and exactly one passing comparison; no
      quality reference was silently demoted to a generic style reference, and
      every reference uses a canonical `roles` array rather than packed text.
- [ ] Every rendered brand, coating, series, and mount mark is an exact entry
      in the current manifest's `allowed_text`/`allowed_marks`; no generic
      Skill, template, test, or model code supplies a maker-specific default.
- [ ] The source/model URL, author, licence and modifications are recorded for
      every downloaded cap body or third-party asset.

## Deterministic processing

- [ ] `process-report.json` shows `overflow_guard=true`, `int32` arithmetic,
      `int64` distance accumulation, and a passed source-polarity check.
- [ ] Role masks and per-colour masks are disjoint and cover exactly the
      approved face area; the safe border contains no positive relief.
- [ ] A second run from the same config produces identical hashes.
- [ ] The original artwork file is unchanged and its hash matches the manifest.
- [ ] No upstream nozzle-width/minimum-feature scan was run. Printer/profile
      values did not lower the grid, change filters/cleanup/palette thresholds,
      edit masks, redraw the candidate, or stale native geometry.

## Geometry and printing

- [ ] The user-facing file is the release report's `primary_3mf`. For a Bambu
      destination it is a verified `--bambu export`/`--bambu slice` project,
      not the internal `native_3mf` Core geometry master. The Bambu project
      contains both `Metadata/project_settings.config` and
      `Metadata/model_settings.config`; neither file was fabricated by hand.

- [ ] Face diameter equals the current measured gripping diameter unless an
      explicit override is documented.
- [ ] The measured diameter and foam decision came from the current job. A
      fixture's `95 mm` or no-foam value was not treated as a universal default.
- [ ] Foam thickness/compression or bare-plastic retention is documented;
      compression assumptions are labelled provisional.
- [ ] Inner-wall friction-rib choice is recorded. Ribs are enabled by default;
      if foam is used, treat them as light extra grip and verify the setting with
      the fit coupon (or document the explicit smooth-wall opt-out).
- [ ] When ribs are enabled, the manifest and geometry report retain count,
      radial protrusion, tangential width, and axial start/height. When foam and
      ribs are combined, check local rib interference on the coupon; nominal
      cavity diameter alone is not evidence of safe compression or retention.
- [ ] For a claimed native one-piece 3MF, the release report's
      `friction_rib_mesh_audit` passes and detects every expected angular rib
      position at five cross-sections (start, three interior slices, and end),
      plus 15 full-height axial contact columns per rib and connected,
      full-width tip faces. This proves structural presence in the final mesh
      only; it does not replace a physical fit coupon.
- [ ] The selected neutral `friction_rib_profile` is recorded. Use
      `light_tapered` for the compatibility default or `wide_tapered` for the
      broad, reference-like six-wedge profile; explicit numeric rib overrides
      are documented. For the 95 mm / 1.5 mm foam fixture, the 0.55 mm,
      6.8 mm-wide, 12.5 mm-high wide profile is an explicit override (the
      preset default is 0.30 mm) and only a coupon starting point (estimated
      local compression 56.7%).
- [ ] A fit ring/coupon was printed and physically measured before claiming fit.
- [ ] `lens-cap model` emits the SCAD and geometry report;
      `lens-cap export-openscad` and `lens-cap bambu-handoff` outputs are
      present when those stages are claimed (`mesh`/`handoff` aliases are
      allowed for legacy scripts).
- [ ] CAD/3MF import uses one shared canvas and preserves text/motif positions.
- [ ] The native 3MF `material_assignment_audit` has non-empty triangle
      assignments for every required current-job palette colour and no used
      colour outside that palette; a single object/mesh may still be
      multi-material through per-triangle assignments.
- [ ] Named material surfaces match their same-canvas masks, and visible top
      areas match the fresh hash-bound post-Boolean assembly. Independent SVG
      layer areas remain pre-Boolean diagnostics only.
- [ ] For a claimed Bambu project, record the exact machine/process/filament
      profile input and post-run SHA-256 values, every resolved inheritance
      file and hash, and a passed `effective_profile_audit` showing the project
      settings match the resolved profiles. Also record slicer name/version,
      nozzle, layer height, material slots, orientation, support, purge tower,
      and preview.
- [ ] For a claimed sliced 3MF, `verification.slice_audit` passes: the G-code
      MD5 and project/slice bindings agree, layer Z is monotonic and consistent
      with the model height/config, every declared layer has positive extrusion,
      and the extrusion path has non-degenerate XY range/diversity plausible for
      the model bounds. G-code presence or byte count alone is not release
      evidence.
- [ ] A `print-ready` claim includes a target-profile toolpath preview showing
      that critical focal/aperture text, fine lines, and material islands have
      actual extrusion paths. Upstream raster-width estimates are not evidence.
- [ ] STL projection/geometry audit passes, or the job is clearly marked
      `UNVERIFIABLE` with the reason.

The public same-canvas projection audit (or an equivalent adapter) must be
run for every relief STL, with its diff/report archived beside the job. A
model/validation PASS without this footprint check is not a claim that the
manifest-declared artwork text or marks stayed in place.
For the canonical anti-jag contour, confirm that the process report names the
directed pixel-union algorithm, preserves `evenodd` compound-path holes,
records vector footprint/source-area delta, and keeps maximum deviation within
the declared source-space budget. Projection tolerance must be derived from it,
not hand-raised after a failure.

## Publication

- [ ] Generated binaries are either reproducible from the manifest or attached
      as release assets with checksums.
- [ ] The job README states what is `PASS`, `FAIL`, and `UNVERIFIABLE`.
- [ ] Trademark and copyright notices are retained; Apache-2.0 is not claimed
      for third-party artwork or downloaded models.

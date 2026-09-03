# Changelog

## 0.1.0-alpha.2 — 2026-09-02

Routing and packaging revision for the public Alpha.

* Adds explicit lens-cap trigger phrases and a primary/exclusive routing rule
  so generic design Skills are not invoked in parallel for lens-cap requests.
* Adds Codex `agents/openai.yaml` metadata, a portable resolver contract, and a
  repository-level `AGENTS.md` for cloned-project discovery.
* Adds regression tests and source-distribution entries for the routing files.

## 0.1.0-alpha.1 — 2026-09-02

First public Alpha release of the reproducible lens-cap pipeline.

* Publishes the deterministic artwork → mask/SVG → parameterised OpenSCAD
  workflow, portable CLI, examples, Skills, tests, and CI configuration.
* Includes explicit fitted-cap intake for measured diameter, foam choice, and
  default inner-wall friction ribs, with coupon-gated physical-fit reporting.
* Uses the OpenSCAD Manifold backend for integrated relief exports and rejects
  an assembly mesh when the declared relief height is missing.
* Keeps private artwork, generated STL/3MF/G-code, and platform credentials out
  of the repository by default.
* Ignores per-lens `jobs/` workspaces by default; publish a job only after its
  artwork and model provenance/licence have been reviewed.

Known Alpha limitations: Bambu Studio/3MF slicing is an external manual step;
physical fit and material behaviour require a same-material coupon; brand marks,
film references, and generated artwork require separate provenance and rights.

## Unreleased

* Tightens cross-host lens-cap routing for named-lens circular/front/relief
  requests and natural variants such as circular image, lens cover, and
  printable model; adds optical-design/repair/product-photo negative cases,
  and documents Codex catalog-cache plus legacy top-level-trigger limitations.
  The grouped diameter/foam/rib intake is now explicitly one-per-job and is
  persisted for the production handoff instead of being repeated.
* Makes omitted compression semantic: foam jobs retain the provisional 20%
  assumption, while bare-wall jobs default to zero; companion Skill mirrors and
  receipts are refreshed together.
* Documents the provider-dependent ImageGen approval handoff as an explicit
  human gate, including the minimum raster/hash/brief/circle/fit packet needed
  before the deterministic 3MF bridge.
* Supports an optional `lens_identity.focal_length_display` for zoom ranges
  such as `28–70mm`; the numeric `focal_length_mm` anchor and existing prime
  briefs remain unchanged, while smoke/rehearsal gates match the full range.

* Adds `scripts/build_3mf.py` and `bin/lens-cap-3mf`, the canonical one-command
  endpoint from a job file to a verified integrated native 3MF, with an
  explicit optional Bambu slice path and machine-readable release report.
  Missing desktop tools remain `UNVERIFIABLE` instead of being mistaken for a
  completed 3MF.
* Adds a fresh ImageGen Helios-44-2 REHOUSE smoke fixture and a cross-brand
  Mamiya-Sekor C 80mm F1.9 fixture across multiple adapter envelopes, with
  retained native/sliced 3MF snapshots and portable sidecars.
* Adds a provider-neutral clean-room user/agent rehearsal runner and transcripts;
  it verifies exclusive Skill sequencing, one grouped diameter/foam/rib intake,
  persistence before the first geometry command, adapter-wall arithmetic, and
  the final 3MF bridge. Its brief gate also requires an approved raster,
  in-fixture hashes, provenance, and a sourced culture/rehouse anchor. The
  fixture matrix is CI-smoked without requiring a desktop slicer.
* Adds a dependency-free manifest-driven route shim
  (`scripts/resolve_skill_route.py`) and badge/front trigger vocabulary for
  legacy or host-specific Skill loaders; the clean-room rehearsal executes the
  shim against its first user turn.
  The shim now accepts terse named-lens noun phrases and compact catalog
  notation while rejecting unnamed generic-lens requests. Rehearsal intake
  checks also verify foam/rib polarity, the default-vs-explicit rib flag, and
  that no production command is claimed before the physical gate.
* Adds a read-only-by-default, idempotent companion-Skill synchronizer with
  manifest/version/file-hash drift reports for project, Codex, and Claude
  destinations. Explicit `--apply`/`--force` gates writes and preserves local
  edits.
* Fixes `init` so a missing relative starter artwork path remains relative to
  the task config directory instead of leaking the caller's working directory;
  adds cross-context routing/intake regression checks.

* Added generic, manifest-recorded inner-wall friction wedges to fitted caps.
  They are enabled by default; the `light_tapered` compatibility profile uses a
  conservative 0.10 mm radial intrusion,
  can be explicitly disabled for a smooth wall, and are reused in fit coupons.
  Geometry reports now record the rib parameters, signed bare-wall clearance /
  interference, and provisional local-foam compression estimate; physical fit
  still requires a coupon.
  Pre-existing configs without rib fields inherit the enabled default; pin
  `friction_ribs_enabled=false` and rebuild when reproducing a legacy smooth
  wall.
* Generalized brand/coating/series marks so no maker-specific glyph is a
  built-in default. The image-generation and
  production Skills now collect the rib preference in the same grouped intake.
* Added neutral `light_tapered` and `wide_tapered` rib profiles. The former is
  the compatibility default; the latter provides six broad, tapered wedges for
  reference-like inner-wall geometry while remaining brand-agnostic. Explicit
  rib dimensions override a profile's defaults. User-supplied SCAD/3MF archives
  are documented as reference observations only and are never copied into the
  project; the 95 mm / 1.5 mm foam wide-profile example remains coupon-gated.

* Initial public project skeleton with a deterministic artwork process stage,
  role/mask reports, portable CLI, examples, tests and CI.
* OpenSCAD/3MF and physical-fit adapters remain explicit downstream stages;
  they must record their own tool versions and provenance.
* Added locked-environment bootstrap support, runtime fingerprints, chunked
  palette classification, source/SVG hash gates, and stale-STL filtering for
  safer repeated builds.
* Added the provider-neutral concept-art Skill and design-brief template,
  plus a same-canvas STL projection audit that exposes translation, mirroring,
  white-border, and wrong-material regressions before slicing.

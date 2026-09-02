# Changelog

## Unreleased

* Added generic, manifest-recorded inner-wall friction wedges to fitted caps.
  They are enabled by default with a conservative 0.10 mm radial intrusion,
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

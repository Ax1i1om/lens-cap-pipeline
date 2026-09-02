# Changelog

## Unreleased

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

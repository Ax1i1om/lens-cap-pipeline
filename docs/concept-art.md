# Concept-art companion

The repository has two deliberately separate contracts:

1. The lens-cap-imagegen Skill researches a named lens and produces a candidate
   circular artwork plus a provenance brief.
2. The lens-cap-production Skill accepts the human-approved raster and
   deterministically produces masks, SVGs, SCAD and printer handoff files.

This boundary matters. “High quality” means equivalence of the declared lens
specification, text hierarchy, and visual style—not pixel-identical generation.
A generative image provider can change typography, texture, or composition
between runs; no prompt can make that output a reproducible CAD input. Once a
candidate is approved, copy it into the job, compute its SHA-256, and never let
a downstream tool redraw it.

## Design brief

Start with examples/design-brief.template.json. Fill in the canonical lens
identity and exact display text, then close the semantic sets:

- allowed_text and allowed_marks are the only words and marks permitted on
  the image;
- forbidden_legacy_tokens prevents a previous lens job leaking into a new
  one;
- anchors separates verified facts, sourced folklore, and visual inspiration;
- generation records the actual provider/mode, model/version, prompt,
  reference hashes, candidate hash, and human approval;
- provenance records copyright, trademark, and source-model terms.

At least one anchor must be a source-backed manufacturer-culture fact. A film,
mission, donor, or rehousing relationship is a separate claim with an explicit
scope: exact lens, family, brand, or visual metaphor. A community nickname can
be attested while its origin story remains contested; do not collapse those
two claims. Keep a qualifier next to any lore that appears in the artwork.

## Prompt and review

The default prompt contract is:

- complete circular medallion on a square canvas;
- focal length as the largest first read and maximum aperture as the second;
- restrained brand/model text, quoted exactly;
- black/charcoal/gray/ivory broad shapes and continuous engraved lines;
- no pointillism, dense halftone, gradients, glossy 3D, random numerals,
  pseudo-text, or accidental rectangular crop;
- no literal lens body, glass, iris, film still, actor, official logo, or
  spacecraft unless the user supplies an authorised asset and asks for it.

Translate a historical association into original geometry. For example, use
orbital arcs for aerospace, a single flame/exposure wedge for candlelight,
contour bands for desert imagery, or registration grids for rehoused
mechanical cinema glass. In balanced or mythic mode, a selected lore or cinema
association must have a visible symbolic/structural role, not only a tiny
caption. Hide the lore words once and check that the motif still reads without
becoming an official mark.

Review the candidate at full resolution. Confirm the circle, exact focal and
aperture strings, hierarchy, omissions, palette, and motif. If typography is
wrong, make a targeted iteration or deliver a clean artwork plus a separate
editable text layer; never silently substitute a near model name.

## Handoff to production

After approval:

1. copy the selected candidate to a descriptive path inside the job;
2. record its SHA-256 and the approval note in the design brief/manifest;
3. if the source is opaque, declare its circle center and radius in the TOML;
4. run the production CLI from the repository;
5. compare the process master and role masks before any external adapter.

For a standalone printable front, ask for the finished face diameter and
nozzle/minimum-feature limit. For a fitted cap, ask for the actual mating
outside diameter (前口径), the foam plan, and whether to retain the inner-wall
friction ribs. Ribs default to on; only an explicit smooth-wall request
disables them. The confirmed diameter also sets the face/relief diameter by
default. The production Skill asks this gate before modeling and does not ask
users to choose an assembly structure.
If a supplied reference shows chunky internal projections, record the neutral
`wide_tapered` profile request for production; keep the default
`light_tapered` profile for ordinary compatibility. This mechanical choice
must not alter the approved focal-length/aperture artwork, and the reference
archive remains provenance-only rather than a mesh to copy.

The design brief is a human/provenance contract. The CLI does not infer or
silently fill its semantic fields: `validate` proves deterministic file
integrity, while the release checklist must separately mark a missing identity
or licence brief as UNVERIFIABLE for public publication. This keeps creative
evidence honest without blocking a private, relief-only experiment.

The concept Skill and first-party templates are distributed with the source
repository/sdist. The installed CLI wheel is intentionally provider-neutral;
it does not bundle a proprietary image SDK or claim access to a particular
image-generation service.

# Design depth and anti-generic review

Read this reference before writing or revising a generation prompt and again
before approving a candidate. The goal is lens-specific meaning, not visual
busyness.

## Keep concept exploration separate from production reduction

- `concept_art` explores a distinctive story and large-scale composition. It
  may use research-derived accent colours, layered print textures, asymmetric
  massing, and richer symbolic geometry. The complete circle and exact text
  hierarchy still apply.
- `printable_front` preserves an approved concept's hero structure while
  reducing colours, line density, and minimum features for manufacture.
- When the user asks for both, approve the concept direction before reducing
  it. Do not let nozzle, palette, or relief constraints choose the concept.
- If the user directly requests a printable front and no concept is approved
  yet, still complete authorship preflight before applying manufacturing
  reduction. Nozzle, palette, and feature limits may delete or consolidate
  fragile detail, but may not choose a sparse concept on the user's behalf.

## Build a research-to-design map

For every proposed hero anchor, record:

1. the verified claim and its evidence scope;
2. the original visual metaphor derived from that claim;
3. at least two observable, functionally distinct structural consequences
   across different systems (for example numeral counterform plus field
   division, or dominant path plus perimeter/container rhythm). Repeating or
   scaling one symbol does not count twice; one continuous gesture may count
   when it governs two different functions;
4. what changes when the identity is replaced by its closest same-maker
   sibling; only use a competing maker when no sensible sibling exists. A
   maker-culture layer may establish tone, but a second structural layer must
   respond to verified lens-specific evidence such as focal/aperture
   proportions, zoom range, optical formula, format, or a documented technical
   feature. Never invent exclusive lore merely to pass this test; and
5. any protected-material boundary or qualifier.

At least one sourced anchor system must control a large-scale structural
decision and propagate beyond one isolated symbol.
For example, a documented aspherical-manufacturing story might become an
interferometric contour whose deviation shapes the whole field; merely adding
a tiny lens icon beside a generic ring does not count.

## Resolve without busyness

Completion is not a detail quota. A sparse or minimalist direction may pass,
but it must resolve all of these relationships:

- **integration:** the anchor changes more than one major system. For example,
  the primary type may be embedded in, cut by, continued by, aligned with,
  contained by, tensioned through scale/spacing, or act as a counterform to the
  system rather than floating above it. Stock letterforms may remain intact,
  but their placement, scale, and spacing must be governed by the system;
- **mid-scale rhythm:** between the whole-circle silhouette and tiny surface
  detail, spacing, repetition, overlaps, transitions, or counterforms create a
  deliberate visual cadence. Microtexture is optional;
- **whole-field responsibility:** every large region has a named job—anchor,
  hierarchy, balance, manufacture, or intentional quiet. Arbitrary filler
  panels and accidental dead zones fail;
- **visual grammar:** line weights, corner/radius families, alignments, gaps,
  junctions, and containers behave like one system rather than unrelated
  templates.

Minimal means fewer, stronger relationships. It does not mean a text stack
plus unused space. Dense work does not pass merely by containing more marks.
A single arrow, pictogram, or oversized gesture beside unchanged display type
is still one isolated relationship. Adding framed labels or calling the rest
of the circle "quiet" does not resolve it. Quiet space passes only when its
boundaries and proportions visibly make it an active counterform, separation,
balance, or manufacturing zone.

## Explore genuinely different directions

For every new, unapproved concept, form at least two structural directions
before showing the first candidate. Directions must differ in the main spatial
idea, not just palette, texture, or ornament. Useful contrasts include
asymmetry versus axial order, one continuous path versus divided fields, or
archival diagram versus mythic emblem. Render only the number of candidates
requested. A single final image may still come from two internally compared
directions.

Write each direction as a one-sentence thesis containing an active structural
verb. Reject a direction that can only be described with style adjectives or
a list of objects. Check prompt contradictions: a negative list may forbid
literal/protected depiction, but it must leave at least one original,
nonverbal structural cue for the selected anchor.

## Anti-generic approval gate

Review the candidate at full resolution and apply all tests:

1. **Text-off test:** hide every identity-bearing label and numeral, including
   dates, format labels, mount codes, and lore copy; retain only the focal
   length and aperture to test hierarchy. The selected cultural or technical
   anchor must remain perceptible through geometry.
2. **Nearest-neighbour swap test:** first replace the identity with the closest
   same-maker sibling; if no sensible sibling exists, use the closest plausible
   competitor from the same era/category. Ignore a passive change in text width,
   but count a deliberate structural response to verified focal/aperture
   proportions, zoom range, optical formula, format, or another documented
   technical feature. A maker-culture layer alone is insufficient unless the
   user explicitly requests a series system; it may pass when coordinated with
   that lens-specific structural layer. Never fabricate a unique anecdote to
   manufacture difference, and do not choose a deliberately distant lens.
3. **Structural-anchor test:** the sourced hero anchor must shape the primary
   composition and create coordinated consequences in multiple systems, not
   survive only as one oversized icon, badge, garnish, or caption.
4. **Composition-resolution test:** the focal/aperture hierarchy, anchor,
   negative space, and every field, container, or perimeter device actually
   present feel intentionally resolved within the circle. Open or asymmetric
   relationships may pass when they are deliberate and stable. Name the job of
   each large region; unexplained dead zones or filler panels fail.
5. **Visual-grammar test:** repeated weights, radii/corners, alignments, gaps,
   and junctions are visibly systematic at full resolution.
6. **Finish-target test:** compare against an observable declared finish target;
   bare adjectives such as minimal, clean, vintage, premium, or printable do
   not constitute evidence. When the
   user supplied quality references, compare only their transferable finish
   traits—integration, hierarchy, edge discipline, rhythm, and completion—not
   their literal motifs or density.
7. **Reduction-integrity test:** for `printable_front`, compare with the
   approved concept or structural thesis. The hero path/topology, primary
   counterforms, and type relationships must survive. If the story disappears,
   return to the nearest previously passing reduction or redesign the smallest
   printable structural relationship instead of restoring an unprintable
   detail or approving a generic reduction.

Concentric rings, aperture-like geometry, radial ticks, calibration grids,
serial-style numerals, and generic instrument-panel marks do not satisfy an
anchor by themselves. They may support a lens-specific structure, but cannot
be the structure. Passing means more specific, not more crowded.

Record the review in `design_review`:

- `reviewed_candidate_sha256` equals the exact raster reviewed;
- every anchor has a unique lowercase stable `anchor_id`; zero-based
  `hero_anchor_index` selects an anchor whose `motif_commitment` is
  `structural`, and `hero_anchor_id` must equal that selected anchor's id;
- `anchor_system_consequences` records at least two distinct system/effect
  pairs, each explicitly carrying the same `anchor_id` as `hero_anchor_id`.
  Use distinct `system` values from `typography_or_counterform`,
  `field_path_or_divide`, and `container_or_perimeter`; pure repetition or
  scaling is not a second effect and consequences borrowed from a different
  anchor fail;
- `full_resolution_reviewed=true`;
- `text_off_anchor_recognizable=true`;
- `identity_swap_requires_redesign=true`;
- `anchor_drives_primary_composition=true`; and
- `composition_resolved=true`;
- `visual_grammar_consistent=true`;
- `finish_target_met=true`;
- `production_reduction_preserves_authorship=true` for a printable front; and
- a substantive, verb-led `structural_thesis` naming the causal topology;
- a substantive `finish_target_note` naming the intended craft floor and any
  transferable quality-reference traits; and
- `quality_reference_checks` covers every local, hashed `quality_reference`
  exactly once with `met=true` and a substantive comparison note; and
- a substantive `reviewer_note` naming the visible anchor system, its
  coordinated consequences, the finish comparison, and why the nearest
  plausible identity swap would require a redesign.

Use design-brief `schema_version=2`. Never copy the scaffold's placeholder
sentences or set the booleans merely to unblock production: bind the review to
the exact candidate after inspecting it at full resolution. Do not mark the
candidate approved unless every applicable field passes. A failed identity,
hero anchor, or large-scale composition requires a new concept; do not freeze
it with a preserve-composition edit. Once those structural checks pass, a typo,
edge-craft, spacing, palette, grammar, or finish failure may receive a targeted
edit, after which all checks must run again.

For each user-declared quality benchmark, add an `approved_references` entry
with stable `id`, a `roles` array containing `"quality_reference"`, descriptive `path_or_url`, local
`snapshot_path`, matching `snapshot_sha256`, and at least two observable
`transferable_traits`. Include that digest in `generation.reference_hashes`.
Then add exactly one `design_review.quality_reference_checks` entry with the
same `reference_id`, `met=true`, and a concrete `comparison_note`. Other
research/style references do not create this comparison requirement. Always
use the array even for one role; comma-packed role strings are invalid.

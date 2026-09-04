# Design depth and anti-generic review

Read this reference before writing or revising a generation prompt and again
before approving a candidate. The goal is lens-specific meaning, not visual
busyness.

## Keep concept exploration separate from production reduction

- `concept_art` explores a distinctive story and large-scale composition. It
  may use research-derived accent colours, layered print textures, asymmetric
  massing, and richer symbolic geometry. The complete circle and exact text
  hierarchy still apply.
- `printable_front` preserves an approved concept's hero structure. Any later
  print-safe revision must be a separately approved candidate informed by an
  actual target-slicer toolpath review.
- When the user asks for both, approve the concept direction before reducing
  it. Do not let nozzle, palette, or relief constraints choose the concept.
- If the user directly requests a printable front and no concept is approved
  yet, still complete authorship preflight. Do not let assumed nozzle or
  feature limits delete detail before an actual slicer review.

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

## Keep evidence out of the picture unless it improves the picture

The research map is an internal decision aid and provenance record, not a
rendering checklist. A source establishes permission to use an idea and the
scope of the claim; it does not require an element, group count, qualifier,
timeline, connector, callout, label, or diagram to appear. Do not feed evidence
states, source language, `anchor_id`, hashes, schema fields, review booleans, or
production parameters into the image-provider prompt.

Use one visual proposition, not one subsystem per fact. A lens-specific fact
may change the width of an opening, the pressure between glyphs, the cadence of
a field, or the direction of a gesture without being literally decoded by a
viewer. Literal counted optical modules are optional; use them only when the
count improves the composition and can be judged reliably. Documentary rigor
belongs in the brief. Visual quality belongs in the picture.

## Resolve without busyness

Completion is not a detail quota. It is **relational density**: a few forms can
feel complete when they repeatedly affect one another, while many isolated
facts still feel like a draft. A sparse or dense direction may pass, but it
must resolve all of these relationships:

- **integration:** the anchor changes more than one major system. For example,
  the primary type may be embedded in, cut by, continued by, aligned with,
  contained by, tensioned through scale/spacing, or act as a counterform to the
  system rather than floating above it. Stock letterforms may remain intact,
  but their placement, scale, and spacing must be governed by the system;
- **mid-scale rhythm:** between the whole-circle silhouette and tiny surface
  detail, spacing, repetition, overlaps, transitions, or counterforms create a
  deliberate visual cadence. Microtexture is optional;
- **three-scale hierarchy:** the composition contains a macro focal/aperture
  lockup, at least one mid-scale signature gesture crossing roughly 25–60% of
  the diameter, and one or two restrained fine-rhythm clusters. Large type plus
  small captions alone fails; fine rhythm may be linework or print texture and
  must never become random filler;
- **whole-field balance:** the complete circle feels intentionally occupied.
  Evaluate large regions visually rather than turning each one into a labelled
  function. Quiet space passes when the hero/type visibly shapes its boundary;
  accidental dead zones and filler panels fail;
- **perimeter engagement:** when a rim is present, the signature gesture must
  interrupt, enter, transform, or echo it in at least two separated locations.
  A passive default single-line circle is not a resolved perimeter device;
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

Reject **connector grammar** unless the user explicitly asks for a schematic:
nodes or cards joined by orthogonal pipes, PCB traces, subway routes, arrows,
progress bars, dashboard panels, exploded-instruction layouts, or multiple
independent boxes linked merely to prove causality. Technical lines may be a
subordinate rhythm, but the main motion should be a diagonal, arc, eccentric
convergence, strong field split, or an axial tension with equivalent force.

At thumbnail size, a strong candidate should read in this order: focal length,
aperture, signature gesture, circular closure. If only the large number
survives—or if panels and mechanisms read before the aperture—the concept is
not finished. Then inspect at full resolution for edge discipline, spacing,
repeat cadence, and tactile print character.

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
   length and aperture to test hierarchy. The signature gesture and formal
   system must remain intentional and specific; the viewer does not need to
   guess the historical anecdote without the brief.
2. **Nearest-neighbour swap test:** first replace the identity with the closest
   same-maker sibling; if no sensible sibling exists, use the closest plausible
   competitor from the same era/category. Ignore a passive change in text width,
   but count a deliberate structural response to verified focal/aperture
   proportions, zoom range, optical formula, format, or another documented
   technical feature. At least one identity-bound relationship must materially
   change; do not require the whole series grammar or composition to be thrown
   away. A maker-culture layer alone is insufficient unless the user explicitly
   requests a series system. Never fabricate a unique anecdote or force a
   literal count merely to pass, and do not choose a deliberately distant lens.
3. **Structural-anchor test:** the sourced signature gesture must change at
   least two coordinated relationships in the composition, not survive only as
   one oversized icon, badge, garnish, or caption. In archival/balanced mode it
   supports the focal/aperture hero lockup; it need not become a competing hero.
4. **Composition-resolution test:** the focal/aperture hierarchy, anchor,
   negative space, and every field, container, or perimeter device actually
   present feel intentionally resolved within the circle. Open or asymmetric
   relationships may pass when they are deliberate and stable. Name the job of
   the major regions at a glance; unexplained dead zones or filler panels fail.
5. **Visual-grammar test:** repeated weights, radii/corners, alignments, gaps,
   and junctions are visibly systematic at full resolution.
6. **Finish-target test:** compare against an observable declared finish target;
   bare adjectives such as minimal, clean, vintage, premium, or printable do
   not constitute evidence. When the
   user supplied quality references, compare their transferable finish traits:
   integration, hierarchy, macro/mid/micro layering, density envelope, tonal
   occupancy, perimeter engagement, edge discipline, rhythm, tactile print
   finish, and completion. Do not copy their literal motifs or exact layouts.
   Research correctness cannot offset visibly weaker finish parity.
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

Also reject a concept when its main structure can be summarized as “rectangular
cards or modules joined by pipes.” Check that the focal glyph itself has at
least one authored relationship—counterform, silhouette, terminal,
continuation, or field cut—rather than stock type with a coloured overlay.
Check that a present rim participates in the composition, and that macro,
mid-scale, and fine rhythms coexist without turning into an interface. These
are finish gates, not invitations to add arbitrary detail.

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
When the provider accepts image references, designate one snapshot as the lead
finish reference and pass the actual local image input; use the others as
supporting references. A prose summary and hash alone do not condition visual
quality.
Then add exactly one `design_review.quality_reference_checks` entry with the
same `reference_id`, `met=true`, and a concrete `comparison_note`. Other
research/style references do not create this comparison requirement. Always
use the array even for one role; comma-packed role strings are invalid.

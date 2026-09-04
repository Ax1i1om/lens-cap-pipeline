# Concept-art companion

The repository has two deliberately separate contracts:

1. The lens-cap-imagegen Skill researches a named lens and produces a candidate
   circular artwork plus a provenance brief.
2. The lens-cap-production Skill accepts the human-approved raster and
   deterministically produces masks, SVGs, SCAD, and—when requested and the
   required local tool is available—a verified 3MF.

This boundary matters. “High quality” means semantic fidelity plus a resolved,
authored composition with coherent hierarchy, field responsibility, visual
grammar, and finish—not pixel-identical generation.
A generative image provider can change typography, texture, or composition
between runs; no prompt can make that output a reproducible CAD input. Once a
candidate is approved, copy it into the job, compute its SHA-256, and never let
a downstream tool redraw it.

The clean-room fixture records a real candidate, its prompt/provenance, and the
human approval boundary. Its rehearsal deliberately does not call ImageGen a
second time; it tests whether a fresh agent can consume that approved packet
and complete the deterministic production route. Provider output therefore
needs style/specification equivalence at review time, not pixel identity across
runs.

## Design brief

Start with examples/design-brief.template.json. Fill in the canonical lens
identity and exact display text, then close the semantic sets:

Keep `lens_identity.focal_length_mm` as the positive numeric machine anchor. If
the lens is a zoom, optionally add the sibling
`lens_identity.focal_length_display` (for example `28–70mm`) and use that exact
token as the first `display_text` and `allowed_text` entry. Prime lenses may
omit the optional field and continue to use the numeric focal token.

- allowed_text and allowed_marks are the only words and marks permitted on
  the image;
- forbidden_legacy_tokens prevents a previous lens job leaking into a new
  one;
- anchors separates verified facts, sourced folklore, and visual inspiration;
  each anchor carries a unique stable `anchor_id`;
- generation records the actual provider/mode, model/version, prompt,
  reference hashes, candidate hash, and human approval;
- design_review is schema-v2, candidate-hash-bound evidence for the structural,
  anti-generic, completion, and printable-reduction gates;
- provenance records copyright, trademark, and source-model terms.

At least one anchor must be a source-backed manufacturer-culture fact. A film,
mission, donor, or rehousing relationship is a separate claim with an explicit
scope: exact lens, family, brand, or visual metaphor. A community nickname can
be attested while its origin story remains contested; do not collapse those
two claims. Keep a qualifier next to any lore that appears in the artwork.

## Prompt and review

The default prompt contract is:

- complete circular cap-front composition on a square canvas (do not prompt it
  as a generic badge/logo/seal unless requested);
- focal length as the largest first read and maximum aperture as the second;
- for a variable-aperture zoom, keep the first F-number as the machine anchor,
  record the full normalized range in `maximum_aperture_display`, and preserve
  that complete range as `display_text[1]`; T-stops are not yet supported;
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

The selected anchor must act as a system: it creates at least two observable,
functionally distinct consequences across typography/counterform,
field/path/division, or container/perimeter. Copying or scaling one icon does
not count. “Minimal”, “clean”, “vintage”, or “printable” are not finish targets;
state observable integration, edge, rhythm, and field relationships. Any image
the user presents as a quality benchmark includes `quality_reference` in its
canonical `roles` array, even if it is also a style or series reference; save a local snapshot and hash, name at
least two transferable traits, and compare them without copying identity or
literal motifs.

Review the candidate at full resolution. Confirm the circle, exact focal and
aperture strings, hierarchy, omissions, palette, structural anchor,
whole-field resolution, visual grammar, finish target, and printable reduction.
If identity, anchor, or large-scale composition fails, change direction; once
those structural checks pass, local typography/edge/spacing/palette fixes are
allowed but every gate must run again. Never silently substitute a near model
name.

## Handoff to production

After approval:

1. copy the selected candidate to a descriptive path inside the job;
2. create the job with `lens-cap init ... --lens-identity ... --display-text
   FOCAL APERTURE ...`; the text list is the complete ordered closed set,
   including every secondary model/system line;
3. review the job TOML circle, complete palette (including relief heights),
   grid, safe border, prefilter, cleanup, and assembly mode;
4. run `lens-cap handoff-init JOB.toml --brand ... --model ...
   --focal-length ... --maximum-aperture ... --provider ...` to scaffold a
   brief with the candidate hash, mechanical values, and (when alpha is
   present) a reviewed circle suggestion;
5. verify each http(s) or explicit archive anchor source, replace its
   `to_verify` evidence state and every
   scaffold placeholder, complete all provenance licence fields, confirm the
   full `display_text`/`allowed_text` set and circular composition, then fill
   schema-v2 `design_review`: exact reviewed hash, structural hero index plus
   matching stable id, two distinct-system consequences explicitly bound to
   that same anchor id, all completion booleans, structural thesis,
   observable finish target, exact quality-reference coverage, and reviewer
   evidence. Set `generation.approved=true` only after that human review;
6. run `lens-cap handoff-check JOB.toml` and then the production CLI;
7. compare the process master and role masks before any external adapter;
8. if the requested deliverable is a 3MF, continue through the canonical
   bridge and verify that the `.3mf` actually exists and passes package and
   projection checks.

This approval handoff is an intentional human gate in Alpha. Image-generation
providers expose their result differently (a conversation attachment, a local
file, or a download), so the repository does not pretend it can discover or
approve an arbitrary provider result automatically. The minimum handoff packet
for a new Codex task is:

1. the approved raster copied inside the job (normally `art/master.png`), with
   its SHA-256;
2. schema-v2 `design-brief.json` containing `approved=true`, the exact `display_text`,
   `allowed_text`/`allowed_marks`, the prompt/provider record, anchor evidence,
   and licence/provenance notes;
3. a job TOML containing the current palette and, for an opaque raster, the
   reviewed `[circle].center_px` and `radius_px`;
4. for a fitted cap, the measured mating diameter, foam status/thickness, and
   rib choice in the same job.

Do not mark a candidate approved, invent a missing hash, or infer circle
coordinates from a cropped preview merely to keep the chain moving. The
clean-room rehearsal starts *after* this packet exists; it proves the
deterministic packet-to-3MF route, not a provider-specific save/download API.
On a host without OpenSCAD it may still prove the interaction and deterministic
preflight, but its top-level production status remains `unverifiable`; that is
not an actual 3MF success.

Production auditing cannot silently redesign an approved candidate. It does
not run a nozzle-derived minimum-feature scan. Only a target slicer's actual
toolpath preview determines whether narrow details survive. If the user
explicitly requests a simplified print-safe design after that review, create a
separate candidate, SHA-256, and approval; never overwrite the master.

For a standalone printable front, ask for the finished face diameter; nozzle
information is needed only for target-slicer review. For a fitted cap, ask for the actual mating
outside diameter (前口径), the foam plan, and whether to retain the inner-wall
friction ribs. Ribs default to on; only an explicit smooth-wall request
disables them. The confirmed diameter also sets the face/relief diameter by
default. The production Skill asks this gate before modeling and does not ask
users to choose an assembly structure.
If the adapter's nominal diameter and one-side radial wall are also known, they
may be recorded as the optional `--adapter-nominal-ring` /
`--adapter-radial-wall` pair. The production configuration verifies
`nominal + 2 × radial wall = measured mating diameter` within 0.05 mm. This is
provenance for the envelope, not a fourth mandatory question; the actual
measured mating diameter remains the sole required mechanical dimension.
If a supplied reference shows chunky internal projections, record the neutral
`wide_tapered` profile request for production; keep the default
`light_tapered` profile for ordinary compatibility. This mechanical choice
must not alter the approved focal-length/aperture artwork, and the reference
archive remains provenance-only rather than a mesh to copy.

The design brief is a human/provenance contract. `handoff-init` only writes a
review-required scaffold; it does not infer brand history, approve a provider
result, or call an image service. Its `--provider` argument is required so the
scaffold cannot silently record a null provider. It copies the complete
`metadata.display_text` list from the job and refuses focal/aperture
disagreement; `handoff-check` requires both brief text lists to remain exactly
equal to that complete job list. It also snapshots the job's circle, full
palette (including relief heights), grid, safe border, prefilter, cleanup, and
assembly mode; edit those before scaffolding, or rerun
`handoff-init --force` before approval if they change. Anchor mapping fields
must be substantive, sources must be http(s) or explicit archive identifiers,
and evidence states must start with an explicit positive sourced/verified
status. All provenance licence/brand-mark/notes fields are mandatory; a
reasoned `not applicable — ...` statement is valid, while bare `NONE`/`N/A` is
not.
`handoff-check` and the canonical
`bin/lens-cap-3mf` bridge verify the approved flag, exact identity/text order,
candidate/review/source hash chain, structural hero and cross-system evidence,
completion and quality-reference checks, culture/rehousing anchor, and licence fields. A missing or
unapproved or mismatched brief is `FAILED` at the 3MF endpoint, while private
relief-only experiments may still use the core `process`/`model` commands.
Treat any `physical_fit` data in the brief as a snapshot from approval time.
For multi-diameter production, each job TOML is authoritative for measured
diameter, wall/bottom/side dimensions, bare clearance, liner, ribs, and adapter
envelope. Nozzle and print settings are separate slicer metadata and must not
change pre-slicer artwork or geometry. Never propagate one size's brief summary over
another job.

When the user asks for an actual 3MF, neither the concept image nor a generated
SCAD/STL/handoff is a terminal result. Success requires the existing output
`.3mf` plus the canonical verification evidence. A passed interaction or
preflight with `status=unverifiable` must be reported as incomplete production,
not upgraded to success.

The concept Skill and first-party templates are distributed with the source
repository/sdist. The installed CLI wheel is intentionally provider-neutral;
it does not bundle a proprietary image SDK or claim access to a particular
image-generation service.

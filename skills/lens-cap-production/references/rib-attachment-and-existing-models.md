# Rib attachment and existing-model edits

Read for friction-rib work and for explicit structural edits to a supplied cap.
This is a workflow contract, not a universal mesh-editing command. Use a
versioned adapter/script appropriate to the actual file and available tools;
do not assume any past job-local script, object ID, path, printer or dimensions.

## Preserve the user's chosen model

When the user asks to modify a particular 3MF/STL/SCAD while keeping its front,
use that file as the baseline. Do not regenerate from a preview PNG, revectorize
artwork, replace the shell with a template, resize the face, or change existing
chamfers unless separately requested. The source-model hash and explicit edit
scope replace the artwork-packet prerequisite for this branch only. Do not
fabricate a design brief or claim an artwork-to-model bridge run. New artwork
and new builds still require their normal approval gates.

Keep the source recoverable; normally deliver a clearly named edited copy.
Record current mating diameter, foam/adhesive thickness and rib preference in
the edit manifest; reuse confirmed current-job values and ask only what is
missing. For a diagnosis-only request, inspect and report without modifying.
Preserving a supplied model for an authorized local edit does not grant public
redistribution rights.

For a requested front-edge or badge/body-junction edit, also read
[Front edges and face junctions](front-edge-and-face-junction.md). Identify
which edge is authorized to change; the protected front artwork stays locked.

## Measure before adding geometry

- Hash the exact target and any reference. Resolve units, component paths,
  nested transforms, orientation and the intended structural body before
  measuring; never assume object 1 or that the opening is at Z=0.
- Measure the actual smooth cavity and outer wall at the intended rib heights,
  plus the floor, opening, chamfers and existing ribs. Keep actual geometry,
  user mating diameter and proposed tip clearance/interference separate.
- Measure supplied reference meshes, not just SCAD defaults or filenames.
  Record count/spacing, root and tip footprint, radial projection, axial span,
  lead-in, and whether the nominal size describes the cavity or tip contact.
- For an existing-shell edit, retain its actual cavity and derive ribs to
  bridge from the desired contact surface into that wall. Do not move roots
  using a preset's nominal cavity. If the preserved shell cannot support the
  requested fit, explain the conflict and ask before changing the shell.

For a centred circular cavity and a straight-chord wedge, let `Rwall` be the
actual inner-wall radius, `Rroot` the root-corner radius, and `a` the full root
angle. The root chord's minimum radius is `Rroot × cos(a/2)`, not `Rroot`.
Require `Rroot × cos(a/2) > Rwall + overlap_margin` over its full footprint,
with a positive, recorded margin larger than numerical uncertainty and still
inside the outer wall. For noncircular walls or chamfers, check actual local
intersections instead of applying this circle formula blindly.

Likewise, a straight tip with corner radius `Rtip` and full angle `b` has
contact-plane distance `Rtip × cos(b/2)`. With centred, symmetric opposing
tips, the flat-to-flat diameter is `2 × Rtip × cos(b/2)`. Report contact-plane
fit rather than only the larger corner-to-corner diameter; for other patterns,
measure the limiting inscribed mating circle. Choose fit allowance from the
current job, not from a previous cap's numeric result. With foam, include its
compressed stack-up and local compression; do not apply bare-plastic clearance.

## Fuse and verify the final artifact

1. Establish positive-volume intersection between each rib and the side wall.
   Boolean-union into the structural body and remove internal duplicate
   surfaces. Merely appending meshes in one object or relying on slicer gap
   closing is not a repair. Floor contact alone does not establish wall contact.
2. Reopen the delivered mesh/project, not just the pre-export CAD. Require the
   edited structural body to be a closed connected solid with no open edges,
   non-manifold edges, zero-area faces or inconsistent orientation. In a
   multipart colour project, audit the body separately; intentional relief
   islands are not detached ribs. Do not flatten material assignments to make
   the whole project's component count equal one.
3. Check every rib near both ends, at interior heights and at profile changes,
   using sections or equivalent volume tests across the root width. Sampling
   must resolve the narrowest contact region. Verify continuous material from
   each tip through its root into the wall, with no intervening cavity gap.
   Check actual tip fit and axial span. A single connected-component result
   can hide ribs connected only through the floor; it is insufficient alone.
4. Prove unchanged regions: preserve untouched front meshes, palette/material
   assignments and transforms byte-for-byte where feasible. If serialization
   or Boolean tessellation changes, compare decoded geometry/materials with a
   recorded numeric tolerance. Validate protected regions of the face, outer
   silhouette and chamfers. Separately record expected changes at an explicitly
   authorized edge; do not reject that intended change as preservation drift.
   Do not smooth/reduce artwork to obtain a pass.
5. Preserve an existing Bambu project's real configuration; replace only the
   intended structural mesh and necessary derived counts. Never fabricate a
   Core file's missing Bambu configuration. Remove or regenerate stale sliced
   toolpaths after any geometry change. Retain unsliced print settings unless
   the user asks for changes. Validate ZIP/XML, component references and bounds,
   then test import with the installed target slicer. Compare pre-existing
   warnings with the source rather than attributing them silently to the edit.

## Evidence and delivery

Identify this as an existing-model edit in the report, with `primary_3mf`
pointing to the exact edited 3MF being delivered (when 3MF is requested).
Save source/output and adapter/tool hashes or versions, resolved measurements,
fit/foam assumptions, per-rib intersection/continuity results, structural
topology, unchanged-region comparison, and slicer import result. Include a
real-mesh section or underside view when explaining attachment; a render alone
does not replace topology checks. Distinguish artifact integrity, slicer
import, actual toolpath review and physical fit. CLI import is not GUI preview;
neither proves retention on the lens. Report unperformed checks honestly and
require a same-material coupon or real fit test before claiming physical fit.

Deliver the edited file with its report, not a freshly rebuilt substitute.
Do not call this branch passed until preservation, wall continuity and package
checks pass; if a required tool/check is unavailable, state the specific
`UNVERIFIABLE` result instead of handing off unverified ribs as fixed.

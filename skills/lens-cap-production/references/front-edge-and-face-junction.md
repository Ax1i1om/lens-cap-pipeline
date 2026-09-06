# Front edges and face-to-body junctions

Read for front-edge finishing, raised badges/medallions, face-to-cap assembly,
or a reported seam/white rim. This supplements the existing-model preservation
and rib rules; it does not authorize redesigning approved artwork.

## Choose the correct edge

| Feature | Intended treatment | Preserve |
| --- | --- | --- |
| Closed-front outer cap edge | Small mechanical bevel when requested | Cavity, fit, ribs, artwork footprint and support |
| Raised badge perimeter/shoulder meeting the body | Supported, intentional transition at its structural base | Approved top outline, pattern, height and palette |
| Numerals, lettering, motifs and internal colour boundaries | No bevel/rounding without an explicit artwork-change request | Strokes, counters, line positions and visible colours |

For a new integrated cap, prefer the artwork's substrate to be continuous with
the cap floor, not a floating disc. Keep an intentionally raised badge or step
when it is part of the approved design. A valid junction need not be filleted
or tangent-smooth. Do not add a border, groove, glue gap, separate insert or
globally rounded silhouette just because the user wants a cleaner connection.
For existing models, change only the requested edge/junction.

## Outer-front micro-chamfer

The current public control is `[fit].front_outer_chamfer_mm` (init flag
`--front-outer-chamfer`). It creates a 45-degree bevel on the closed cap's
outer circumference only; `0` keeps the square edge. It is not a badge
shoulder fillet, a relief-edge bevel, or the opening's inner lead-in.
Do not invent CLI fields for those features. Use a recorded adapter if another
transition must be implemented, or report it unsupported; do not pretend the
outer-bevel flag performs that operation.

When a user asks for a small chamfer without a size, `0.30 mm` is a possible
initial candidate, not a universal default. Choose it after reading actual
wall/floor thickness and available front land, not from nozzle diameter.
Do not silently enable it for an unrelated existing-model edit.

For a centred circular body of outer radius `Ro`, a 45-degree bevel of size `c`
leaves top radius `Ro - c` and occupies the last `c` of axial height. Keep
`0 <= c < min(wall_thickness, floor_thickness)` and retain support under the
complete structural face footprint. The current centred adapter checks
`face_diameter <= outer_diameter - 2*c`; imported models require transformed
footprint/local section measurements, not merely a bounding diameter. Record
the narrowest remaining support margin. Equality is a geometric limit, not an
automatic manufacturing safety margin.

If the chosen bevel undercuts the face, reduce it within the user's allowed
scope. If no requested nonzero bevel fits, explain the conflict before changing
the face or shell envelope. Never shrink/crop artwork, widen a border, or leave
an unsupported lip to make a bevel fit. A smaller applied bevel must be reported,
not silently presented as the originally requested size.

## Build a real joint without changing the face

Resolve assembly units/transforms and identify the cap floor, face substrate,
visible relief tops and actual interface height. Keep the approved XY mapping,
top elevations and materials locked. Correct verified adapter-introduced
misalignment, not the approved graphic's composition. A part's bounding-box
centre is not automatically the artwork's intended datum.

For same-material cap and substrate, use positive-volume construction overlap
or an equivalently robust solid union and export one closed structural volume.
Point/edge-only contact, duplicate coplanar faces and grouped meshes do not
prove this. If overlap needs increasing, extend only the hidden underside
within permitted structural space while retaining visible top heights; do not
lift, thicken or soften the visible design. Choose overlap from geometry and
Boolean precision, then verify the result rather than trusting an epsilon.

For multicolour relief, preserve closed material parts and their shared frame.
Each intended relief island must have a positive-area interface to its support,
directly or through the intended supporting layer, without an air slit. A
material-aware partition is valid, but it is not the only supported export.
The current Bambu path uses closed material parts with a small construction
overlap; do not misdiagnose normal colour islands or this recorded overlap as
detached geometry. Do not remove it simply to force a zero-overlap statistic.
Record the overlap's location/size and verify material mapping and actual
toolpath resolution when claiming sliced/print-ready. Unexplained competing
volumes, colour leakage or duplicate extrusion paths fail. A monochrome union
can be a separate integrity audit, never a substitute colour deliverable.

If the requested edit is a raised badge's shoulder, work on the structural
base/riser. A bevel removes material; a fillet can add visible material and
change the silhouette. Preserve the protected top footprint and check its
support before either. Prefer a compact, continuous transition in the intended
substrate material where space permits; introduce no pale filler ring, extra
disc or circumferential void. If it cannot fit without altering the approved
face, explain that choice instead of applying an unrelated outer-cap bevel.

## Diagnose rims before cleaning up

A hairline can be geometry, unintended material, XY/Z mismatch, duplicate
coplanar surfaces, raster boundary contamination, or selection/shading only.
Compare the approved source/model with final material surfaces and an unselected
view or section before editing. Do not erase a legitimate circular graphic,
smooth the raster, alter `safe_border_mm`, or run cleanup to hide a structural
defect. Remove only a verified introduced artifact within scope; changes to
approved-source content require the user's design decision.

## Final-mesh acceptance

- Measure the intended bevel's axial/radial dimensions around its perimeter,
  including transition endpoints. A config value or lit highlight is not
  evidence that the delivered mesh contains the requested bevel.
- Inspect sections just below, through and above the actual face/body joint,
  including its perimeter, thin support regions and profile changes. Check
  positive-area contact/controlled overlap for all intended islands; do not
  fill intentional artwork holes. Reject unintended circumferential gaps,
  duplicate structural surfaces, unsupported lips and accidental layer steps.
  Include local narrow/critical regions; uniform angular samples can miss them.
- Verify closed connected same-material structure and the multicolour interface
  strategy. Compare protected geometry, visible material footprints, elevations,
  transforms and fit features against the approved baseline. Existing overlaps
  outside an imported model's edit scope are reported, not silently rebuilt.
- Inspect top and oblique views with actual assigned materials. A connected
  solid may still have a wrong-colour ring or unintended shoulder. Palette
  collapse, face shrinkage and nozzle-based simplification are not repairs.
- For a Bambu deliverable, test import. If claiming sliced/print-ready, inspect
  interface/bevel layers and colour transitions for gaps, duplicate paths and
  unsupported spans. Do not use gap closing, brim or purge-tower placement to
  repair geometry. Separate import, toolpath review and physical strength/fit.

Save the chosen edge/transition dimensions, support margin, interface/material
evidence, preservation comparison, hashes and views. Separate the authorized
edge's expected geometry change from protected regions that must stay unchanged.
These are acceptance
requirements, not a claim that all are automated. The current generator does
not expose a badge-shoulder fillet or dedicated final-mesh chamfer/junction
audit. Existing-model edits retain their source-preserving contract; new
artwork-based jobs keep their canonical build and approval gates.

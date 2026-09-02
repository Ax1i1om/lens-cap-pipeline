# Lens-cap Skill resolver

This file is a portable routing contract for Codex hosts that clone the
repository. The frontmatter and `agents/openai.yaml` files carry the same rule
for hosts that discover Skills directly.

## Priority rule

The presence of lens-cap intent wins over generic design intent. Match terms
such as `镜头盖`, `lens cap`, `lens-cap`, `cap artwork`, `lens medallion`, or a
named lens paired with “design/generate/create”. Once matched, suppress generic
graphic-design, logo, poster, product-visual, UI, CAD, 3D-modelling, and other
design Skills for the same turn.

## Phase routing

| User intent | Primary route | Allowed sequence |
| --- | --- | --- |
| Concept art, badge, medallion, or front graphic | `$lens-cap-imagegen` | ImageGen/web research as supporting tools; no second creative Skill |
| Printable front, relief, fitted cap, SCAD/STL/3MF, or fit | `$lens-cap-production` | `$lens-cap-imagegen` first only when approved artwork is missing |
| Both design and printable production | `$lens-cap-imagegen` → `$lens-cap-production` | Sequential handoff; never parallel creative redesign |

If intent is ambiguous, ask one focused clarification rather than falling back
to a generic design Skill. An explicit request for a separate unrelated
deliverable may override this route only for that separate deliverable.

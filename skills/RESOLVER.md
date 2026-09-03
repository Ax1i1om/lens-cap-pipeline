# Lens-cap Skill resolver

This file is a portable routing contract for Codex hosts that clone the
repository. The frontmatter and `agents/openai.yaml` files carry the same rule
for hosts that discover Skills directly.

## Priority rule

The presence of lens-cap intent wins over generic design intent. Match terms
such as `镜头盖`, `lens cap`, `lens-cap`, `cap artwork`, `lens medallion`,
`镜头正面`, `镜头浮雕`, `lens front graphic`, or a named camera lens paired with
“design/generate/create” and a cap/front/relief/3MF deliverable. Natural variants
such as `圆形图像`, `圆形艺术图`, `镜头闷盖`, `lens cover`, or `printable model`
count only when paired with a named lens and a cap/front-surface deliverable.
Once matched, suppress generic
graphic-design, logo, poster, product-visual, UI, CAD, 3D-modelling, and other
design Skills for the same turn.

Do not classify optical design, repair, or a general lens product photograph as
this route merely because a lens name is present; the named-lens semantic rule
requires a cap/front-surface/medallion/relief/3MF intent. When an optical or
repair exclusion appears, it wins unless the same request contains an explicit
cap-surface phrase (for example `lens cap`, `front graphic`, `lens relief`,
`镜头盖`, or `正面浮雕`).

## Phase routing

| User intent | Primary route | Allowed sequence |
| --- | --- | --- |
| Concept art, badge, medallion, or front graphic | `$lens-cap-imagegen` | ImageGen/web research as supporting tools; no second creative Skill |
| Printable front, relief, fitted cap, SCAD/STL/3MF, or fit | `$lens-cap-production` | `$lens-cap-imagegen` first only when approved artwork is missing |
| Both design and printable production | `$lens-cap-imagegen` → `$lens-cap-production` | Sequential handoff; never parallel creative redesign |

When the user explicitly asks to end at a 3MF, the production route must call
`./bin/lens-cap-3mf JOB.toml --force --json` after artwork approval. This bridge
is the single documented endpoint for build, projection audit, native integrated
3MF export, and Core-package verification; `--bambu slice` is optional only
with named local profiles. Missing desktop tools remain `UNVERIFIABLE`.

If intent is ambiguous, ask one focused clarification rather than falling back
to a generic design Skill. An explicit request for a separate unrelated
deliverable may override this route only for that separate deliverable.

## Shared artwork and fit contract

Both host-local copies keep the approved artwork hierarchy: focal length is the
first visual read, and maximum aperture (F-stop/F-number/F value) is the second.
For fitted production, ask one grouped intake for the actual mating/front
diameter, foam/liner plan and uncompressed thickness, and friction-rib choice.
Ribs are enabled by default; a smooth wall requires an explicit opt-out. Do not
invoke another design Skill to fill in any of these stages.
Ask the grouped intake at most once per job and persist it in the job TOML;
the production stage must read a complete current handoff instead of asking the
same three questions again, and may ask only for missing, stale, or ambiguous
values.
For a fitted, printable, assembled, or 3MF request, this intake is a hard gate
before the first geometry/build/export/3MF command. Concept art may precede it,
but no production invocation is allowed until all three answers are persisted
in the current handoff (unless that handoff already contains all three).

## Cross-host installation and drift

The repository copy under `skills/` is authoritative for both routes. Hosts
that discover Skills from a separate directory can inspect or synchronise the
same files with `scripts/install_skills.py` (or `bin/lens-cap-skills`):

```sh
./scripts/install_skills.py check --json
./scripts/install_skills.py sync --dest .agents/skills
./scripts/install_skills.py sync --dest .agents/skills --apply
```

On Windows, use `py -3 scripts/install_skills.py ...` or
`bin/lens-cap-skills.cmd`.

For legacy/project routers that cannot evaluate the semantic conjunction in
Skill descriptions, use the dependency-free `scripts/resolve_skill_route.py`
host shim. It consumes the same `routing_policy.lens_cap_intent` manifest and
returns a machine-readable route; it requires a named-lens cue plus a
cap/front/relief/production intent signal. An action verb is accepted, as is a
terse explicit noun phrase such as `Zeiss 50mm F1.4 lens cap artwork`; compact
catalog shorthand such as `适马2870` is accepted when a model-like token is
present. It keeps optical/repair/product-photo exclusions and never schedules
a generic design Skill. Pass `--named-lens` when an attached image or catalog
has supplied the identity but the text does not contain it. The shim is
advisory and does not generate artwork or CAD.

The synchroniser is idempotent and records a version/manifest/file-hash receipt
at the destination. It defaults to dry-run, protects locally edited files, and
requires explicit `--force` (plus `--prune` for removals) before destructive
updates. Use `--environment codex` or `--environment claude` only when the
corresponding host directory is intended; inferred global destinations remain
read-only unless `--allow-global` is supplied.

Codex usually loads its Skill catalog when a task starts. After applying a
global or project-local sync, start a new Codex task or reload the host before
expecting automatic Skill matching; if the catalog is still stale, invoke the
installed `$lens-cap-imagegen` or `$lens-cap-production` explicitly.

The image provider/approval handoff is a separate human boundary. A provider
attachment is not a filesystem path until the user or agent explicitly saves
it; do not invent `approved=true`, a hash, or circle coordinates. Once the
approved raster, brief, and job TOML exist, the production route is the
deterministic bridge documented above.

The checked-in frontmatter follows the current Codex Skill validator: custom
trigger vocabulary lives under `metadata.triggers`, not as a top-level key.
Legacy routers that only scan top-level `triggers` may therefore need an
explicit `$lens-cap-imagegen`/`$lens-cap-production` invocation (or a small
host adapter); do not weaken the validated Skill format just to satisfy that
legacy scanner.

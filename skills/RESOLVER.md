# Lens-cap Skill resolver

This file is a portable routing contract for Codex hosts that clone the
repository. The frontmatter and `agents/openai.yaml` files carry the same rule
for hosts that discover Skills directly.

## Priority rule

The presence of lens-cap intent wins over generic design intent, but the object
must be bound positively. Match `镜头盖`, `镜头前盖`, `镜头帽`, `镜头闷盖`,
`前盖正面`, `实体盖`, `lens cap`, `lens-cap`, `front cap`, or
artwork/front-face/model wording that
explicitly belongs to that cap. This explicit object claims the exclusive
imagegen route even when identity is missing; the Skill then asks for brand and
model. Controlled omission-friendly forms require credible identity and include
a circular image for a named lens, `circular lens badge`, `circular lens-front
artwork`, and `圆形镜头正面图稿`. A named camera lens does not turn bare
`cover`, `badge`, image, or an incidental format word into a cap request.
Consequently article
covers, lens-review badges, printable posters photographed through a lens,
lens-pouch dust covers, and photo-to-3MF metadata exports remain outside.
In an already established lens-cap conversation, the resolver also accepts a
short, named-lens continuation such as `试试康泰时28F2` or `test Sigma 28-70`;
this narrow exception is limited to try/test wording with a model-like numeric
cue and does not override optical/repair/product-photo exclusions. The
standalone shim requires `--lens-cap-context` for this exception; without that
explicit context flag, the same terse phrase is rejected in a clean task. A
contextual turn that omits the cap noun must still contain a lens noun,
focal-plus-aperture specification, or known lens identity/alias (including
`八羽怪` and `八枚玉`).
Once matched, suppress generic
graphic-design, logo, poster, product-visual, UI, CAD, 3D-modelling, and other
design Skills for the same turn.

Do not classify optical design, repair, or a general lens product photograph as
this route merely because a lens name is present. When an exclusion appears,
it wins unless the same request contains a positively identified cap object
(for example `lens cap`, `front face of a lens cap`, `镜头盖`, or `前盖正面`).
An object mention inside an explicit local negation (`do not design a lens
cap`, `not a lens cap request`, `anything but/other than a lens cap`,
`except/without/skip a lens cap`, `不要设计镜头盖`, `不是镜头盖`, `镜头盖不要`,
`不想要/跳过镜头盖`, `除镜头盖外`) is not positive cap intent. Likewise,
showing/auditing/updating/fixing/debugging/testing the lens-cap Skill,
generator, pipeline, trigger, tools, task, or documentation; format/capability
or task-status questions; task open/archive commands; and read-only questions
about a cap are meta tasks rather than cap deliverables. These vetoes are also
top-level for a quoted request inside prompt/route testing, interaction
simulation/replay, or an explicit read-only/no-file audit; quoted affirmative
verbs are test data. The ordinary meta vetoes remain clause-aware: a later, explicit
affirmative request to design/create/produce a lens cap in the same turn still
routes.
Chromatic-aberration, distortion, bokeh, 色差、畸变、焦外 tests remain excluded
even when the caller supplies established lens-cap context. Inspecting format
support or explaining an STL/3MF export is documentation rather than production.
Production additionally requires a cap-owned physical, fitted, printable,
model/part, CAD/SCAD/STL/3MF, or printer-handoff signal. Named-lens plus
production-file/model shorthand is accepted only with explicit
`--lens-cap-context`, not in a clean task. Lens body/barrel, focusing-ring/gear,
hood/遮光罩, mount, cage, plate, label, case, and grip deliverables cannot borrow sticky
context. Metadata/photo/manual/review/warranty/poster/pouch ownership also vetoes it.

## Phase routing

| User intent | Primary route | Allowed sequence |
| --- | --- | --- |
| Cap-owned concept art, badge, medallion, or front graphic | `$lens-cap-imagegen` | ImageGen/web research as supporting tools; no second creative Skill |
| Printable cap/front-cap, cap relief/model/part, SCAD/STL/3MF, or fit | `$lens-cap-production` | `$lens-cap-imagegen` first only when a new artwork-based build lacks approved artwork |
| Explicit structural edit of a supplied cap model, with its front preserved | `$lens-cap-production` | Existing-model branch; no image regeneration or template-shell replacement |
| Both design and printable production | `$lens-cap-imagegen` → `$lens-cap-production` | Sequential handoff; never parallel creative redesign |

For a new artwork-based build ending at a 3MF, the production route must call
`./bin/lens-cap-3mf JOB.toml --force --json` after artwork approval. If the
brief has not been scaffolded, run `lens-cap handoff-init JOB.toml` and review
all placeholders first; `lens-cap handoff-check JOB.toml` is the preflight. This bridge
is the single documented endpoint for build, projection audit, native integrated
3MF export, and Core-package verification. Deliver its `primary_3mf`: native/Core
is the portable geometry-audit master, not a Bambu project. For a Bambu
destination use `--bambu export` with named local machine/process/filament
profiles; reserve `--bambu slice` for an explicit embedded-toolpath request.
Missing desktop tools remain `UNVERIFIABLE`.

An explicit edit of a supplied model uses the production Skill's
`references/rib-attachment-and-existing-models.md` instead of that build bridge.
The source-model hash and requested edit scope bind preservation; missing
original artwork does not authorize redrawing it. This branch still requires
actual-wall measurement, rib fusion/continuity and preservation/package/import
checks, and must not claim a bridge run that did not occur.

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
returns a machine-readable route. A positive cap object is sufficient to enter
identity intake; omission-friendly artwork forms still require a credible
named-lens cue. An action verb is accepted, as is a terse explicit noun
phrase such as `Zeiss 50mm F1.4 lens cap artwork`; compact catalog shorthand
such as `适马2870` is accepted when a model-like token is present and a cap
object is also named. Terse `试试／test` continuation
wording additionally requires `--lens-cap-context`. It keeps
optical/repair/product-photo exclusions and never schedules
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
it; do not invent `approved=true`, a hash, or circle coordinates. The
provider-neutral `handoff-init` command computes hashes and an alpha-circle
suggestion but never approves or calls an image service. Once the approved
raster, brief, and job TOML exist, the production route is the deterministic
bridge documented above.

The checked-in frontmatter follows the current Codex Skill validator: custom
trigger vocabulary lives under `metadata.triggers`, not as a top-level key.
Legacy routers that only scan top-level `triggers` may therefore need an
explicit `$lens-cap-imagegen`/`$lens-cap-production` invocation (or a small
host adapter); do not weaken the validated Skill format just to satisfy that
legacy scanner.

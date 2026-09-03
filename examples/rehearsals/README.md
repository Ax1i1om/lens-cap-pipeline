# Clean-room interaction scenarios

`helios-44-2-rehouse-clean-room.json` and
`mamiya-sekor-c-80-f1-9-rehouse-clean-room.json` are minimal four-turn
transcripts for the public Alpha acceptance gate. The second scenario proves
that the same interaction contract carries a different maker and a foam-lined
85 mm adapter envelope. Copy either one for another named lens and change:

- `conversation.turns`: the actual user/agent messages from a fresh task;
- `fixture.path` and `fixture.primary_job`: the approved brief and persisted
  TOML to exercise;
- optional `fixture.identity_terms`: translated brand/model aliases when the
  first user turn is not written in the brief's canonical Latin spelling;
- `design-brief.json` keeps `lens_identity.focal_length_mm` as a positive
  numeric machine anchor. For a zoom, add the optional sibling
  `lens_identity.focal_length_display` (for example `28–70mm`) and make that
  exact token the first `display_text`/`allowed_text` item. The range must be
  positive, ascending, and begin at `focal_length_mm`. Prime briefs can omit it
  and keep the numeric focal-length behavior;
- `intake.answer`: the three values the user supplied, including the real
  mating diameter, optional `adapter_nominal_ring_mm` +
  `adapter_radial_wall_mm` pair, and any uncompressed foam thickness. When the
  pair is present it must satisfy `nominal + 2 × radial wall = mating` and
  match the selected job metadata.

Keep the contract explicit:

1. `workspace.conversation_history` is `none` and `workspace.filesystem` is
   `isolated_temp`;
2. `route.skills` is exactly `lens-cap-imagegen` followed by
   `lens-cap-production`, with no generic design Skill in parallel;
3. `intake.questions` is the canonical three-field list and
   `intake.asked_once` / `persisted_in_job_toml` are true;
4. the selected job TOML agrees with the answered diameter, foam decision,
   rib enabled/explicit flags, and rib profile; and
5. `production.endpoint` names `./bin/lens-cap-3mf`.

The optional `intake.answer.friction_ribs_explicit` flag records whether the
user made a deliberate rib choice. It is `false` for the default-on path and
must be `true` for an explicit smooth-wall opt-out (or an explicitly selected
profile). The validator also checks that the persisted flags and the answer
text agree on foam/rib polarity, and rejects a claimed production command
before the grouped intake. A future/conditional mention such as “批准后再运行…”
is allowed; it is not treated as an executed command.

Run it with:

```sh
python3 scripts/rehearse_user_agent.py my-rehearsal.json --bambu never --json
```

An external scenario file is allowed. Relative fixture paths resolve beside
that file when it is outside the checkout; absolute fixture paths are also
accepted. The runner only reads scenario/fixture inputs and writes generated
3MFs when an explicit `--artifact-dir` is supplied.

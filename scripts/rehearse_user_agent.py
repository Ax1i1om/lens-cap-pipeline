#!/usr/bin/env python3
"""Replay a structured clean-room user/agent interaction through the 3MF route.

The normal ``smoke_rehouse.py`` runner starts at an already approved artwork
packet.  This companion command adds the small, provider-neutral acceptance
boundary before that runner: it checks that a natural lens-cap request was
routed to the two dedicated Skills, that the three physical intake questions
were asked once, and that their answers were persisted in the selected job
TOML.  It then executes the public clean-room production runner in an isolated
temporary directory.

The transcript is deliberately data, not an LLM transcript generator.  A
Codex/Claude host can record its actual turns in the same JSON shape and use
this command as a deterministic handoff gate; no host-specific conversation
history, design Skill, or provider API is required.  The command never edits
the fixture artwork and only writes generated 3MFs when ``--artifact-dir`` is
explicitly supplied.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any, Mapping

# Keep direct execution usable before an editable install exists.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.resolve_skill_route as route_resolver  # noqa: E402
import scripts.smoke_rehouse as smoke  # noqa: E402

SCHEMA_VERSION = 1
REQUIRED_INTAKE = (
    "mating_outside_diameter_mm",
    "foam_liner_plan_and_uncompressed_thickness_mm",
    "friction_rib_preference",
)
EXPECTED_ROUTE = ("lens-cap-imagegen", "lens-cap-production")
DIAMETER_TERMS = (
    "diameter",
    "mating",
    "front diameter",
    "前口径",
    "口径",
    "卡合",
    "外径",
    "直径",
)
FOAM_TERMS = ("foam", "liner", "泡棉", "内壁贴", "内衬")
RIB_TERMS = ("rib", "friction", "凸条", "凸起", "摩擦", "突条")
FOAM_NONE_TERMS = (
    "no foam",
    "without foam",
    "none",
    "不贴泡棉",
    "不加泡棉",
    "不使用泡棉",
    "无泡棉",
    "不需要泡棉",
)
FOAM_POSITIVE_TERMS = ("foam", "liner", "泡棉", "内衬", "衬垫")
RIB_ON_TERMS = (
    "on",
    "enabled",
    "default",
    "开启",
    "打开",
    "保留",
    "默认",
    "light_tapered",
    "wide_tapered",
)
RIB_OFF_TERMS = (
    "off",
    "disabled",
    "smooth wall",
    "smooth-wall",
    "关闭",
    "关掉",
    "去掉",
    "不保留",
    "不添加",
    "不需要",
    "不要凸条",
    "无凸条",
    "光滑内壁",
    "smooth inner wall",
)
RIB_DEFAULT_TERMS = (
    "default",
    "no preference",
    "按默认",
    "默认",
    "无偏好",
    "不指定",
    "保持默认",
)

# The transcript is allowed to mention a future handoff, but it must not claim
# that a production command already ran before the grouped physical intake.
# Keep this intentionally narrower than a bare “3MF” search: the initial user
# request is expected to name the desired endpoint, and the agent may explain
# the route in prose.  Exact bridge/tool invocations and completion verbs are
# the evidence that a geometry/export action happened too early.
_PRODUCTION_COMMAND_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:\./)?(?:bin/)?lens-cap-3mf\b"
    r"|(?<![A-Za-z0-9_])(?:python(?:3)?\s+)?(?:scripts/)?build_3mf\.py\b"
    r"|(?<![A-Za-z0-9_])lens-cap\s+(?:build|model|export-openscad|bambu-handoff)\b"
    r"|(?:运行|执行|调用|启动|run|invoke|execute|start)\s+"
    r"(?:openscad|bambu(?:\s+studio)?)\b",
    re.I,
)
_PRODUCTION_COMPLETION_RE = re.compile(
    r"(?:已(?:经)?|正在|完成|成功|"
    r"(?:ran|invoked|executed|generated|exported|sliced))"
    r"[^。\n]{0,48}(?:3\s*mf|stl|scad|openscad|bambu)",
    re.I,
)
_FUTURE_PRODUCTION_RE = re.compile(
    r"(?:回答后|答复后|批准后|确认后|完成后|之后|稍后|下一步|再运行|再执行|"
    r"等(?:你|用户|审批|确认)|将会?|计划|会在|拿到|收到|"
    r"after|once|when|later|then|next|upon|following|will|would|pending|approved)",
    re.I,
)


class RehearsalError(ValueError):
    """Raised when a transcript or its persisted handoff is incomplete."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise RehearsalError(f"{label} must be an object")
    return value


def _nonempty_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RehearsalError(f"{label} must be non-empty text")
    return value.strip()


def _number(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise RehearsalError(f"{label} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RehearsalError(f"{label} must be numeric") from exc
    if not math.isfinite(number) or (positive and number <= 0):
        qualifier = "positive " if positive else "finite "
        raise RehearsalError(f"{label} must be a {qualifier}number")
    return number


def _resolve_inside(
    root: Path,
    raw: Any,
    label: str,
    *,
    base_dir: Path | None = None,
    allow_external: bool = False,
) -> Path:
    value = _nonempty_text(raw, label)
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        # Repository scenarios traditionally use paths relative to the
        # checkout.  When a user copies a scenario outside the checkout, fall
        # back to its own directory if the repository-relative spelling does
        # not exist.  This keeps checked-in examples stable while making
        # external, portable transcripts useful.
        repository_candidate = root / candidate
        scenario_candidate = (base_dir / candidate) if base_dir is not None else None
        external_base = False
        if scenario_candidate is not None:
            try:
                scenario_candidate.resolve().relative_to(root.resolve())
            except ValueError:
                external_base = True
        choices = (
            (scenario_candidate, repository_candidate)
            if external_base
            else (repository_candidate, scenario_candidate)
        )
        for choice in choices:
            if choice is not None and choice.exists():
                candidate = choice
                break
        else:
            # Preserve the convention used for this scenario in the eventual
            # error, even when the requested path does not exist yet.
            candidate = scenario_candidate if external_base and scenario_candidate is not None else repository_candidate
    resolved = candidate.resolve()
    if not allow_external:
        try:
            resolved.relative_to(root.resolve())
        except ValueError as exc:
            raise RehearsalError(f"{label} must stay inside the repository: {value!r}") from exc
    return resolved


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(term.casefold() in lowered for term in terms)


def _contains_any_excluding(text: str, terms: tuple[str, ...], excluded: tuple[str, ...]) -> bool:
    """Find a term while ignoring a line that is explicitly negated."""

    lowered = text.casefold()
    if any(term.casefold() in lowered for term in excluded):
        return False
    return any(term.casefold() in lowered for term in terms)


def _premature_production_action(text: str) -> str | None:
    """Return a reason when an agent claims production ran before intake."""

    source = unicodedata.normalize("NFKC", text).casefold()
    # A negated command (“不要运行 …”) is a warning, not evidence of an
    # invocation.  It is still the host's responsibility to keep real tool
    # calls out of the transcript until the gate; this exception only avoids a
    # false positive in a prose warning.
    for match in _PRODUCTION_COMMAND_RE.finditer(source):
        context = source[max(0, match.start() - 80) : min(len(source), match.end() + 80)]
        prefix = source[max(0, match.start() - 32) : match.start()]
        if re.search(
            r"(?:不要|勿|禁止|未|尚未|暂不|不应|不会|不在|do not|don't|without)"
            r"[^。；;\n]{0,24}$",
            prefix,
        ):
            continue
        # A future/conditional handoff is a valid plan in the first agent
        # turn; only an immediate invocation or an already completed command
        # is a gate violation.  The production runner itself is not inferred
        # from a mere mention of the eventual endpoint.
        offset = context.find(match.group(0))
        before = context[:offset]
        after = context[offset + len(match.group(0)) :]
        immediate_action = bool(
            re.search(r"(?:运行|执行|调用|启动|run|invoke|execute|start)\s*$", before, re.I)
        )
        if _FUTURE_PRODUCTION_RE.search(before) or (
            _FUTURE_PRODUCTION_RE.search(after) and not immediate_action
        ):
            continue
        return f"production command appears before intake: {match.group(0).strip()}"
    if _PRODUCTION_COMPLETION_RE.search(source):
        return "production/export completion appears before intake"
    return None


def _contains_focal_length(text: str, focal_length_mm: float) -> bool:
    """Match a focal number as a token, not as a substring of another number."""

    number = re.escape(f"{focal_length_mm:g}")
    return re.search(
        rf"(?<![\d.]){number}(?:\s*mm)?(?![\d.])", text.casefold()
    ) is not None


def _contains_focal_display(text: str, display: str) -> bool:
    """Match a declared prime/zoom focal token in a natural-language turn."""

    try:
        canonical = smoke._canonical_focal_display(display)
    except smoke.SmokeError as exc:
        raise RehearsalError(str(exc)) from exc
    source = unicodedata.normalize("NFKC", text).casefold()
    source = (
        source.replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("‑", "-")
        .replace("‒", "-")
        .replace("－", "-")
        .replace("~", "-")
        .replace("毫米", "mm")
    )
    source = re.sub(r"\bto\b|至", "-", source)
    if "-" in canonical:
        start, end = canonical.split("-", 1)
        pattern = (
            rf"(?<![\d.]){re.escape(start)}(?:\s*mm)?\s*"
            rf"-\s*{re.escape(end)}(?:\s*mm)?(?![\d.])"
        )
    else:
        pattern = rf"(?<![\d.]){re.escape(canonical)}(?:\s*mm)?(?![\d.\-])"
    return re.search(pattern, source) is not None


def _contains_aperture(text: str, aperture: str) -> bool:
    """Match F-number spellings while rejecting a larger adjacent F-number."""

    compact = "".join(aperture.casefold().split())
    if compact.startswith("f"):
        compact = compact[1:]
    compact = compact.lstrip("/")
    if not compact:
        return False
    source = text.casefold().replace(" ", "")
    number = re.escape(compact)
    return re.search(rf"(?<![a-z\d.])f/?{number}(?![a-z\d.])", source) is not None


def _contains_measurement(text: str, value: float) -> bool:
    """Match a physical millimetre value without accepting a larger number."""

    number = re.escape(f"{value:g}")
    return re.search(rf"(?<![\d.]){number}(?:\s*mm)?(?![\d.])", text.casefold()) is not None


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RehearsalError(f"cannot read {label} {path}: {exc}") from exc
    return dict(_mapping(value, label))


def _load_job(job_path: Path) -> dict[str, Any]:
    try:
        import tomllib

        value = tomllib.loads(job_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, ModuleNotFoundError) as exc:
        raise RehearsalError(f"cannot read job TOML {job_path}: {exc}") from exc
    return dict(_mapping(value, "job TOML"))


def _answer_text(turns: list[dict[str, str]], answer_index: int) -> str:
    if answer_index < 0 or answer_index >= len(turns):
        raise RehearsalError("intake.answer_turn_index is outside conversation.turns")
    turn = turns[answer_index]
    if turn["speaker"] != "user":
        raise RehearsalError("intake answer turn must be a user turn")
    return turn["text"]


def _validate_conversation(scenario: Mapping[str, Any], brief: Mapping[str, Any]) -> dict[str, Any]:
    workspace = _mapping(scenario.get("workspace"), "workspace")
    if str(workspace.get("conversation_history", "")).casefold() != "none":
        raise RehearsalError("workspace.conversation_history must be 'none'")
    if str(workspace.get("filesystem", "")).casefold() != "isolated_temp":
        raise RehearsalError("workspace.filesystem must be 'isolated_temp'")

    conversation = _mapping(scenario.get("conversation"), "conversation")
    raw_turns = conversation.get("turns")
    if not isinstance(raw_turns, list) or len(raw_turns) < 4:
        raise RehearsalError("conversation.turns must contain at least four turns")
    turns: list[dict[str, str]] = []
    for index, raw_turn in enumerate(raw_turns):
        turn = _mapping(raw_turn, f"conversation.turns[{index}]")
        speaker = _nonempty_text(turn.get("speaker"), f"conversation.turns[{index}].speaker").casefold()
        if speaker not in {"user", "agent"}:
            raise RehearsalError(f"conversation.turns[{index}].speaker must be user or agent")
        text = _nonempty_text(turn.get("text"), f"conversation.turns[{index}].text")
        turns.append({"speaker": speaker, "text": text})
    if turns[0]["speaker"] != "user":
        raise RehearsalError("conversation must begin with a user request")
    if not any(turn["speaker"] == "agent" for turn in turns[1:]):
        raise RehearsalError("conversation must include an agent response")

    identity = _mapping(brief.get("lens_identity"), "design brief lens_identity")
    identity_tokens = [
        str(identity.get("brand", "")),
        str(identity.get("model", "")),
    ]
    fixture_aliases = scenario.get("fixture", {})
    if isinstance(fixture_aliases, Mapping):
        extra_aliases = fixture_aliases.get("identity_terms", [])
        if isinstance(extra_aliases, list):
            identity_tokens.extend(str(token) for token in extra_aliases)
    identity_tokens = [token.strip() for token in identity_tokens if token.strip()]
    initial = turns[0]["text"]
    # Require a textual brand/model cue, not merely a shared focal number; the
    # latter would let a Sigma/Contax request accidentally reuse a Helios
    # fixture.  Optional ``fixture.identity_terms`` covers a translated brand
    # name or a common revision alias.
    if not any(token.casefold() in initial.casefold() for token in identity_tokens):
        raise RehearsalError("initial user request does not name the fixture lens")
    focal = _number(identity.get("focal_length_mm"), "design brief focal_length_mm", positive=True)
    focal_display_raw = identity.get("focal_length_display")
    if focal_display_raw is None:
        focal_display = format(focal, "g")
        focal_present = _contains_focal_length(initial, focal)
    else:
        focal_display = _nonempty_text(focal_display_raw, "design brief focal_length_display")
        focal_present = _contains_focal_display(initial, focal_display)
    if not focal_present:
        raise RehearsalError("initial user request does not state the fixture focal length display")
    aperture = _nonempty_text(identity.get("maximum_aperture"), "design brief maximum_aperture")
    if not _contains_aperture(initial, aperture):
        raise RehearsalError("initial user request does not state the fixture maximum aperture")
    if not _contains_any(
        initial,
        (
            "lens cap",
            "lens-cap",
            "circular",
            "front graphic",
            "front surface",
            "3mf",
            "printable model",
            "printable lens model",
            "可打印模型",
            "可打印镜头模型",
            "镜头盖",
            "镜头盖正面",
            "镜头闷盖",
            "圆形",
            "正面",
            "浮雕",
        ),
    ):
        raise RehearsalError("initial user request does not state a cap/front-surface deliverable")

    # The transcript's self-reported route is not enough for a clean-room
    # acceptance record.  Run the same manifest-driven host shim against the
    # actual first request so a stale/legacy router cannot claim the dedicated
    # sequence while its semantic matcher would have missed the lens-cap
    # surface.  The shim is deliberately advisory for ambiguous attachments;
    # this transcript has a textual named-lens cue, so no override is needed.
    route_resolution = route_resolver.resolve_request(initial)
    if route_resolution.get("route") != list(EXPECTED_ROUTE):
        raise RehearsalError(
            "manifest route resolver did not select imagegen -> production for the initial request"
        )

    intake = _mapping(scenario.get("intake"), "intake")
    if intake.get("asked_once") is not True:
        raise RehearsalError("intake.asked_once must be true")
    if intake.get("persisted_in_job_toml") is not True:
        raise RehearsalError("intake.persisted_in_job_toml must be true")
    questions = intake.get("questions")
    if questions != list(REQUIRED_INTAKE):
        raise RehearsalError(
            "intake.questions must contain the grouped diameter/foam/rib fields in canonical order"
        )
    answer = _mapping(intake.get("answer"), "intake.answer")
    diameter = _number(answer.get("mating_outside_diameter_mm"), "intake.answer.mating_outside_diameter_mm", positive=True)
    nominal_raw = answer.get("adapter_nominal_ring_mm")
    radial_raw = answer.get("adapter_radial_wall_mm")
    if (nominal_raw is None) != (radial_raw is None):
        raise RehearsalError(
            "intake.answer.adapter_nominal_ring_mm and adapter_radial_wall_mm must be supplied together"
        )
    nominal: float | None = None
    radial_wall: float | None = None
    if nominal_raw is not None:
        nominal = _number(nominal_raw, "intake.answer.adapter_nominal_ring_mm", positive=True)
        radial_wall = _number(
            radial_raw, "intake.answer.adapter_radial_wall_mm", positive=False
        )
        if radial_wall < 0:
            raise RehearsalError("intake.answer.adapter_radial_wall_mm cannot be negative")
        derived = nominal + 2.0 * radial_wall
        if not math.isclose(derived, diameter, abs_tol=1e-6):
            raise RehearsalError(
                "intake adapter envelope must satisfy nominal ring + 2 * radial wall = mating diameter"
            )
    foam_status = str(answer.get("foam_liner_status", "")).strip().casefold()
    if foam_status not in {"none", "foam"}:
        raise RehearsalError("intake.answer.foam_liner_status must be none or foam")
    thickness_raw = answer.get("liner_thickness_mm")
    thickness: float | None
    if foam_status == "foam":
        thickness = _number(thickness_raw, "intake.answer.liner_thickness_mm", positive=True)
    else:
        if thickness_raw not in (None, 0, 0.0):
            raise RehearsalError("a no-foam answer must use null or zero liner_thickness_mm")
        thickness = None
    answer_index_raw = intake.get("answer_turn_index", 2)
    if isinstance(answer_index_raw, bool) or not isinstance(answer_index_raw, int):
        raise RehearsalError("intake.answer_turn_index must be an integer")
    answer_index = answer_index_raw
    answer_turn = _answer_text(turns, answer_index)
    ribs = answer.get("friction_ribs_enabled")
    if not isinstance(ribs, bool):
        raise RehearsalError("intake.answer.friction_ribs_enabled must be boolean")
    profile = answer.get("friction_rib_profile", "light_tapered")
    profile = _nonempty_text(profile, "intake.answer.friction_rib_profile")
    explicit_raw = answer.get("friction_ribs_explicit")
    if explicit_raw is None:
        # The compact transcript format predates this derived field. Infer a
        # default only when the answer actually says “default/no preference”;
        # an affirmative profile choice is treated as an explicit decision.
        explicit = not _contains_any(answer_turn, RIB_DEFAULT_TERMS)
    elif isinstance(explicit_raw, bool):
        explicit = explicit_raw
    else:
        raise RehearsalError("intake.answer.friction_ribs_explicit must be boolean")
    if not ribs and not explicit:
        raise RehearsalError("disabling friction ribs must be an explicit smooth-wall choice")
    if ribs and not explicit and not _contains_any(answer_turn, RIB_DEFAULT_TERMS):
        raise RehearsalError("a non-explicit rib decision must state the default/no-preference choice")
    # Do not trust ``asked_once`` as a purely self-reported flag: require one
    # agent turn *before* the answer that names all three grouped questions.
    # Later persistence messages may repeat values, but must not look like a
    # second intake prompt.
    pre_answer_agent_indexes = [
        index for index, turn in enumerate(turns[:answer_index]) if turn["speaker"] == "agent"
    ]
    grouped_question_indexes = [
        index
        for index in pre_answer_agent_indexes
        if _contains_any(turns[index]["text"], DIAMETER_TERMS)
        and _contains_any(turns[index]["text"], FOAM_TERMS)
        and _contains_any(turns[index]["text"], RIB_TERMS)
    ]
    if len(grouped_question_indexes) != 1:
        raise RehearsalError(
            "the agent must ask the diameter/foam/rib intake together exactly once before the answer"
        )
    if answer_index <= grouped_question_indexes[0]:
        raise RehearsalError("intake answer must follow the grouped agent question")
    for index, turn in enumerate(turns[:answer_index]):
        if turn["speaker"] != "agent":
            continue
        premature_reason = _premature_production_action(turn["text"])
        if premature_reason is not None:
            raise RehearsalError(f"{premature_reason} (turn {index})")
    for index, turn in enumerate(turns[answer_index + 1 :], start=answer_index + 1):
        if turn["speaker"] != "agent":
            continue
        if (
            _contains_any(turn["text"], DIAMETER_TERMS)
            and _contains_any(turn["text"], FOAM_TERMS)
            and _contains_any(turn["text"], RIB_TERMS)
            and _contains_any(turn["text"], ("?", "？", "请确认", "请提供", "询问", "ask", "tell me"))
        ):
            raise RehearsalError(f"agent repeated the grouped physical intake after the answer at turn {index}")
    if not _contains_measurement(answer_turn, diameter):
        raise RehearsalError("intake answer turn does not state the mating diameter")
    foam_terms = FOAM_TERMS + ("不贴", "不加")
    if not _contains_any(answer_turn, foam_terms):
        raise RehearsalError("intake answer turn does not state the foam decision")
    if foam_status == "none":
        if not _contains_any(answer_turn, FOAM_NONE_TERMS):
            raise RehearsalError("a no-foam job must state an explicit no-foam decision")
    elif not _contains_any_excluding(answer_turn, FOAM_POSITIVE_TERMS, FOAM_NONE_TERMS):
        raise RehearsalError("a foam job must state a positive foam/liner decision")
    if not _contains_any(answer_turn, RIB_TERMS + ("默认",)):
        raise RehearsalError("intake answer turn does not state the friction-rib decision")
    if ribs:
        if not _contains_any_excluding(answer_turn, RIB_ON_TERMS, RIB_OFF_TERMS):
            raise RehearsalError("an enabled-rib job must state that ribs are on/retained")
    elif not _contains_any(answer_turn, RIB_OFF_TERMS):
        raise RehearsalError("a smooth-wall job must state that friction ribs are off")
    if foam_status == "foam" and not _contains_measurement(answer_turn, float(thickness)):
        raise RehearsalError("intake answer turn does not state the uncompressed foam thickness")
    if not _contains_any(
        "\n".join(turn["text"] for turn in turns[answer_index + 1 :] if turn["speaker"] == "agent"),
        ("job.toml", "job toml"),
    ):
        raise RehearsalError("agent transcript does not show intake persistence in job.toml")

    route = _mapping(scenario.get("route"), "route")
    skills = route.get("skills")
    if skills != list(EXPECTED_ROUTE):
        raise RehearsalError("route.skills must be imagegen followed by production, with no alternate design route")
    if route.get("generic_parallel_skills") != []:
        raise RehearsalError("generic design Skills must not run in parallel")
    if str(route.get("sequence", "")).casefold() not in {"sequential", "imagegen_then_production"}:
        raise RehearsalError("route.sequence must document sequential imagegen → production handoff")

    agent_text = "\n".join(turn["text"] for turn in turns if turn["speaker"] == "agent")
    if not _contains_any(agent_text, ("lens-cap-imagegen", "$lens-cap-imagegen")):
        raise RehearsalError("agent transcript does not show the dedicated imagegen Skill")
    if not _contains_any(agent_text, ("lens-cap-production", "$lens-cap-production")):
        raise RehearsalError("agent transcript does not show the dedicated production Skill")
    if not _contains_any(agent_text, ("3mf", "3MF")):
        raise RehearsalError("agent transcript does not show the 3MF endpoint")
    imagegen_turns = [
        index
        for index, turn in enumerate(turns)
        if turn["speaker"] == "agent"
        and _contains_any(turn["text"], ("lens-cap-imagegen", "$lens-cap-imagegen"))
    ]
    production_turns = [
        index
        for index, turn in enumerate(turns)
        if turn["speaker"] == "agent"
        and _contains_any(turn["text"], ("lens-cap-production", "$lens-cap-production"))
    ]
    if not imagegen_turns or not production_turns or min(imagegen_turns) > min(production_turns):
        raise RehearsalError("agent transcript must hand off from imagegen to production in that order")

    production = _mapping(scenario.get("production"), "production")
    endpoint = _nonempty_text(production.get("endpoint"), "production.endpoint")
    if "3mf" not in endpoint.casefold() or "lens-cap-3mf" not in endpoint.casefold():
        raise RehearsalError("production.endpoint must use the repository lens-cap-3mf bridge")

    return {
        "status": "passed",
        "turn_count": len(turns),
        "answer_turn_index": answer_index,
        "route": list(EXPECTED_ROUTE),
        "route_resolution": route_resolution,
        "focal_length_display": focal_display,
        "intake": {
            "asked_once": True,
            "persisted_in_job_toml": True,
            "mating_outside_diameter_mm": diameter,
            "adapter_nominal_ring_mm": nominal,
            "adapter_radial_wall_mm": radial_wall,
            "derived_mating_diameter_mm": (
                nominal + 2.0 * radial_wall if nominal is not None and radial_wall is not None else None
            ),
            "foam_liner_status": foam_status,
            "liner_thickness_mm": thickness,
            "friction_ribs_enabled": ribs,
            "friction_ribs_explicit": explicit,
            "friction_rib_profile": profile,
        },
    }


def validate_interaction(
    scenario: Mapping[str, Any],
    *,
    root: str | Path = ROOT,
    scenario_base: str | Path | None = None,
) -> dict[str, Any]:
    """Validate the transcript and its physical handoff without running CAD."""

    root_path = Path(root).expanduser().resolve()
    scenario_base_path = (
        Path(scenario_base).expanduser().resolve() if scenario_base is not None else None
    )
    if scenario.get("schema_version") != SCHEMA_VERSION:
        raise RehearsalError(f"unsupported scenario schema_version {scenario.get('schema_version')!r}")
    scenario_id = _nonempty_text(scenario.get("scenario_id"), "scenario_id")
    fixture_raw = _mapping(scenario.get("fixture"), "fixture")
    fixture = _resolve_inside(
        root_path,
        fixture_raw.get("path"),
        "fixture.path",
        base_dir=scenario_base_path,
        allow_external=True,
    )
    if not fixture.is_dir():
        raise RehearsalError(f"fixture directory does not exist: {fixture}")
    brief_path = fixture / "design-brief.json"
    if not brief_path.is_file():
        raise RehearsalError(f"fixture design brief is missing: {brief_path}")
    brief = _load_json(brief_path, "design brief")
    conversation_report = _validate_conversation(scenario, brief)

    primary_job = _resolve_inside(fixture, fixture_raw.get("primary_job"), "fixture.primary_job")
    if not primary_job.is_file() or primary_job.suffix.casefold() != ".toml":
        raise RehearsalError(f"fixture.primary_job must be an existing TOML file: {primary_job}")
    job = _load_job(primary_job)
    fit = _mapping(job.get("fit"), "job.fit")
    metadata = _mapping(job.get("metadata", {}), "job.metadata")
    intake = conversation_report["intake"]
    measured = _number(job.get("measured_diameter_mm"), "job.measured_diameter_mm", positive=True)
    if not math.isclose(measured, intake["mating_outside_diameter_mm"], abs_tol=1e-6):
        raise RehearsalError("job measured_diameter_mm does not match the answered mating diameter")
    if intake["adapter_nominal_ring_mm"] is not None:
        job_nominal = _number(
            metadata.get("adapter_nominal_ring_mm"),
            "job.metadata.adapter_nominal_ring_mm",
            positive=True,
        )
        job_radial = _number(
            metadata.get("adapter_radial_wall_mm"),
            "job.metadata.adapter_radial_wall_mm",
        )
        if job_radial < 0:
            raise RehearsalError("job metadata adapter_radial_wall_mm cannot be negative")
        if not math.isclose(job_nominal, intake["adapter_nominal_ring_mm"], abs_tol=1e-6):
            raise RehearsalError("job nominal adapter ring does not match the answered envelope")
        if not math.isclose(job_radial, intake["adapter_radial_wall_mm"], abs_tol=1e-6):
            raise RehearsalError("job radial adapter wall does not match the answered envelope")
        if not math.isclose(job_nominal + 2.0 * job_radial, measured, abs_tol=1e-6):
            raise RehearsalError("job adapter envelope does not derive the measured mating diameter")
    job_foam = str(fit.get("foam_liner_status", "")).strip().casefold()
    if job_foam != intake["foam_liner_status"]:
        raise RehearsalError("job fit.foam_liner_status does not match the persisted intake")
    if job_foam == "foam":
        job_thickness = _number(fit.get("liner_thickness_mm"), "job.fit.liner_thickness_mm", positive=True)
        if not math.isclose(job_thickness, intake["liner_thickness_mm"], abs_tol=1e-6):
            raise RehearsalError("job foam thickness does not match the answered uncompressed thickness")
    if fit.get("friction_ribs_enabled") is not intake["friction_ribs_enabled"]:
        raise RehearsalError("job fit.friction_ribs_enabled does not match the persisted intake")
    job_explicit = fit.get("friction_ribs_explicit", False)
    if not isinstance(job_explicit, bool):
        raise RehearsalError("job fit.friction_ribs_explicit must be boolean")
    if job_explicit is not intake["friction_ribs_explicit"]:
        raise RehearsalError("job fit.friction_ribs_explicit does not match the persisted intake")
    job_profile = str(fit.get("friction_rib_profile", "light_tapered")).strip()
    if job_profile != intake["friction_rib_profile"]:
        raise RehearsalError("job friction_rib_profile does not match the persisted intake")

    return {
        "status": "passed",
        "scenario_id": scenario_id,
        "scenario_sha256": None,
        "workspace": {
            "conversation_history": "none",
            "filesystem": "isolated_temp",
        },
        "fixture": {
            "path": str(fixture),
            "primary_job": str(primary_job),
            "brief": str(brief_path),
            "lens_identity": brief.get("lens_identity"),
        },
        "interaction": conversation_report,
        "persisted_job": {
            "path": str(primary_job),
            "measured_diameter_mm": measured,
            "adapter_nominal_ring_mm": metadata.get("adapter_nominal_ring_mm"),
            "adapter_radial_wall_mm": metadata.get("adapter_radial_wall_mm"),
            "foam_liner_status": job_foam,
            "liner_thickness_mm": fit.get("liner_thickness_mm"),
            "friction_ribs_enabled": fit.get("friction_ribs_enabled"),
            "friction_ribs_explicit": job_explicit,
            "friction_rib_profile": job_profile,
        },
    }


def _parse_report(text: str) -> dict[str, Any]:
    try:
        return smoke._json_from_output(text)
    except Exception as exc:  # pragma: no cover - defensive wrapper for CLI output
        raise RehearsalError(f"production runner did not emit JSON: {text[-800:]!r}") from exc


def run_rehearsal(
    scenario_path: str | Path,
    *,
    bambu: str = "never",
    require_external: bool = False,
    keep_workdir: bool = False,
    workdir: str | Path | None = None,
    artifact_dir: str | Path | None = None,
    force_artifacts: bool = False,
    root: str | Path = ROOT,
) -> dict[str, Any]:
    """Validate one transcript, then run its fixture through clean production."""

    root_path = Path(root).expanduser().resolve()
    scenario_file = Path(scenario_path).expanduser()
    if not scenario_file.is_absolute():
        # Prefer a path that exists from the caller's cwd (which makes a copied
        # external transcript convenient), then fall back to a checkout-
        # relative spelling used by the bundled example.
        cwd_candidate = Path.cwd() / scenario_file
        scenario_file = cwd_candidate if cwd_candidate.exists() else root_path / scenario_file
    # An absolute argument may point to a user-maintained transcript outside
    # the checkout.  That is intentional: this command only reads the
    # scenario/fixture inputs and writes through explicit downstream paths.
    scenario_file = scenario_file.resolve()
    scenario = _load_json(scenario_file, "scenario")
    report = validate_interaction(scenario, root=root_path, scenario_base=scenario_file.parent)
    report["scenario_sha256"] = _sha256(scenario_file)

    fixture_path = Path(report["fixture"]["path"])
    primary_job_path = Path(report["fixture"]["primary_job"])
    primary_relative = primary_job_path.relative_to(fixture_path)
    command = [
        sys.executable,
        str(root_path / "scripts" / "smoke_rehouse.py"),
        "--fixture",
        str(fixture_path),
        "--bambu",
        bambu,
        "--json",
    ]
    if require_external:
        command.append("--require-external")
    if keep_workdir:
        command.append("--keep-workdir")
    if workdir is not None:
        command.extend(("--workdir", str(Path(workdir).expanduser().resolve())))
    if artifact_dir is not None:
        command.extend(("--artifact-dir", str(Path(artifact_dir).expanduser().resolve())))
        if force_artifacts:
            command.append("--force-artifacts")
    try:
        result = subprocess.run(
            command,
            cwd=root_path,
            capture_output=True,
            text=True,
            timeout=2400,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RehearsalError(f"clean-room production runner failed to start: {exc}") from exc
    if result.returncode != 0:
        raise RehearsalError(
            "clean-room production runner failed (rc=%s)\nstdout:\n%s\nstderr:\n%s"
            % (result.returncode, result.stdout[-1600:], result.stderr[-1600:])
        )
    production = _parse_report(result.stdout)
    if production.get("status") != "passed":
        raise RehearsalError(f"clean-room production report is not passed: {production.get('status')!r}")
    # The clean runner intentionally copies jobs into a new temporary fixture,
    # so compare the stable trailing relative path rather than an ephemeral
    # absolute pathname.
    primary_suffix = tuple(primary_relative.parts)
    included_primary = False
    for item in production.get("jobs", []):
        candidate = Path(str(item.get("job", "")))
        if tuple(candidate.parts[-len(primary_suffix) :]) == primary_suffix:
            included_primary = True
            break
    if not included_primary:
        raise RehearsalError("clean-room report does not include the persisted primary job")
    if artifact_dir is not None and not production.get("copied_artifacts"):
        raise RehearsalError("artifact_dir was requested but no 3MF was retained")
    report["production"] = production
    report["status"] = "passed"
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", type=Path, help="structured user/agent rehearsal JSON")
    parser.add_argument("--bambu", choices=("auto", "never", "export", "slice"), default="never")
    parser.add_argument("--require-external", action="store_true")
    parser.add_argument("--keep-workdir", action="store_true")
    parser.add_argument("--workdir", type=Path)
    parser.add_argument("--artifact-dir", type=Path)
    parser.add_argument("--force-artifacts", action="store_true")
    parser.add_argument("--json", action="store_true", help="print the complete report as JSON")
    return parser


def _human_summary(report: Mapping[str, Any]) -> str:
    production = _mapping(report.get("production"), "production report")
    interaction = _mapping(report.get("interaction"), "interaction report")
    intake = _mapping(interaction.get("intake"), "interaction intake")
    native_statuses: list[str] = []
    for job in production.get("jobs", []):
        if not isinstance(job, Mapping):
            continue
        native = job.get("native_3mf")
        native_statuses.append(str(native.get("status", "unknown")) if isinstance(native, Mapping) else "unknown")
    return "\n".join(
        (
            "status: passed",
            f"scenario: {report.get('scenario_id')}",
            f"route: {' -> '.join(interaction.get('route', ())) }",
            "intake: "
            f"{float(intake['mating_outside_diameter_mm']):g} mm, "
            f"foam={intake['foam_liner_status']}, "
            f"ribs={'on' if intake['friction_ribs_enabled'] else 'off'}",
            f"production: {production.get('runner', 'smoke_rehouse.py')} ({production.get('status')})",
            "native 3MF: " + (", ".join(native_statuses) if native_statuses else "unknown"),
            f"fit: {production.get('fit_status', 'unverifiable_until_coupon_measurement')}",
        )
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = run_rehearsal(
            args.scenario,
            bambu=args.bambu,
            require_external=args.require_external,
            keep_workdir=args.keep_workdir,
            workdir=args.workdir,
            artifact_dir=args.artifact_dir,
            force_artifacts=args.force_artifacts,
        )
    except (OSError, RehearsalError, ValueError) as exc:
        print(f"lens-cap rehearse-user-agent: error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str) if args.json else _human_summary(report))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

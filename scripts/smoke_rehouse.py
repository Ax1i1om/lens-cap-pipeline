#!/usr/bin/env python3
"""Run a clean-room, end-to-end lens-cap fixture smoke test.

The normal ``scripts/smoke.py`` intentionally stays tiny and portable.  This
runner is the heavier release rehearsal: it copies only an approved named-lens
fixture into a temporary checkout, invokes the public CLI as a new user would,
audits relief projections, and sends every publishable native/Bambu package
through the same canonical release bridge. When the local installation is
available it also asks Bambu Studio for one multipart profiled snapshot.
No generated output is written back to the source fixture unless the caller
explicitly supplies ``--artifact-dir``.

The test is deliberately an integration rehearsal rather than a claim that a
file check proves a real lens fit.  Mechanical fit remains pending until a
same-material coupon is printed and measured.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Mapping

# Keep direct execution usable before an editable install exists.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lens_cap_pipeline.brief import (  # noqa: E402
    BriefError,
    _reference_roles,
    _review_evidence,
    _stable_id,
    canonical_aperture_display,
    validate_design_brief,
)
from lens_cap_pipeline.config import PipelineConfig, load_config  # noqa: E402
from scripts.build_3mf import _projection_tolerance  # noqa: E402

ADAPTER = ROOT / "tools" / "3mf_adapter" / "three_mf_adapter.py"
PROJECTION = ROOT / "scripts" / "audit_stl_projection.py"
BRIDGE = ROOT / "scripts" / "build_3mf.py"
DEFAULT_FIXTURE = ROOT / "examples" / "fixtures" / "helios-44-2-rehouse-imagegen-v3"
DEFAULT_SIZES = (95, 82, 77)  # fallback for the original Helios fixture


class SmokeError(RuntimeError):
    """Raised when a deterministic smoke gate fails."""


def _smoke_review_evidence(
    value: Any,
    field: str,
    *,
    min_alnum: int,
    min_words: int = 3,
) -> str:
    try:
        return _review_evidence(
            value,
            field,
            min_alnum=min_alnum,
            min_words=min_words,
        )
    except BriefError as exc:
        raise SmokeError(str(exc)) from exc


def _smoke_stable_id(value: Any, field: str) -> str:
    try:
        return _stable_id(value, field)
    except BriefError as exc:
        raise SmokeError(str(exc)) from exc


def _smoke_reference_roles(reference: Mapping[str, Any], prefix: str) -> tuple[str, ...]:
    try:
        return _reference_roles(reference, prefix)
    except BriefError as exc:
        raise SmokeError(str(exc)) from exc


_FOCAL_DISPLAY_RE = re.compile(
    r"^(?P<start>[0-9]+(?:\.[0-9]+)?)(?:mm)?"
    r"(?:-(?P<end>[0-9]+(?:\.[0-9]+)?)(?:mm)?)?$"
)


def _canonical_focal_display(value: Any, *, label: str = "focal_length_display") -> str:
    """Return a strict, unit-free focal display token.

    ``focal_length_mm`` remains the numeric machine anchor.  This optional
    companion accepts a prime token (``50mm``) or a zoom range (``28–70mm``)
    for the human-facing first display token, while rejecting arbitrary text.
    Unicode dashes, optional ``mm`` units, and the words ``to``/``至`` are
    normalized so briefs and natural-language transcripts can use ordinary
    typography without weakening the closed text contract.
    """

    if not isinstance(value, str) or not value.strip():
        raise SmokeError(f"{label} must be non-empty text")
    text = unicodedata.normalize("NFKC", value).casefold().strip()
    text = (
        text.replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("‑", "-")
        .replace("‒", "-")
        .replace("－", "-")
        .replace("~", "-")
        .replace("毫米", "mm")
    )
    text = re.sub(r"\bto\b|至", "-", text)
    text = re.sub(r"\s+", "", text)
    match = _FOCAL_DISPLAY_RE.fullmatch(text)
    if match is None:
        raise SmokeError(
            f"{label} must be a focal token such as '50mm' or a zoom range such as '28–70mm'"
        )
    start = match.group("start")
    end = match.group("end")
    try:
        start_value = float(start)
        end_value = float(end) if end is not None else None
    except (TypeError, ValueError, OverflowError) as exc:  # pragma: no cover - regex already limits input
        raise SmokeError(f"{label} contains an invalid focal number") from exc
    if not math.isfinite(start_value) or start_value <= 0:
        raise SmokeError(f"{label} must use a positive focal length")
    if end_value is not None and (not math.isfinite(end_value) or end_value <= start_value):
        raise SmokeError(f"{label} zoom range must end above its starting focal length")
    return f"{start}-{end}" if end is not None else start


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_from_output(text: str) -> dict[str, Any]:
    """Parse the last JSON object from a tool that may print diagnostics."""

    stripped = text.strip()
    if stripped:
        try:
            value = json.loads(stripped)
            if isinstance(value, dict):
                return value
        except json.JSONDecodeError:
            pass
    decoder = json.JSONDecoder()
    # Walking from the end avoids accidentally selecting a diagnostic object
    # printed before the command's final report.
    for index in range(len(text) - 1, -1, -1):
        if text[index] != "{":
            continue
        try:
            value, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if end == len(text[index:].rstrip()) and isinstance(value, dict):
            return value
        if isinstance(value, dict):
            return value
    raise SmokeError(f"tool did not emit a JSON object: {text[-600:]!r}")


def _run(
    command: Iterable[str],
    *,
    cwd: Path = ROOT,
    timeout: int = 900,
) -> dict[str, Any]:
    argv = [str(item) for item in command]
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SmokeError(f"command timed out after {timeout}s: {argv!r}\n{exc}") from exc
    return {
        "argv": argv,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def _find_executable(*candidates: str) -> str | None:
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return str(Path(found).resolve())
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return str(path.resolve())
    return None


def _find_bambu_profiles() -> dict[str, Path] | None:
    """Find a conservative A1 mini/0.2 mm profile trio on common installs."""

    explicit = {
        "machine": os.environ.get("LENS_CAP_MACHINE_PROFILE"),
        "process": os.environ.get("LENS_CAP_PROCESS_PROFILE"),
        "filament": os.environ.get("LENS_CAP_FILAMENT_PROFILE"),
    }
    if all(explicit.values()):
        paths = {key: Path(value).expanduser() for key, value in explicit.items() if value}
        if all(path.is_file() for path in paths.values()):
            return paths

    roots = [
        Path("/Applications/BambuStudio.app/Contents/Resources/profiles/BBL"),
        Path("/Applications/BambuStudio.app/Contents/Resources/profiles"),
    ]
    for root in roots:
        machine_dir = root / "machine"
        if not machine_dir.is_dir():
            continue
        def preferred(pattern: str) -> Path | None:
            matches = sorted(root.glob(pattern))
            # Bambu ships similarly named A1/A1M profiles.  Prefer the A1M
            # variant because the machine profile below is the A1 mini; a
            # lexicographically first profile can otherwise be incompatible.
            matches.sort(key=lambda path: ("A1M" not in path.name, path.name))
            return matches[0] if matches else None

        machine = preferred("machine/*A1 mini*0.2 nozzle*.json")
        process = preferred("process/*0.10mm Standard*0.2 nozzle*.json")
        filament = preferred("filament/*Bambu PLA Basic*0.2 nozzle*.json")
        if machine and process and filament:
            return {"machine": machine, "process": process, "filament": filament}
    return None


def _explicit_bambu_profiles(
    machine: Path | None,
    process: Path | None,
    filament: Path | None,
) -> dict[str, Path] | None:
    """Resolve an all-or-nothing profile trio supplied by a portable caller."""

    raw = {"machine": machine, "process": process, "filament": filament}
    provided = {name: value for name, value in raw.items() if value is not None}
    if not provided:
        return None
    if len(provided) != len(raw):
        missing = ", ".join(sorted(set(raw) - set(provided)))
        raise SmokeError(
            "explicit Bambu profiles are all-or-nothing; missing: " + missing
        )
    resolved = {
        name: value.expanduser().resolve()
        for name, value in raw.items()
        if value is not None
    }
    missing_files = [str(path) for path in resolved.values() if not path.is_file()]
    if missing_files:
        raise SmokeError(
            "explicit Bambu profile does not exist: " + ", ".join(missing_files)
        )
    return resolved


def _copy_fixture(source: Path, destination: Path) -> list[Path]:
    """Copy only source inputs, never old generated outputs or artifacts."""

    required = (source / "design-brief.json", source / "prompt.txt", source / "art" / "master.png")
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise SmokeError("fixture is missing required source inputs: " + ", ".join(missing))
    (destination / "art").mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / "design-brief.json", destination / "design-brief.json")
    shutil.copy2(source / "prompt.txt", destination / "prompt.txt")
    shutil.copy2(source / "art" / "master.png", destination / "art" / "master.png")
    source_jobs = sorted(source.glob("jobs/*/job.toml"))
    if not source_jobs:
        # Preserve a useful error for a malformed fixture instead of silently
        # producing a report with no mechanical scenarios.
        expected = ", ".join(f"jobs/{size}mm/job.toml" for size in DEFAULT_SIZES)
        raise SmokeError(f"fixture has no jobs/*/job.toml files (expected e.g. {expected})")
    copied_jobs: list[Path] = []
    for source_job in source_jobs:
        relative_job = source_job.relative_to(source)
        target_job = destination / relative_job
        target_job.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_job, target_job)
        copied_jobs.append(relative_job)
    return copied_jobs


def _validate_brief(fixture: Path) -> dict[str, Any]:
    fixture = fixture.expanduser().resolve()
    brief_path = fixture / "design-brief.json"
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    if not isinstance(brief, dict):
        raise SmokeError("design-brief.json must contain an object")
    if type(brief.get("schema_version")) is not int or brief["schema_version"] != 2:
        raise SmokeError("fixture design brief schema_version must be the integer 2")
    if brief.get("production_target") != "printable_front":
        raise SmokeError("fixture production_target must be printable_front")
    identity = brief.get("lens_identity")
    if not isinstance(identity, dict):
        raise SmokeError("fixture must declare a lens_identity object")
    focal = identity.get("focal_length_mm")
    if isinstance(focal, bool):
        raise SmokeError("fixture focal_length_mm must be a positive number")
    try:
        focal_value = float(focal)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SmokeError("fixture focal_length_mm must be a positive number") from exc
    if not math.isfinite(focal_value) or focal_value <= 0:
        raise SmokeError("fixture focal_length_mm must be a positive number")
    aperture = str(identity.get("maximum_aperture", "")).strip()
    if not aperture:
        raise SmokeError("fixture must declare maximum_aperture")
    hierarchy = brief.get("visual_direction", {}).get("dominant_hierarchy")
    if hierarchy != ["focal_length", "maximum_aperture"]:
        raise SmokeError("fixture artwork hierarchy is not focal length then maximum aperture")
    display = brief.get("display_text")
    allowed = brief.get("allowed_text")
    if (
        not isinstance(display, list)
        or len(display) < 2
        or not all(isinstance(token, str) and token.strip() for token in display)
        or not isinstance(allowed, list)
        or display != allowed
    ):
        raise SmokeError("display_text and allowed_text must be the same closed set")
    generation = brief.get("generation")
    if not isinstance(generation, Mapping):
        raise SmokeError("fixture must declare a generation handoff object")
    if generation.get("approved") is not True:
        raise SmokeError("approved artwork handoff must set generation.approved=true")
    _smoke_review_evidence(
        generation.get("approval_note"),
        "generation.approval_note",
        min_alnum=20,
    )
    design_review = brief.get("design_review")
    if not isinstance(design_review, Mapping):
        raise SmokeError("fixture must declare a design_review object")
    reviewed_candidate_hash = design_review.get("reviewed_candidate_sha256")
    if not isinstance(reviewed_candidate_hash, str) or not re.fullmatch(
        r"[0-9a-fA-F]{64}", reviewed_candidate_hash
    ):
        raise SmokeError(
            "design_review.reviewed_candidate_sha256 must bind the reviewed raster"
        )
    for field in (
        "full_resolution_reviewed",
        "text_off_anchor_recognizable",
        "identity_swap_requires_redesign",
        "anchor_drives_primary_composition",
        "composition_resolved",
        "visual_grammar_consistent",
        "finish_target_met",
        "production_reduction_preserves_authorship",
    ):
        if design_review.get(field) is not True:
            raise SmokeError(f"design_review.{field} must be true")
    for field, minimum in (
        ("structural_thesis", 20),
        ("finish_target_note", 20),
        ("reviewer_note", 32),
    ):
        _smoke_review_evidence(
            design_review.get(field),
            f"design_review.{field}",
            min_alnum=minimum,
        )
    generation_hash = generation.get("candidate_sha256")
    if not isinstance(generation_hash, str) or (
        reviewed_candidate_hash.lower() != generation_hash.lower()
    ):
        raise SmokeError(
            "design_review.reviewed_candidate_sha256 must match generation.candidate_sha256"
        )
    anchors = brief.get("anchors")
    if not isinstance(anchors, list) or not anchors:
        raise SmokeError("fixture must declare at least one sourced design anchor")
    sourced_anchors = []
    culture_anchors = []
    anchor_commitments = []
    anchor_ids = []
    for index, raw_anchor in enumerate(anchors):
        if not isinstance(raw_anchor, Mapping):
            raise SmokeError(f"anchors[{index}] must be an object")
        anchor_id = _smoke_stable_id(
            raw_anchor.get("anchor_id"), f"anchors[{index}].anchor_id"
        )
        if anchor_id in anchor_ids:
            raise SmokeError(f"duplicate anchor_id: {anchor_id}")
        anchor_ids.append(anchor_id)
        source = raw_anchor.get("source")
        evidence = raw_anchor.get("evidence_state")
        render_role = raw_anchor.get("render_role")
        if not all(isinstance(value, str) and value.strip() for value in (source, evidence, render_role)):
            raise SmokeError(f"anchors[{index}] must include source, evidence_state, and render_role")
        sourced_anchors.append(str(source).strip())
        anchor_commitments.append(str(raw_anchor.get("motif_commitment", "")).casefold())
        claim_kind = str(raw_anchor.get("claim_kind", "")).casefold()
        if any(term in claim_kind for term in ("culture", "manufacturer", "rehouse", "cinema", "history", "craft", "system")):
            culture_anchors.append(str(source).strip())
    if not culture_anchors:
        raise SmokeError("fixture needs a source-backed manufacturer/culture or qualified rehouse anchor")
    hero_anchor_index = design_review.get("hero_anchor_index")
    if type(hero_anchor_index) is not int or not 0 <= hero_anchor_index < len(anchors):
        raise SmokeError("design_review.hero_anchor_index must select an existing anchor")
    if anchor_commitments[hero_anchor_index].strip() != "structural":
        raise SmokeError(
            "design_review.hero_anchor_index must select a structural anchor"
        )
    hero_anchor_id = _smoke_stable_id(
        design_review.get("hero_anchor_id"), "design_review.hero_anchor_id"
    )
    if hero_anchor_id != anchor_ids[hero_anchor_index]:
        raise SmokeError(
            "design_review.hero_anchor_id must match the anchor selected by "
            "hero_anchor_index"
        )
    consequences = design_review.get("anchor_system_consequences")
    consequence_systems = set()
    allowed_systems = {
        "typography_or_counterform",
        "field_path_or_divide",
        "container_or_perimeter",
    }
    if not isinstance(consequences, list) or len(consequences) < 2:
        raise SmokeError(
            "design_review.anchor_system_consequences must contain at least two entries"
        )
    for index, consequence in enumerate(consequences):
        if not isinstance(consequence, Mapping):
            raise SmokeError(
                f"design_review.anchor_system_consequences[{index}] must be an object"
            )
        consequence_anchor_id = _smoke_stable_id(
            consequence.get("anchor_id"),
            f"design_review.anchor_system_consequences[{index}].anchor_id",
        )
        if consequence_anchor_id != hero_anchor_id:
            raise SmokeError(
                f"design_review.anchor_system_consequences[{index}].anchor_id "
                "must equal design_review.hero_anchor_id"
            )
        system = consequence.get("system")
        effect = consequence.get("effect")
        if system not in allowed_systems or system in consequence_systems:
            raise SmokeError(
                "design_review.anchor_system_consequences must use distinct canonical systems"
            )
        _smoke_review_evidence(
            effect,
            f"design_review.anchor_system_consequences[{index}].effect",
            min_alnum=12,
        )
        consequence_systems.add(system)

    reference_hashes = generation.get("reference_hashes")
    if not isinstance(reference_hashes, list) or not all(
        isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value)
        for value in reference_hashes
    ):
        raise SmokeError("generation.reference_hashes must be SHA-256 digests")
    reference_hash_set = {value.casefold() for value in reference_hashes}
    approved_references = brief.get("approved_references")
    if not isinstance(approved_references, list):
        raise SmokeError("approved_references must be a list")
    reference_ids = []
    quality_ids = []
    for index, reference in enumerate(approved_references):
        if not isinstance(reference, Mapping):
            raise SmokeError(f"approved_references[{index}] must be an object")
        prefix = f"approved_references[{index}]"
        roles = _smoke_reference_roles(reference, prefix)
        reference_id = _smoke_stable_id(reference.get("id"), f"{prefix}.id")
        if reference_id in reference_ids:
            raise SmokeError(f"duplicate approved-reference id: {reference_id}")
        reference_ids.append(reference_id)
        path_or_url = reference.get("path_or_url")
        if not isinstance(path_or_url, str) or not path_or_url.strip():
            raise SmokeError(f"{prefix}.path_or_url must be recorded")
        if "quality_reference" not in roles:
            continue
        snapshot_value = reference.get("snapshot_path")
        snapshot_hash = reference.get("snapshot_sha256")
        traits = reference.get("transferable_traits")
        quality_ids.append(reference_id)
        if not isinstance(snapshot_value, str) or not snapshot_value.strip():
            raise SmokeError(
                f"approved_references[{index}].snapshot_path must be recorded"
            )
        snapshot = (fixture / snapshot_value).resolve()
        if fixture not in snapshot.parents or not snapshot.is_file():
            raise SmokeError(
                f"approved_references[{index}].snapshot_path must stay inside the fixture"
            )
        if not isinstance(snapshot_hash, str) or not re.fullmatch(
            r"[0-9a-fA-F]{64}", snapshot_hash
        ):
            raise SmokeError(
                f"approved_references[{index}].snapshot_sha256 must be a digest"
            )
        if _sha256(snapshot).casefold() != snapshot_hash.casefold():
            raise SmokeError(
                f"approved_references[{index}].snapshot_sha256 does not match"
            )
        if snapshot_hash.casefold() not in reference_hash_set:
            raise SmokeError(
                f"approved_references[{index}].snapshot_sha256 must be a generation reference hash"
            )
        if not isinstance(traits, list) or len(traits) < 2:
            raise SmokeError(
                f"approved_references[{index}].transferable_traits must name two traits"
            )
        for trait_index, trait in enumerate(traits):
            _smoke_review_evidence(
                trait,
                f"approved_references[{index}].transferable_traits[{trait_index}]",
                min_alnum=8,
                min_words=2,
            )
    quality_checks = design_review.get("quality_reference_checks")
    if not isinstance(quality_checks, list):
        raise SmokeError("design_review.quality_reference_checks must be a list")
    checked_quality_ids = []
    for index, check in enumerate(quality_checks):
        if not isinstance(check, Mapping):
            raise SmokeError(
                f"design_review.quality_reference_checks[{index}] must be an object"
            )
        reference_id = _smoke_stable_id(
            check.get("reference_id"),
            f"design_review.quality_reference_checks[{index}].reference_id",
        )
        note = check.get("comparison_note")
        if (
            reference_id in checked_quality_ids
            or check.get("met") is not True
        ):
            raise SmokeError(
                f"design_review.quality_reference_checks[{index}] is incomplete"
            )
        _smoke_review_evidence(
            note,
            f"design_review.quality_reference_checks[{index}].comparison_note",
            min_alnum=20,
        )
        checked_quality_ids.append(reference_id)
    if set(checked_quality_ids) != set(quality_ids) or len(checked_quality_ids) != len(
        quality_ids
    ):
        raise SmokeError(
            "design_review.quality_reference_checks must cover every quality_reference"
        )
    provenance = brief.get("provenance")
    if not isinstance(provenance, Mapping):
        raise SmokeError("fixture must declare provenance/licence notes")
    for field in ("artwork_license", "film_or_history_permissions"):
        if not isinstance(provenance.get(field), str) or not provenance[field].strip():
            raise SmokeError(f"provenance.{field} must be recorded")

    focal_display_value = identity.get("focal_length_display")
    if focal_display_value is None:
        focal_display = format(focal_value, "g")
    else:
        # Keep the reviewed spelling (including an en dash or ``mm``) for the
        # provenance report; compare canonical forms only for validation.
        _canonical_focal_display(focal_display_value)
        focal_display = focal_display_value.strip()
    focal_display_canonical = _canonical_focal_display(focal_display, label="focal display")
    focal_display_start = float(focal_display_canonical.split("-", 1)[0])
    if not math.isclose(focal_display_start, focal_value, abs_tol=1e-9):
        raise SmokeError("focal_length_display must start at focal_length_mm")
    displayed_focal = _canonical_focal_display(
        display[0], label="display_text[0]"
    )
    if displayed_focal != focal_display_canonical:
        raise SmokeError("the first display_text token must be the focal length")
    try:
        aperture_anchor = canonical_aperture_display(
            aperture, field="lens_identity.maximum_aperture"
        )
        aperture_display_value = identity.get("maximum_aperture_display")
        aperture_display = canonical_aperture_display(
            aperture if aperture_display_value is None else aperture_display_value,
            field="lens_identity.maximum_aperture_display",
        )
        displayed_aperture = canonical_aperture_display(
            display[1], field="display_text[1]"
        )
    except BriefError as exc:
        raise SmokeError(str(exc)) from exc
    if aperture_display.split("-", 1)[0] != aperture_anchor.split("-", 1)[0]:
        raise SmokeError("maximum_aperture_display must start at maximum_aperture")
    if displayed_aperture != aperture_display:
        raise SmokeError(
            "the second display_text token must be the complete maximum aperture display"
        )
    prompt_value = generation.get("prompt")
    if not isinstance(prompt_value, str) or not prompt_value.strip():
        raise SmokeError("generation.prompt must point to the committed prompt record")
    prompt_path = (fixture / prompt_value).resolve()
    if not prompt_path.is_file() or fixture.resolve() not in prompt_path.parents:
        raise SmokeError("generation.prompt must stay inside the fixture")
    prompt_hash = generation.get("prompt_sha256")
    if not isinstance(prompt_hash, str) or prompt_hash.lower() != _sha256(prompt_path).lower():
        raise SmokeError("generation.prompt_sha256 does not match prompt.txt")
    prompt = prompt_path.read_text(encoding="utf-8")
    required_tokens = generation.get("required_prompt_tokens", display[:2])
    if not isinstance(required_tokens, list) or not all(isinstance(token, str) for token in required_tokens):
        raise SmokeError("generation.required_prompt_tokens must be a list of strings")
    for token in required_tokens:
        if token not in prompt:
            raise SmokeError(f"prompt record is missing exact token {token!r}")
    candidate_value = generation.get("candidate_path")
    if not isinstance(candidate_value, str) or not candidate_value.strip():
        raise SmokeError("generation.candidate_path must name the approved raster")
    art_path = (fixture / candidate_value).resolve()
    if fixture.resolve() not in art_path.parents or not art_path.is_file():
        raise SmokeError(f"approved artwork is missing: {art_path}")
    art_hash = generation.get("candidate_sha256")
    if not isinstance(art_hash, str) or _sha256(art_path).lower() != art_hash.lower():
        raise SmokeError("approved artwork hash does not match design brief")
    return {
        "status": "passed",
        "identity": identity,
        "focal_length_mm": focal_value,
        "focal_length_display": focal_display,
        "maximum_aperture": aperture,
        "display_text": display,
        "prompt": Path(os.path.relpath(prompt_path, fixture)).as_posix(),
        "prompt_sha256": _sha256(prompt_path),
        "artwork": Path(os.path.relpath(art_path, fixture)).as_posix(),
        "artwork_sha256": _sha256(art_path),
        "anchor_count": len(sourced_anchors),
        "culture_anchor_count": len(culture_anchors),
        "hero_anchor_index": hero_anchor_index,
        "hero_anchor_id": hero_anchor_id,
        "quality_reference_count": len(quality_ids),
        "completion_quality_checked": True,
    }


def _build_job(
    job_path: Path,
    *,
    require_external: bool,
    export_openscad: bool,
) -> tuple[PipelineConfig, dict[str, Any]]:
    config = load_config(job_path)
    # Validate every size variant against the same release-grade semantic
    # handoff.  The selected ``--bridge-job`` exercises this gate again through
    # the public endpoint, while this direct call prevents non-selected matrix
    # jobs from passing on the smoke runner's looser fixture checks alone.
    design_brief = validate_design_brief(config)
    command = [
        sys.executable,
        "-m",
        "lens_cap_pipeline.cli",
        "build",
        str(job_path),
        "--force",
        "--bambu-handoff",
        "--external",
        "--json",
    ]
    if export_openscad:
        command.insert(command.index("--bambu-handoff"), "--export-openscad")
    if require_external:
        command.append("--strict-external")
    run = _run(command, timeout=1200)
    if run["returncode"] not in {0}:
        raise SmokeError(
            f"CLI build failed for {job_path} (rc={run['returncode']})\n"
            f"stdout:\n{run['stdout'][-1200:]}\nstderr:\n{run['stderr'][-1200:]}"
        )
    payload = _json_from_output(run["stdout"])
    if payload.get("status") not in {"passed", "unverifiable"}:
        raise SmokeError(f"unexpected build status for {job_path}: {payload.get('status')!r}")
    process_report = config.output_dir / "process-report.json"
    geometry_report = config.output_dir / "model" / "geometry-report.json"
    if not process_report.is_file() or not geometry_report.is_file():
        raise SmokeError(f"build did not write process/model reports for {job_path}")
    process_data = json.loads(process_report.read_text(encoding="utf-8"))
    geometry_data = json.loads(geometry_report.read_text(encoding="utf-8"))
    if process_data.get("status") != "passed" or geometry_data.get("status") != "passed":
        raise SmokeError(f"deterministic report failed for {job_path}")
    mechanical = geometry_data.get("mechanical")
    expected = float(config.measured_diameter_mm or config.face_diameter_mm)
    if not isinstance(mechanical, dict) or float(mechanical.get("measured_diameter_mm", -1)) != expected:
        raise SmokeError(f"measured diameter did not survive build for {job_path}")
    # A fixture may describe a step-up/adapter envelope as a nominal ring plus
    # a radial wall.  Check the arithmetic explicitly so a clean-room run
    # exercises the same measurement rule users need for real hardware.
    nominal = config.metadata.get("adapter_nominal_ring_mm") if isinstance(config.metadata, dict) else None
    radial_wall = config.metadata.get("adapter_radial_wall_mm") if isinstance(config.metadata, dict) else None
    measurement_note: dict[str, float] | None = None
    if nominal is not None or radial_wall is not None:
        if nominal is None or radial_wall is None:
            raise SmokeError(f"adapter envelope on {job_path} must declare both nominal ring and radial wall")
        try:
            nominal_value = float(nominal)
            radial_wall_value = float(radial_wall)
        except (TypeError, ValueError, OverflowError) as exc:
            raise SmokeError(f"adapter envelope on {job_path} contains non-numeric dimensions") from exc
        if not math.isfinite(nominal_value) or not math.isfinite(radial_wall_value) or nominal_value <= 0 or radial_wall_value < 0:
            raise SmokeError(f"adapter envelope on {job_path} contains invalid dimensions")
        derived = nominal_value + 2.0 * radial_wall_value
        if abs(derived - expected) > 1e-6:
            raise SmokeError(
                f"measured diameter {expected:g} does not equal nominal ring + 2*radial wall "
                f"({nominal_value:g} + 2*{radial_wall_value:g} = {derived:g}) for {job_path}"
            )
        measurement_note = {
            "nominal_ring_mm": nominal_value,
            "radial_adapter_wall_mm": radial_wall_value,
            "derived_mating_diameter_mm": derived,
        }
    if mechanical.get("friction_ribs_enabled") is not True:
        raise SmokeError(f"fixture default ribs were not enabled for {job_path}")
    if mechanical.get("friction_ribs_explicit") is not False:
        raise SmokeError(f"fixture default ribs were incorrectly marked explicit for {job_path}")
    scad = config.output_dir / "model" / f"{config.job_slug}.scad"
    assembly = config.output_dir / "model" / "mesh" / f"{config.job_slug}-assembly.stl"
    if not scad.is_file():
        raise SmokeError(f"build did not write a usable SCAD for {job_path}")
    if export_openscad and (not assembly.is_file() or assembly.stat().st_size <= 84):
        raise SmokeError(f"OpenSCAD was requested but no usable assembly STL was written for {job_path}")
    return config, {
        "status": "passed",
        "build_status": payload.get("status"),
        "design_brief": design_brief,
        "job": str(job_path),
        "measured_diameter_mm": expected,
        "adapter_envelope": measurement_note,
        "friction_ribs": {
            "enabled": mechanical["friction_ribs_enabled"],
            "explicit": mechanical["friction_ribs_explicit"],
            "profile": mechanical.get("friction_rib_profile"),
        },
        "process_report": str(process_report),
        "geometry_report": str(geometry_report),
        "scad": str(scad),
        "assembly_stl": str(assembly) if assembly.is_file() else None,
        "cli": {"returncode": run["returncode"], "stderr_tail": run["stderr"][-600:]},
    }


def _projection_audit(config: PipelineConfig) -> dict[str, Any]:
    mesh_dir = config.output_dir / "model" / "mesh"
    mask_dir = config.output_dir / "masks"
    names: list[str] = []
    tolerance = _projection_tolerance(config)
    command = [sys.executable, str(PROJECTION)]
    for palette in config.relief:
        mesh = mesh_dir / f"{config.job_slug}-{palette.name}_relief.stl"
        mask = mask_dir / f"{palette.name}.png"
        if not mesh.is_file() or not mask.is_file():
            continue
        names.append(palette.name)
        command.extend(("--mesh", f"{palette.name}={mesh}", "--expected-mask", f"{palette.name}={mask}"))
    if not names:
        return {
            "status": "unverifiable",
            "reason": "relief STL exports are unavailable (install OpenSCAD for this optional gate)",
        }
    report_path = config.output_dir / "model" / "projection-report.json"
    diff_dir = config.output_dir / "model" / "projection-diff"
    command.extend(
        (
            "--canvas-size-mm",
            str(config.face_diameter_mm),
            "--tolerance-pixels",
            str(tolerance["tolerance_pixels"]),
            "--output-report",
            str(report_path),
            "--output-dir",
            str(diff_dir),
        )
    )
    run = _run(command, timeout=1200)
    if run["returncode"] != 0:
        raise SmokeError(
            f"projection audit failed for {config.job_slug}\n{run['stdout'][-1200:]}\n{run['stderr'][-1200:]}"
        )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("status") != "passed":
        raise SmokeError(f"projection report is not passed for {config.job_slug}")
    return {
        "status": "passed",
        "report": str(report_path),
        "tolerance": tolerance,
        "colors": {
            name: {
                "raw_iou": report["colors"][name]["raw_iou"],
                "missing_outside_tolerance": report["colors"][name]["expected_pixels_outside_tolerance"],
                "extra_outside_tolerance": report["colors"][name]["projected_pixels_outside_tolerance"],
            }
            for name in sorted(names)
        },
    }


def _adapter_verify(
    path: Path,
    *,
    require_slice: bool = False,
    require_closed: bool = False,
    require_single_volume: bool = False,
) -> dict[str, Any]:
    command = [sys.executable, str(ADAPTER), "verify", str(path)]
    if require_slice:
        command.append("--require-slice")
    if require_closed:
        command.append("--require-closed")
    if require_single_volume:
        command.append("--require-single-volume")
    run = _run(command, timeout=120)
    if run["returncode"] != 0:
        raise SmokeError(f"3MF verification failed for {path}: {run['stderr'][-1000:]}")
    return _json_from_output(run["stdout"])


def _required_passed_audit(payload: Mapping[str, Any], field: str) -> dict[str, Any]:
    audit = payload.get(field)
    if not isinstance(audit, Mapping) or audit.get("status") != "passed":
        raise SmokeError(f"canonical bridge did not report a passing {field} gate")
    return dict(audit)


def _profile_manifest_audit(manifest_path: Path) -> dict[str, Any]:
    """Require reproducible profile inputs and their effective-project audit.

    ``build_3mf.py`` is the only producer used by this runner.  The adapter
    sidecar is nevertheless the authoritative location for the complete
    profile inheritance chain and the comparison against settings embedded in
    the Bambu project, so retain both in the smoke report.
    """

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SmokeError(f"cannot read canonical Bambu manifest: {manifest_path}") from exc
    if not isinstance(manifest, Mapping):
        raise SmokeError("canonical Bambu manifest must contain an object")
    if manifest.get("adapter") != "bambu-studio-3mf" or manifest.get("multipart") is not True:
        raise SmokeError("canonical Bambu manifest is not a multipart Bambu project")
    profiles = manifest.get("profiles")
    resolution = manifest.get("profile_resolution")
    expected = {"machine", "process", "filament"}
    if not isinstance(profiles, Mapping) or set(profiles) != expected:
        raise SmokeError("canonical Bambu manifest lacks the complete profile input set")
    if not isinstance(resolution, Mapping) or set(resolution) != expected:
        raise SmokeError("canonical Bambu manifest lacks the complete profile inheritance audit")
    for label in sorted(expected):
        entry = profiles[label]
        resolved = resolution[label]
        if not isinstance(entry, Mapping) or not isinstance(resolved, Mapping):
            raise SmokeError(f"canonical Bambu {label} profile evidence is malformed")
        digest = entry.get("sha256")
        post_digest = entry.get("post_run_sha256")
        if (
            not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest.casefold()) is None
            or post_digest != digest
        ):
            raise SmokeError(f"canonical Bambu {label} profile hash/TOCTOU audit failed")
        chain = resolved.get("chain")
        if not isinstance(chain, list) or not chain:
            raise SmokeError(f"canonical Bambu {label} profile inheritance chain is empty")
        if any(
            not isinstance(item, Mapping)
            or not isinstance(item.get("sha256"), str)
            or re.fullmatch(r"[0-9a-f]{64}", str(item["sha256"]).casefold()) is None
            for item in chain
        ):
            raise SmokeError(f"canonical Bambu {label} profile inheritance hashes are malformed")
    effective = manifest.get("effective_profile_audit")
    if not isinstance(effective, Mapping) or effective.get("status") != "passed":
        raise SmokeError("canonical Bambu effective profile audit did not pass")
    checked = effective.get("checked")
    if not isinstance(checked, Mapping) or not expected.issubset(checked):
        raise SmokeError("canonical Bambu effective profile audit is incomplete")
    return {
        "status": "passed",
        "inputs": {key: dict(profiles[key]) for key in sorted(expected)},
        "resolution": {key: dict(resolution[key]) for key in sorted(expected)},
        "effective": dict(effective),
    }


def _canonical_bridge(
    config: PipelineConfig,
    output_dir: Path,
    *,
    openscad: str | None,
    require_external: bool,
    bambu_mode: str = "never",
    bambu: str | None = None,
    profiles: Mapping[str, Path] | None = None,
) -> dict[str, Any]:
    """Run and audit the sole publishable native/Bambu release endpoint."""

    if bambu_mode not in {"never", "export", "slice"}:
        raise SmokeError(f"unsupported canonical Bambu mode: {bambu_mode!r}")

    native_output = output_dir / f"{config.job_slug}-native.3mf"
    release_report = output_dir / f"{config.job_slug}-3mf-release.json"
    bambu_output = output_dir / f"{config.job_slug}-bambu-{bambu_mode}.3mf"
    command = [
        sys.executable,
        str(BRIDGE),
        str(config.config_path),
        "--force",
        "--bambu",
        bambu_mode,
        "--native-output",
        str(native_output),
        "--report",
        str(release_report),
        "--json",
    ]
    if openscad:
        command.extend(("--openscad", openscad))
    if bambu_mode != "never":
        command.extend(("--slice-output", str(bambu_output)))
        if bambu:
            command.extend(("--bambu-path", bambu))
        if profiles:
            profile_flags = {
                "machine": "--machine-profile",
                "process": "--process-profile",
                "filament": "--filament-profile",
            }
            for label, flag in profile_flags.items():
                value = profiles.get(label)
                if value is not None:
                    command.extend((flag, str(value)))
    run = _run(command, timeout=2100)
    try:
        payload = _json_from_output(run["stdout"])
    except SmokeError:
        payload = {
            "status": "failed",
            "reason": (run["stderr"] or run["stdout"])[-1200:],
        }
    if run["returncode"] != 0:
        status = payload.get("status")
        reason = str(payload.get("reason", ""))
        if status == "unverifiable" and not require_external:
            return {
                "status": "unverifiable",
                "runner": "scripts/build_3mf.py",
                "mode": bambu_mode,
                "failure_class": payload.get("failure_class"),
                "reason": reason,
                "returncode": run["returncode"],
            }
        if status == "failed" and not require_external:
            return {
                "status": "failed",
                "runner": "scripts/build_3mf.py",
                "mode": bambu_mode,
                "failure_class": payload.get("failure_class"),
                "reason": reason,
                "returncode": run["returncode"],
            }
        raise SmokeError(
            "canonical lens-cap-3mf bridge failed for "
            f"{config.job_slug} (rc={run['returncode']})\n"
            f"stdout:\n{run['stdout'][-1600:]}\nstderr:\n{run['stderr'][-1600:]}"
        )
    if payload.get("status") != "passed":
        raise SmokeError(
            f"canonical lens-cap-3mf bridge returned {payload.get('status')!r} "
            f"for {config.job_slug}"
        )
    if not native_output.is_file() or native_output.stat().st_size <= 0:
        raise SmokeError("canonical bridge reported PASS but its native 3MF is missing")
    verified = _adapter_verify(
        native_output,
        require_closed=True,
        require_single_volume=True,
    )
    if not release_report.is_file():
        raise SmokeError("canonical bridge did not retain its 3MF release report")
    try:
        retained_payload = json.loads(release_report.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SmokeError("canonical bridge retained an unreadable release report") from exc
    if retained_payload != payload:
        raise SmokeError("canonical bridge stdout and retained release report disagree")
    design_brief = payload.get("design_brief")
    if not isinstance(design_brief, Mapping) or design_brief.get("status") != "passed":
        raise SmokeError("canonical bridge did not report a passing design-brief gate")
    native = payload.get("native_3mf")
    if not isinstance(native, Mapping):
        raise SmokeError("canonical bridge did not report its native 3MF")
    if native.get("sha256") != verified.get("sha256") or native.get("bytes") != verified.get("bytes"):
        raise SmokeError("canonical native 3MF does not match its audited release evidence")
    release_audits = {
        field: _required_passed_audit(payload, field)
        for field in (
            "source_binding_audit",
            "projection",
            "native_bounds_audit",
            "material_assignment_audit",
        )
    }
    rib_audit = payload.get("friction_rib_mesh_audit")
    if not isinstance(rib_audit, Mapping) or rib_audit.get("status") not in {
        "passed",
        "not_required",
    }:
        raise SmokeError("canonical bridge did not pass its friction-rib mesh gate")
    result: dict[str, Any] = {
        "status": "passed",
        "runner": "scripts/build_3mf.py",
        "mode": bambu_mode,
        "job": str(config.config_path),
        "native_3mf": {
            "status": "passed",
            "mode": "canonical-one-piece",
            "path": str(native_output),
            "manifest": f"{native_output}.manifest.json",
            "sha256": verified.get("sha256"),
            "bytes": verified.get("bytes"),
            "model": verified.get("model"),
            "has_embedded_gcode": verified.get("has_embedded_gcode"),
        },
        "bambu_3mf": {"status": "not_requested", "mode": "never"},
        "release_report": str(release_report),
        "design_brief": dict(design_brief),
        "release_audits": release_audits,
        "friction_rib_mesh_audit": dict(rib_audit),
        "retention_status": payload.get("retention_status"),
        "retention_warning": payload.get("retention_warning"),
        "returncode": run["returncode"],
    }
    if bambu_mode != "never":
        bambu_payload = payload.get("bambu_3mf")
        if not isinstance(bambu_payload, Mapping) or bambu_payload.get("mode") != bambu_mode:
            raise SmokeError("canonical bridge did not report the requested Bambu stage")
        if not bambu_output.is_file() or bambu_output.stat().st_size <= 0:
            raise SmokeError("canonical bridge reported PASS but its Bambu 3MF is missing")
        bambu_verified = _adapter_verify(
            bambu_output,
            require_slice=bambu_mode == "slice",
        )
        if (
            bambu_payload.get("sha256") != bambu_verified.get("sha256")
            or bambu_payload.get("bytes") != bambu_verified.get("bytes")
        ):
            raise SmokeError("canonical Bambu 3MF does not match its audited release evidence")
        multipart = bambu_payload.get("multipart_audit")
        mesh = bambu_payload.get("mesh_audit")
        if not isinstance(multipart, Mapping) or multipart.get("status") != "passed":
            raise SmokeError("canonical Bambu multipart audit did not pass")
        if not isinstance(mesh, Mapping) or mesh.get("status") != "passed":
            raise SmokeError("canonical Bambu mesh audit did not pass")
        manifest_path = Path(f"{bambu_output}.manifest.json")
        profile_audit = _profile_manifest_audit(manifest_path)
        if profiles is None or set(profiles) != {"machine", "process", "filament"}:
            raise SmokeError("canonical Bambu run lacks the requested profile trio")
        profile_inputs = bambu_payload.get("profile_inputs")
        if not isinstance(profile_inputs, Mapping) or set(profile_inputs) != {
            "machine",
            "process",
            "filament",
        }:
            raise SmokeError("canonical Bambu release lacks complete profile inputs")
        for label, evidence in profile_audit["inputs"].items():
            release_entry = profile_inputs.get(label)
            if not isinstance(release_entry, Mapping) or release_entry.get("sha256") != evidence.get("sha256"):
                raise SmokeError(
                    f"canonical Bambu {label} profile evidence disagrees between release and manifest"
                )
            profile_path = Path(profiles[label]).expanduser().resolve()
            if not profile_path.is_file() or evidence.get("sha256") != _sha256(profile_path):
                raise SmokeError(
                    f"canonical Bambu {label} profile evidence does not bind the requested file"
                )
        result["bambu_3mf"] = {
            "status": "passed",
            "mode": bambu_mode,
            "path": str(bambu_output),
            "manifest": str(manifest_path),
            "sha256": bambu_verified.get("sha256"),
            "bytes": bambu_verified.get("bytes"),
            "has_embedded_gcode": bambu_verified.get("has_embedded_gcode"),
            "multipart_audit": dict(multipart),
            "mesh_audit": dict(mesh),
            "profile_audit": profile_audit,
        }
    return result


def _aggregate_release_status(statuses: Iterable[str]) -> str:
    """Fold canonical transaction statuses without hiding a requested stage."""

    values = list(statuses)
    if not values:
        return "failed"
    if any(value == "failed" for value in values):
        return "failed"
    if any(value != "passed" for value in values):
        return "unverifiable"
    return "passed"


def _copy_artifacts(source_dir: Path, destination: Path, *, force: bool = False) -> list[str]:
    destination.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for path in sorted(source_dir.glob("*.3mf")):
        target = destination / path.name
        if target.exists() and not force:
            raise SmokeError(f"refusing to overwrite artifact; pass --force-artifacts: {target}")
        shutil.copy2(path, target)
        manifest = path.with_suffix(path.suffix + ".manifest.json")
        if manifest.is_file():
            manifest_target = destination / manifest.name
            if manifest_target.exists() and not force:
                raise SmokeError(f"refusing to overwrite artifact manifest: {manifest_target}")
            shutil.copy2(manifest, manifest_target)
        copied.append(str(target))
    # The canonical bridge writes a semantic release report in addition to the
    # adapter's package sidecar.  Preserve it when present so a retained 3MF
    # carries evidence that the approved brief and same-canvas gates actually
    # ran, rather than only proving that the ZIP is structurally readable.
    for path in sorted(source_dir.glob("*-3mf-release.json")):
        target = destination / path.name
        if target.exists() and not force:
            raise SmokeError(f"refusing to overwrite artifact release report: {target}")
        shutil.copy2(path, target)
        copied.append(str(target))
    if not copied:
        raise SmokeError(f"no 3MF outputs were produced under {source_dir}")
    return copied


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE, help="source fixture directory")
    parser.add_argument(
        "--bambu",
        choices=("auto", "never", "export", "slice"),
        default="auto",
        help="optional Bambu stage (auto slices once when profiles are found)",
    )
    parser.add_argument("--require-external", action="store_true", help="fail if OpenSCAD/Bambu stages are unavailable")
    parser.add_argument("--machine-profile", type=Path, help="explicit Bambu machine profile JSON")
    parser.add_argument("--process-profile", type=Path, help="explicit Bambu process profile JSON")
    parser.add_argument("--filament-profile", type=Path, help="explicit Bambu filament profile JSON")
    parser.add_argument("--keep-workdir", action="store_true", help="retain the isolated generated checkout")
    parser.add_argument("--workdir", type=Path, help="explicit isolated output directory (implies --keep-workdir)")
    parser.add_argument("--artifact-dir", type=Path, help="copy generated 3MFs and sidecars here")
    parser.add_argument("--force-artifacts", action="store_true", help="allow replacing files in --artifact-dir")
    parser.add_argument(
        "--bridge-job",
        type=Path,
        help=(
            "fixture-relative jobs/.../job.toml that receives the optional Bambu "
            "stage; every job always uses the canonical native 3MF bridge"
        ),
    )
    parser.add_argument("--json", action="store_true", help="print the complete report as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    fixture = args.fixture.expanduser().resolve()
    if not fixture.is_dir():
        print(f"lens-cap smoke-rehouse: fixture directory not found: {fixture}", file=sys.stderr)
        return 2
    retained = args.workdir is not None or args.keep_workdir
    temp_context: tempfile.TemporaryDirectory[str] | None = None
    if args.workdir:
        work_root = args.workdir.expanduser().resolve()
        if work_root.exists() and any(work_root.iterdir()):
            print(f"lens-cap smoke-rehouse: workdir is not empty: {work_root}", file=sys.stderr)
            return 2
        work_root.mkdir(parents=True, exist_ok=True)
    else:
        if retained:
            # ``TemporaryDirectory`` always cleans itself at interpreter
            # shutdown, even when the object is kept alive.  Use mkdtemp for
            # an explicitly retained rehearsal and remove it ourselves only
            # in the ordinary ephemeral mode.
            work_root = Path(tempfile.mkdtemp(prefix="lens-cap-rehouse-smoke-"))
        else:
            temp_context = tempfile.TemporaryDirectory(prefix="lens-cap-rehouse-smoke-")
            work_root = Path(temp_context.name)

    try:
        clean_fixture = work_root / "fixture"
        generated_artifacts = work_root / "artifacts"
        job_relatives = _copy_fixture(fixture, clean_fixture)
        brief_report = _validate_brief(clean_fixture)
        openscad = _find_executable("openscad", "/Applications/OpenSCAD.app/Contents/MacOS/OpenSCAD")
        bambu = _find_executable(
            "bambu-studio",
            "BambuStudio",
            "/Applications/BambuStudio.app/Contents/MacOS/BambuStudio",
        )
        profiles = _explicit_bambu_profiles(
            args.machine_profile,
            args.process_profile,
            args.filament_profile,
        ) or _find_bambu_profiles()
        if args.require_external and not openscad:
            raise SmokeError("OpenSCAD executable not found; --require-external needs native 3MF export")
        jobs: list[PipelineConfig] = []
        job_reports: list[dict[str, Any]] = []
        jobs_by_relative: dict[str, tuple[PipelineConfig, dict[str, Any]]] = {}
        for relative_job in job_relatives:
            job_path = clean_fixture / relative_job
            config, report = _build_job(
                job_path,
                require_external=args.require_external,
                export_openscad=bool(openscad),
            )
            report["projection"] = _projection_audit(config)
            jobs.append(config)
            job_reports.append(report)
            jobs_by_relative[relative_job.as_posix()] = (config, report)
        if not jobs:
            raise SmokeError("fixture did not yield any jobs")

        # ``--bridge-job`` remains accepted, but now selects which matrix job
        # receives the optional Bambu transaction. Every job, selected or not,
        # goes through the canonical bridge for its native one-piece package.
        selected_config: PipelineConfig
        if args.bridge_job is not None:
            raw_bridge_job = args.bridge_job
            if raw_bridge_job.is_absolute() or ".." in raw_bridge_job.parts:
                raise SmokeError("--bridge-job must be a fixture-relative jobs/.../job.toml path")
            bridge_key = raw_bridge_job.as_posix().lstrip("./")
            selected = jobs_by_relative.get(bridge_key)
            if selected is None:
                choices = ", ".join(sorted(jobs_by_relative))
                raise SmokeError(f"--bridge-job {bridge_key!r} is not a fixture job; choose one of: {choices}")
            selected_config = selected[0]
        else:
            selected_config = max(
                jobs,
                key=lambda config: float(
                    config.measured_diameter_mm or config.face_diameter_mm
                ),
            )

        if args.bambu in {"export", "slice"}:
            selected_bambu_mode = args.bambu
        elif args.bambu == "auto" and (
            (bambu is not None and profiles is not None) or args.require_external
        ):
            selected_bambu_mode = "slice"
        else:
            selected_bambu_mode = "never"

        canonical_reports: list[dict[str, Any]] = []
        selected_canonical: dict[str, Any] | None = None
        bambu_report: dict[str, Any] = {
            "status": "not_requested",
            "mode": args.bambu,
        }
        if args.bambu == "auto" and selected_bambu_mode == "never":
            bambu_report["reason"] = (
                "optional Bambu auto-stage skipped because the executable/profile trio "
                "was not completely available"
            )
        for config, report in zip(jobs, job_reports, strict=True):
            bridge_mode = (
                selected_bambu_mode if config is selected_config else "never"
            )
            canonical = _canonical_bridge(
                config,
                generated_artifacts,
                openscad=openscad,
                require_external=args.require_external,
                bambu_mode=bridge_mode,
                bambu=bambu,
                profiles=profiles,
            )
            canonical_reports.append(canonical)
            if config is selected_config:
                selected_canonical = canonical
            report["canonical_bridge"] = canonical
            if canonical.get("status") == "passed":
                report["native_3mf"] = canonical["native_3mf"]
            else:
                report["native_3mf"] = {
                    "status": canonical.get("status", "failed"),
                    "mode": "canonical-one-piece",
                    "reason": canonical.get("reason", "canonical bridge did not pass"),
                }
            if config is selected_config and selected_bambu_mode != "never":
                if canonical.get("status") == "passed":
                    bambu_report = canonical["bambu_3mf"]
                else:
                    bambu_report = {
                        "status": canonical.get("status", "failed"),
                        "mode": selected_bambu_mode,
                        "job": config.job_slug,
                        "reason": canonical.get("reason", "canonical bridge did not pass"),
                    }

        native_3mf_complete = bool(job_reports) and all(
            item.get("native_3mf", {}).get("status") == "passed" for item in job_reports
        )
        overall_status = _aggregate_release_status(
            report.get("status", "failed") for report in canonical_reports
        )
        if args.bambu in {"export", "slice"}:
            overall_status = _aggregate_release_status(
                (overall_status, str(bambu_report.get("status", "failed")))
            )
        copied = (
            _copy_artifacts(
                generated_artifacts,
                args.artifact_dir.expanduser().resolve(),
                force=args.force_artifacts,
            )
            if args.artifact_dir and overall_status == "passed"
            else []
        )
        bridge_report = {
            "status": overall_status,
            "runner": "scripts/build_3mf.py",
            "all_jobs_canonical": True,
            "job_count": len(canonical_reports),
            "selected_bambu_job": selected_config.job_slug,
            "jobs": canonical_reports,
        }
        if (
            overall_status == "passed"
            and isinstance(selected_canonical, Mapping)
            and isinstance(selected_canonical.get("design_brief"), Mapping)
        ):
            # Preserve the v1 consumer contract while exposing the complete
            # per-job bridge list above.
            bridge_report["design_brief"] = dict(
                selected_canonical["design_brief"]
            )
        report = {
            "schema_version": 1,
            "status": overall_status,
            "deterministic_preflight_status": "passed",
            "native_3mf_complete": native_3mf_complete,
            "runner": "scripts/smoke_rehouse.py",
            "fixture": str(fixture),
            "clean_fixture": str(clean_fixture),
            "workdir_retained": retained,
            "tools": {
                "openscad": Path(openscad).name if openscad else None,
                "bambu": Path(bambu).name if bambu else None,
                "bambu_profiles_found": bool(profiles),
            },
            "brief": brief_report,
            "jobs": job_reports,
            "bambu": bambu_report,
            "canonical_bridge": bridge_report,
            "copied_artifacts": copied,
            "fit_status": "unverifiable_until_coupon_measurement",
            "notes": [
                "Style-equivalence and same-canvas projection are audited; pixel-perfect generative reproduction is not claimed.",
                "A valid 3MF and slicer result do not prove physical fit; print and measure a coupon.",
            ],
        }
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else _human_summary(report))
        return 1 if overall_status == "failed" else 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError, SmokeError) as exc:
        print(f"lens-cap smoke-rehouse: error: {exc}", file=sys.stderr)
        return 1
    finally:
        if temp_context is not None and not retained:
            temp_context.cleanup()


def _human_summary(report: dict[str, Any]) -> str:
    lines = [
        f"status: {report['status']}",
        f"deterministic preflight: {report.get('deterministic_preflight_status', 'unknown')}",
        f"clean fixture: {report['clean_fixture']}",
        "jobs: " + ", ".join(f"{item['measured_diameter_mm']:.0f} mm" for item in report["jobs"]),
        "native 3MF: " + ", ".join(item["native_3mf"]["status"] for item in report["jobs"]),
        f"Bambu: {report['bambu']['status']} ({report['bambu'].get('mode')})",
        f"canonical bridge: {report.get('canonical_bridge', {}).get('status', 'not_requested')}",
        "fit: UNVERIFIABLE until a physical coupon is measured",
    ]
    if report.get("copied_artifacts"):
        lines.append("copied artifacts: " + ", ".join(report["copied_artifacts"]))
    if report.get("workdir_retained"):
        lines.append("workdir retained: inspect the generated reports and 3MF files above")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())

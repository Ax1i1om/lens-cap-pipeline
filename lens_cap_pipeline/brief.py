"""Design-brief handoff and validation helpers.

The image provider is intentionally outside the package.  This module only
binds a user-approved raster to a small, source-backed design brief so the
canonical 3MF bridge can fail closed when semantic provenance is missing.
It also provides the provider-neutral scaffold used by ``lens-cap
handoff-init``.  A scaffold is never considered approved automatically.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from .config import PipelineConfig


class BriefError(ValueError):
    """Raised when a design brief is missing or fails the handoff contract."""


_FOCAL_DISPLAY_RE = re.compile(
    r"^(?P<start>[0-9]+(?:\.[0-9]+)?)(?:mm)?"
    r"(?:-(?P<end>[0-9]+(?:\.[0-9]+)?)(?:mm)?)?$",
    re.I,
)
_APERTURE_DISPLAY_RE = re.compile(
    r"^F(?P<start>[0-9]+(?:\.[0-9]+)?)"
    r"(?:-F?(?P<end>[0-9]+(?:\.[0-9]+)?))?$",
    re.I,
)
_BINDING_MODES = frozenset({"single_job", "shared_artwork_variants"})
_IDENTITY_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_ANCHOR_CLAIM_KINDS = frozenset(
    {
        "lens_specification",
        "manufacturer_culture",
        "brand_culture",
        "manufacturer_history",
        "brand_history",
        "system_culture",
        "rehousing_and_cinema_association",
        "rehousing_cinema_association",
        "cinema_rehousing_context",
        "film_association",
        "historical_use",
        "spaceflight_association",
        "community_moniker",
    }
)
_MANUFACTURER_CULTURE_CLAIM_KINDS = frozenset(
    {
        "manufacturer_culture",
        "brand_culture",
        "manufacturer_history",
        "brand_history",
        "system_culture",
    }
)
_ANCHOR_IDENTITY_SCOPES = frozenset({"brand", "model", "family"})
_ANCHOR_CONSEQUENCE_SYSTEMS = frozenset(
    {
        "typography_or_counterform",
        "field_path_or_divide",
        "container_or_perimeter",
    }
)
_REFERENCE_ROLES = frozenset(
    {
        "quality_reference",
        "style_reference",
        "edit_target",
        "exact_content_reference",
    }
)
_PLACEHOLDER_PREFIX_RE = re.compile(
    r"^(?:replace(?:\b|_)|to[\s_-]*verify(?:\b|_)|todo(?:\b|:)|"
    r"tbd(?:\b|:)|url\s+or\s+archive(?:\b|:))",
    re.I,
)
_STABLE_ID_RE = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
_KNOWN_REVIEW_FILLER = frozenset(
    {"asdf", "qwer", "zxcv", "lorem", "ipsum", "blah", "dummy", "test", "okay", "ok"}
)


def sha256_file(path: Path) -> str:
    """Return a streaming SHA-256 digest for a handoff file."""

    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise BriefError(f"cannot read handoff file {path}: {exc}") from exc
    return digest.hexdigest()


def _json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BriefError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BriefError(f"{label} must contain a JSON object: {path}")
    return value


def _nonempty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BriefError(f"{field} must be non-empty text")
    return value.strip()


def _reviewed(value: Any, field: str) -> str:
    """Require a non-placeholder value at the public approval boundary."""

    text = _nonempty(value, field)
    folded = unicodedata.normalize("NFKC", text).casefold().strip()
    if _PLACEHOLDER_PREFIX_RE.match(folded):
        raise BriefError(f"{field} still contains a scaffold placeholder")
    return text


def _substantive_reviewed(value: Any, field: str, *, min_alnum: int = 3) -> str:
    """Reject placeholders and token answers that carry no review evidence."""

    text = _reviewed(value, field)
    folded = " ".join(text.casefold().split())
    if sum(character.isalnum() for character in text) < min_alnum or folded in {
        "x",
        "xx",
        "n/a",
        "na",
        "none",
        "unknown",
        "ok",
    }:
        raise BriefError(f"{field} must contain a substantive reviewed value")
    return text


def _stable_id(value: Any, field: str) -> str:
    """Require a portable, human-readable identifier for cross-field binding."""

    text = _reviewed(value, field)
    if not _STABLE_ID_RE.fullmatch(text):
        raise BriefError(
            f"{field} must be a lowercase stable id using letters, digits, '-' or '_'"
        )
    return text


def _reference_roles(reference: Mapping[str, Any], prefix: str) -> tuple[str, ...]:
    """Return canonical reference roles without accepting ambiguous packed text."""

    if "role" in reference:
        raise BriefError(f"{prefix}.role is ambiguous; use a roles array even for one role")
    roles = reference.get("roles")
    if not isinstance(roles, list) or not roles:
        raise BriefError(f"{prefix}.roles must be a non-empty list")
    if not all(isinstance(role, str) and role in _REFERENCE_ROLES for role in roles):
        raise BriefError(
            f"{prefix}.roles must contain only " + ", ".join(sorted(_REFERENCE_ROLES))
        )
    if len(set(roles)) != len(roles):
        raise BriefError(f"{prefix}.roles must not contain duplicates")
    return tuple(roles)


def _review_evidence(
    value: Any,
    field: str,
    *,
    min_alnum: int,
    min_words: int = 3,
) -> str:
    """Reject token, repeated-pattern, and single-word pseudo-review evidence.

    This deliberately does not pretend to score visual quality.  It only makes
    the release record carry inspectable natural-language evidence rather than
    a checkbox, repeated character, or copied scaffold sentinel.  CJK evidence
    is assessed by character diversity because whitespace tokenization is not
    meaningful for those scripts.
    """

    text = _substantive_reviewed(value, field, min_alnum=min_alnum)
    normalized = unicodedata.normalize("NFKC", text).casefold()
    alnum = "".join(character for character in normalized if character.isalnum())
    unique_alnum = set(alnum)
    minimum_unique = 5 if min_alnum >= 20 else 4
    if len(unique_alnum) < minimum_unique:
        raise BriefError(f"{field} must contain varied review evidence, not repeated characters")
    if alnum and max(alnum.count(character) for character in unique_alnum) / len(alnum) > 0.7:
        raise BriefError(f"{field} must contain varied review evidence, not repeated characters")
    for period in range(1, min(32, len(alnum) // 3) + 1):
        if len(alnum) % period == 0 and alnum == alnum[:period] * (len(alnum) // period):
            raise BriefError(f"{field} must contain varied review evidence, not a repeated pattern")
    token_sequence = re.findall(r"[^\W_]+", normalized, re.UNICODE)
    for period in range(1, min(8, len(token_sequence) // 3) + 1):
        repetitions = len(token_sequence) // period
        if (
            len(token_sequence) % period == 0
            and repetitions >= 3
            and token_sequence == token_sequence[:period] * repetitions
        ):
            raise BriefError(
                f"{field} must contain varied review evidence, not a repeated phrase"
            )
    has_unsegmented_east_asian_script = bool(
        re.search(
            r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]",
            normalized,
        )
    )
    if not has_unsegmented_east_asian_script:
        word_sequence = [
            match.casefold()
            for match in re.findall(r"[^\W\d_]{2,}", normalized, re.UNICODE)
        ]
        words = set(word_sequence)
        if words and words <= _KNOWN_REVIEW_FILLER:
            raise BriefError(f"{field} must contain review evidence, not known filler text")
        if len(words) < min_words:
            raise BriefError(
                f"{field} must contain at least {min_words} distinct review words"
            )
    return text


def _source_reference(value: Any, field: str) -> str:
    """Require a reviewable web source or an explicit archival identifier."""

    text = _substantive_reviewed(value, field)
    parsed = urlsplit(text)
    if parsed.scheme.casefold() in {"http", "https"} and parsed.netloc:
        return text
    if re.fullmatch(r"(?i)(?:archive(?:[-_]id)?|urn):[^\s]+", text):
        return text
    raise BriefError(
        f"{field} must be an http(s) URL or an explicit archive identifier "
        "such as archive:collection/item"
    )


def _positive_evidence_state(value: Any, field: str) -> str:
    """Require an explicit positive evidence status, never a substring hit."""

    text = _substantive_reviewed(value, field)
    folded = unicodedata.normalize("NFKC", text).casefold().strip()
    negative_form = folded.replace("_", " ").replace("-", " ")
    if re.search(
        r"\b(?:not|never|no)\s+(?:yet\s+)?"
        r"(?:verified|sourced|documented|attested|archived)\b|"
        r"\b(?:unverified|unsourced|undocumented|unattested|unarchived)\b",
        negative_form,
    ):
        raise BriefError(f"{field} must state a positive sourced/verified status")
    if not re.match(
        r"^(?:verified|sourced|documented|attested|archived)"
        r"(?:$|[\s_:;,.()/+\-])",
        folded,
    ):
        raise BriefError(
            f"{field} must start with a positive sourced/verified status"
        )
    return text


def _provenance_statement(value: Any, field: str, *, min_alnum: int = 3) -> str:
    """Accept a real licence statement or a reasoned not-applicable sentence."""

    text = _substantive_reviewed(value, field, min_alnum=min_alnum)
    folded = " ".join(text.casefold().split())
    if folded.startswith(("not applicable", "n/a", "na —", "na -", "none —", "none -")):
        remainder = re.sub(
            r"^(?:not applicable|n/?a|none)\s*(?:[—–-]|:|because)?\s*",
            "",
            folded,
        )
        if sum(character.isalnum() for character in remainder) < 8:
            raise BriefError(
                f"{field} must explain why the permission/licence is not applicable"
            )
    return text


def _positive_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise BriefError(f"{field} must be a positive number")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise BriefError(f"{field} must be a positive number") from exc
    if not math.isfinite(result) or result <= 0:
        raise BriefError(f"{field} must be a positive number")
    return result


def canonical_focal_display(value: Any, *, field: str = "focal_length_display") -> str:
    """Normalize a prime/range display token without changing its spelling."""

    text = _nonempty(value, field)
    text = unicodedata.normalize("NFKC", text).casefold().strip()
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
        raise BriefError(
            f"{field} must be a focal token such as '50mm' or a zoom range such as '28–70mm'"
        )
    start = float(match.group("start"))
    end = float(match.group("end")) if match.group("end") else None
    if not math.isfinite(start) or start <= 0:
        raise BriefError(f"{field} must use a positive focal length")
    if end is not None and (not math.isfinite(end) or end <= start):
        raise BriefError(f"{field} zoom range must end above its starting focal length")
    return f"{start:g}-{end:g}" if end is not None else f"{start:g}"


def _aperture_components(
    value: Any, field: str = "maximum_aperture"
) -> tuple[str, str]:
    """Return the first-end anchor and canonical F-number display.

    Variable-aperture zoom notation is an F-number range, not a T-stop range.
    The first endpoint remains the stable machine anchor while the complete
    range is preserved for the second visual read.
    """

    text = unicodedata.normalize("NFKC", _nonempty(value, field)).strip()
    text = (
        text.replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("‑", "-")
        .replace("‒", "-")
        .replace("－", "-")
        .replace("~", "-")
        .replace("至", "-")
    )
    text = re.sub(r"\bto\b", "-", text, flags=re.I)
    compact = "".join(text.upper().split()).replace("/", "")
    if compact.startswith("T"):
        raise BriefError(
            f"{field} must be an F-number such as F1.4 or F3.5-5.6; "
            "T-stops are not supported"
        )
    if not compact.startswith("F"):
        compact = "F" + compact
    match = _APERTURE_DISPLAY_RE.fullmatch(compact)
    if match is None:
        raise BriefError(
            f"{field} must be an F-number such as F1.4 or F3.5-5.6; "
            "T-stops are not supported"
        )
    start = float(match.group("start"))
    end = float(match.group("end")) if match.group("end") else None
    if not math.isfinite(start) or start <= 0:
        raise BriefError(f"{field} must use a positive F-number")
    if end is not None and (not math.isfinite(end) or end <= start):
        raise BriefError(f"{field} range must end above its first F-number")
    anchor = f"F{start:g}"
    display = f"{anchor}-{end:g}" if end is not None else anchor
    return anchor, display


def _aperture_token(value: Any, field: str = "maximum_aperture") -> str:
    """Return the first endpoint of a prime or variable-aperture F-number."""

    return _aperture_components(value, field)[0]


def canonical_aperture_display(
    value: Any, *, field: str = "maximum_aperture_display"
) -> str:
    """Normalize a prime or variable-aperture display to ASCII range form."""

    return _aperture_components(value, field)[1]


def _identity_tokens(value: Any, field: str) -> set[str]:
    """Return punctuation-insensitive semantic tokens for one lens identity.

    F-number spellings such as ``F1.4`` and ``f/1.4`` collapse to the same
    token, while focal units collapse from ``50mm`` to ``50``.  Keeping the
    aperture's ``f`` prefix prevents a model suffix such as ``44-2`` from
    masquerading as the required F2 token.
    """

    text = unicodedata.normalize("NFKC", _nonempty(value, field)).casefold()
    text = (
        text.replace("毫米", "mm")
        .replace(",", ".")
        .replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("‑", "-")
        .replace("‒", "-")
        .replace("－", "-")
        .replace("~", "-")
    )

    def canonical_number(raw: str) -> str:
        return f"{float(raw):g}"

    def canonical_aperture_tokens(match: re.Match[str]) -> str:
        tokens = ["f" + canonical_number(match.group(1)).replace(".", "p")]
        if match.group(2) is not None:
            tokens.append("f" + canonical_number(match.group(2)).replace(".", "p"))
        return " " + " ".join(tokens) + " "

    text = re.sub(
        r"(?<![\w])f\s*/?\s*([0-9]+(?:\.[0-9]+)?)"
        r"(?:\s*-\s*(?:f\s*/?\s*)?([0-9]+(?:\.[0-9]+)?))?",
        canonical_aperture_tokens,
        text,
    )
    text = re.sub(
        r"(?<![\w])([0-9]+(?:\.[0-9]+)?)\s*mm(?![\w])",
        lambda match: " " + canonical_number(match.group(1)) + " ",
        text,
    )
    return set(_IDENTITY_TOKEN_RE.findall(text))


def _normalized_identity_text(value: Any, field: str) -> str:
    """Normalize a structured identity label for exact binding checks."""

    return " ".join(
        unicodedata.normalize("NFKC", _nonempty(value, field)).casefold().split()
    )


def _validate_culture_anchor_identity(
    anchor: Mapping[str, Any],
    *,
    prefix: str,
    subject_scope: str,
    brand: str,
    model: str,
) -> None:
    """Bind a qualifying maker-culture anchor to this brief's lens maker.

    Free-text substring checks let values such as
    ``not_manufacturer_culture`` or a Canon subject satisfy a Helios brief.
    A qualifying anchor therefore carries an explicit structured binding and
    its human-readable subject must still mention the maker/model identity.
    Family-level lore remains possible, but it is deliberate and named.
    """

    binding = anchor.get("identity_binding")
    if not isinstance(binding, Mapping):
        raise BriefError(
            f"{prefix}.identity_binding must bind a manufacturer-culture "
            "anchor to this lens identity"
        )
    binding_brand = _normalized_identity_text(
        binding.get("brand"), f"{prefix}.identity_binding.brand"
    )
    expected_brand = _normalized_identity_text(brand, "lens_identity.brand")
    if binding_brand != expected_brand:
        raise BriefError(
            f"{prefix}.identity_binding.brand disagrees with lens_identity.brand"
        )
    scope = _normalized_identity_text(
        binding.get("scope"), f"{prefix}.identity_binding.scope"
    )
    if scope not in _ANCHOR_IDENTITY_SCOPES:
        raise BriefError(
            f"{prefix}.identity_binding.scope must be brand, model, or family"
        )
    if scope == "model":
        binding_model = _normalized_identity_text(
            binding.get("model"), f"{prefix}.identity_binding.model"
        )
        if binding_model != _normalized_identity_text(model, "lens_identity.model"):
            raise BriefError(
                f"{prefix}.identity_binding.model disagrees with lens_identity.model"
            )
    if scope == "family":
        _substantive_reviewed(
            binding.get("family"),
            f"{prefix}.identity_binding.family",
            min_alnum=3,
        )

    subject_tokens = _identity_tokens(subject_scope, f"{prefix}.subject_scope")
    brand_groups = [
        _identity_tokens(part, "lens_identity.brand")
        for part in re.split(r"\s*(?:/|\||;)\s*", brand)
        if part.strip()
    ]
    model_tokens = _identity_tokens(model, "lens_identity.model")
    generic_tokens = {
        "camera",
        "cine",
        "cinema",
        "family",
        "lens",
        "lenses",
        "optical",
        "system",
    }
    distinctive_model = {
        token for token in model_tokens if token not in generic_tokens and not token.isdigit()
    }
    mentions_identity = any(
        group and group <= subject_tokens for group in brand_groups
    ) or bool(distinctive_model & subject_tokens)
    if not mentions_identity:
        raise BriefError(
            f"{prefix}.subject_scope must name this lens brand/model, not an unrelated maker"
        )


def _validate_metadata_identity_binding(
    metadata_identity: str,
    *,
    brand: str,
    model: str,
    focal_display: str,
    aperture: str,
) -> None:
    """Bind the free-text job identity to the brief's structured identity."""

    actual = _identity_tokens(metadata_identity, "job metadata.lens_identity")
    missing_fields: list[str] = []

    # A slash-delimited brand names explicit aliases (for example
    # ``Helios / Zenit``); one complete alias is enough. Multi-word brands
    # without such a delimiter still require every word.
    brand_alternatives = [
        _identity_tokens(part, "lens_identity.brand")
        for part in re.split(r"\s*(?:/|\||;)\s*", brand)
        if part.strip()
    ]
    if not brand_alternatives or not any(tokens <= actual for tokens in brand_alternatives):
        missing_fields.append("brand")

    requirements = {
        "model": _identity_tokens(model, "lens_identity.model"),
        "focal length": _identity_tokens(focal_display, "lens_identity.focal_length_display"),
        "maximum aperture": _identity_tokens(aperture, "lens_identity.maximum_aperture"),
    }
    missing_fields.extend(
        field for field, expected in requirements.items() if not expected <= actual
    )
    if missing_fields:
        raise BriefError(
            "job metadata.lens_identity must contain normalized "
            "lens_identity.brand/model/focal length/maximum aperture tokens; missing: "
            + ", ".join(missing_fields)
        )


def _brief_path_from_config(config: PipelineConfig, explicit: str | Path | None = None) -> Path:
    """Resolve the brief declared by a job, or the nearest conventional one."""

    if explicit is not None:
        candidate = Path(explicit).expanduser()
        if not candidate.is_absolute():
            candidate = (Path.cwd() / candidate).resolve()
        return candidate
    metadata = config.metadata if isinstance(config.metadata, Mapping) else {}
    declared = metadata.get("design_brief", metadata.get("brief"))
    if isinstance(declared, str) and declared.strip():
        candidate = Path(declared).expanduser()
        if not candidate.is_absolute():
            candidate = (config.config_path.parent / candidate).resolve()
        return candidate
    # A nested job (for example jobs/95mm/job.toml) conventionally shares the
    # fixture-level brief.  Walk upward, but never silently choose a sibling
    # from an unrelated branch.
    current = config.config_path.parent.resolve()
    # Search only the local job/fixture envelope.  Walking all the way to the
    # filesystem root could accidentally bind a job to an unrelated
    # ``/design-brief.json`` left by another project.
    for directory in (current, *tuple(current.parents)[:3]):
        candidate = directory / "design-brief.json"
        if candidate.is_file():
            return candidate
    return current / "design-brief.json"


def resolve_brief_path(config: PipelineConfig, explicit: str | Path | None = None) -> Path:
    """Public path resolver used by the bridge and the handoff checker."""

    return _brief_path_from_config(config, explicit)


def _resolve_inside(base: Path, value: str, field: str) -> Path:
    raw = Path(value).expanduser()
    candidate = raw if raw.is_absolute() else base / raw
    candidate = candidate.resolve()
    if not candidate.is_file():
        raise BriefError(f"{field} does not exist: {candidate}")
    return candidate


def _same_declared_value(actual: Any, declared: Any, field: str) -> None:
    """Compare one non-null physical snapshot value to the active job.

    ``physical_fit`` is deliberately allowed to leave size-dependent values
    null so one approved artwork brief can serve several diameter variants.
    Once a value is declared, however, it is part of the approval record and
    must not silently drift from the TOML that drives the exported geometry.
    """

    if declared is None:
        return
    if isinstance(actual, bool) or isinstance(declared, bool):
        if not isinstance(actual, bool) or not isinstance(declared, bool) or actual is not declared:
            raise BriefError(f"physical_fit.{field} disagrees with the job config")
        return
    if isinstance(actual, (int, float)) and not isinstance(actual, bool):
        if isinstance(declared, bool):
            raise BriefError(f"physical_fit.{field} disagrees with the job config")
        try:
            declared_number = float(declared)
        except (TypeError, ValueError, OverflowError) as exc:
            raise BriefError(f"physical_fit.{field} must be numeric or null") from exc
        if not math.isfinite(declared_number) or not math.isclose(
            float(actual), declared_number, rel_tol=0.0, abs_tol=1e-7
        ):
            raise BriefError(f"physical_fit.{field} disagrees with the job config")
        return
    if actual != declared:
        raise BriefError(f"physical_fit.{field} disagrees with the job config")


def _same_job_snapshot(actual: Any, declared: Any, field: str) -> None:
    """Recursively compare an artwork/process job snapshot without coercion."""

    if isinstance(actual, Mapping):
        if not isinstance(declared, Mapping):
            raise BriefError(f"{field} must be an object matching the job config")
        if set(actual) != set(declared):
            raise BriefError(f"{field} keys disagree with the job config")
        for key, value in actual.items():
            _same_job_snapshot(value, declared[key], f"{field}.{key}")
        return
    if isinstance(actual, (list, tuple)):
        if not isinstance(declared, list) or len(actual) != len(declared):
            raise BriefError(f"{field} disagrees with the job config")
        for index, value in enumerate(actual):
            _same_job_snapshot(value, declared[index], f"{field}[{index}]")
        return
    if isinstance(actual, bool) or isinstance(declared, bool):
        if not isinstance(actual, bool) or not isinstance(declared, bool) or actual is not declared:
            raise BriefError(f"{field} disagrees with the job config")
        return
    if isinstance(actual, (int, float)) and not isinstance(actual, bool):
        if not isinstance(declared, (int, float)) or isinstance(declared, bool):
            raise BriefError(f"{field} disagrees with the job config")
        if not math.isfinite(float(declared)) or not math.isclose(
            float(actual), float(declared), rel_tol=0.0, abs_tol=1e-9
        ):
            raise BriefError(f"{field} disagrees with the job config")
        return
    if actual != declared:
        raise BriefError(f"{field} disagrees with the job config")


def _artwork_process_snapshot(config: PipelineConfig) -> dict[str, Any]:
    """Return normalized job fields that can change approved artwork output."""

    return {
        "grid_size": config.grid_size,
        "safe_border_mm": config.safe_border_mm,
        "prefilter": config.prefilter.public(),
        "cleanup": config.cleanup.public(),
        "assembly_mode": config.assembly_mode,
    }


def validate_design_brief(
    config: PipelineConfig,
    explicit_path: str | Path | None = None,
) -> dict[str, Any]:
    """Validate an approved brief and bind it to the current job artwork.

    This is intentionally stricter than the deterministic process validator:
    the bridge is a public release endpoint, so an absent/unapproved brief is
    a hard validation failure rather than a semantic pass.  The function
    does not require a particular image provider or a particular URL host.
    """

    brief_path = _brief_path_from_config(config, explicit_path)
    if not brief_path.is_file():
        raise BriefError(
            f"approved design brief not found: {brief_path}; run "
            f"'lens-cap handoff-init {config.config_path}' and review it"
        )
    brief = _json_object(brief_path, "design brief")
    schema_version = brief.get("schema_version")
    if type(schema_version) is not int:  # bool and 2.0 are not schema integers
        raise BriefError("design brief schema_version must be the integer 2")
    if schema_version == 1:
        raise BriefError(
            "design brief schema_version must be 2; legacy v1 requires a new reviewed "
            "v2 brief—do not overwrite the old approval record in place"
        )
    if schema_version != 2:
        raise BriefError(
            f"unsupported design brief schema_version {schema_version}; this tool supports 2"
        )
    binding_mode = _nonempty(brief.get("binding_mode"), "binding_mode")
    if binding_mode not in _BINDING_MODES:
        raise BriefError(
            "binding_mode must be 'single_job' or 'shared_artwork_variants'"
        )
    brief_slug_value = _nonempty(brief.get("job_slug"), "job_slug")
    if binding_mode == "single_job":
        if config.job_slug != brief_slug_value:
            raise BriefError("single-job design brief job_slug does not match the job config")
    elif config.job_slug != brief_slug_value and not config.job_slug.startswith(
        brief_slug_value + "-"
    ):
        raise BriefError("shared-variant design brief job_slug does not match the job config")
    generation = brief.get("generation")
    if not isinstance(generation, Mapping) or generation.get("approved") is not True:
        raise BriefError("design brief generation.approved must be true before a 3MF release")
    _reviewed(generation.get("provider"), "generation.provider")
    _reviewed(generation.get("mode"), "generation.mode")
    _review_evidence(
        generation.get("approval_note"),
        "generation.approval_note",
        min_alnum=20,
    )

    production_target = _reviewed(
        brief.get("production_target"), "production_target"
    )
    if production_target != "printable_front":
        raise BriefError(
            "production_target must be printable_front before a 3MF release"
        )

    identity = brief.get("lens_identity")
    if not isinstance(identity, Mapping):
        raise BriefError("design brief lens_identity must be an object")
    brand = _reviewed(identity.get("brand"), "lens_identity.brand")
    model = _reviewed(identity.get("model"), "lens_identity.model")
    focal = _positive_number(identity.get("focal_length_mm"), "lens_identity.focal_length_mm")
    aperture, aperture_from_anchor = _aperture_components(
        identity.get("maximum_aperture")
    )
    aperture_display_raw = identity.get("maximum_aperture_display")
    if aperture_display_raw is None:
        aperture_display = aperture_from_anchor
    else:
        aperture_display_anchor, aperture_display = _aperture_components(
            aperture_display_raw,
            "lens_identity.maximum_aperture_display",
        )
        if aperture_display_anchor != aperture:
            raise BriefError(
                "lens_identity.maximum_aperture_display must start at maximum_aperture"
            )
        if aperture_from_anchor != aperture and aperture_from_anchor != aperture_display:
            raise BriefError(
                "lens_identity maximum_aperture range disagrees with maximum_aperture_display"
            )

    focal_display_raw = identity.get("focal_length_display")
    focal_display = (
        canonical_focal_display(focal_display_raw)
        if focal_display_raw is not None
        else f"{focal:g}"
    )
    focal_start = float(focal_display.split("-", 1)[0])
    if not math.isclose(focal_start, focal, abs_tol=1e-9):
        raise BriefError("lens_identity.focal_length_display must start at focal_length_mm")

    display = brief.get("display_text")
    allowed = brief.get("allowed_text")
    if (
        not isinstance(display, list)
        or len(display) < 2
        or not all(isinstance(item, str) and item.strip() for item in display)
        or not isinstance(allowed, list)
        or display != allowed
    ):
        raise BriefError("display_text and allowed_text must be the same ordered closed text set")
    if canonical_focal_display(display[0], field="display_text[0]") != focal_display:
        raise BriefError("display_text[0] must be the focal length first read")
    if canonical_aperture_display(
        display[1], field="display_text[1]"
    ) != aperture_display:
        raise BriefError(
            "display_text[1] must match the complete "
            "lens_identity.maximum_aperture_display second read"
        )

    anchors = brief.get("anchors")
    if not isinstance(anchors, list) or not anchors:
        raise BriefError("design brief needs at least one sourced anchor")
    culture_count = 0
    anchor_commitments: list[str] = []
    anchor_ids: list[str] = []
    for index, anchor in enumerate(anchors):
        if not isinstance(anchor, Mapping):
            raise BriefError(f"anchors[{index}] must be an object")
        prefix = f"anchors[{index}]"
        anchor_id = _stable_id(anchor.get("anchor_id"), f"{prefix}.anchor_id")
        if anchor_id in anchor_ids:
            raise BriefError(f"duplicate anchor_id: {anchor_id}")
        anchor_ids.append(anchor_id)
        claim_value = _substantive_reviewed(anchor.get("claim_kind"), f"{prefix}.claim_kind")
        claim = unicodedata.normalize("NFKC", claim_value).casefold().strip()
        if claim not in _ANCHOR_CLAIM_KINDS:
            raise BriefError(
                f"{prefix}.claim_kind must be one of the canonical positive claim kinds: "
                + ", ".join(sorted(_ANCHOR_CLAIM_KINDS))
            )
        subject_scope = _substantive_reviewed(
            anchor.get("subject_scope"), f"{prefix}.subject_scope"
        )
        _positive_evidence_state(
            anchor.get("evidence_state"), f"{prefix}.evidence_state"
        )
        _substantive_reviewed(anchor.get("source_role"), f"{prefix}.source_role")
        _source_reference(anchor.get("source"), f"{prefix}.source")
        _substantive_reviewed(anchor.get("summary"), f"{prefix}.summary", min_alnum=8)
        _substantive_reviewed(anchor.get("render_role"), f"{prefix}.render_role")
        _substantive_reviewed(
            anchor.get("anchor_context"), f"{prefix}.anchor_context", min_alnum=8
        )
        commitment = _substantive_reviewed(
            anchor.get("motif_commitment"), f"{prefix}.motif_commitment"
        )
        anchor_commitments.append(
            unicodedata.normalize("NFKC", commitment).casefold().strip()
        )
        _substantive_reviewed(
            anchor.get("anchor_visual_motif"),
            f"{prefix}.anchor_visual_motif",
            min_alnum=8,
        )
        _substantive_reviewed(
            anchor.get("recognition_cue"), f"{prefix}.recognition_cue", min_alnum=8
        )
        qualifier = anchor.get("qualifier")
        if qualifier is not None:
            _substantive_reviewed(qualifier, f"{prefix}.qualifier", min_alnum=8)
        if claim in _MANUFACTURER_CULTURE_CLAIM_KINDS:
            _validate_culture_anchor_identity(
                anchor,
                prefix=prefix,
                subject_scope=subject_scope,
                brand=brand,
                model=model,
            )
            culture_count += 1
    if culture_count == 0:
        raise BriefError(
            "design brief needs at least one source-backed manufacturer/brand culture anchor"
        )

    approved_references = brief.get("approved_references")
    if not isinstance(approved_references, list):
        raise BriefError("approved_references must be a list")
    generation_reference_hashes = generation.get("reference_hashes")
    if not isinstance(generation_reference_hashes, list) or not all(
        isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{64}", value)
        for value in generation_reference_hashes
    ):
        raise BriefError("generation.reference_hashes must be a list of SHA-256 digests")
    declared_reference_hashes = {
        value.casefold() for value in generation_reference_hashes
    }
    reference_ids: list[str] = []
    quality_reference_ids: list[str] = []
    for index, reference in enumerate(approved_references):
        if not isinstance(reference, Mapping):
            raise BriefError(f"approved_references[{index}] must be an object")
        prefix = f"approved_references[{index}]"
        roles = _reference_roles(reference, prefix)
        reference_id = _stable_id(reference.get("id"), f"{prefix}.id")
        if reference_id in reference_ids:
            raise BriefError(f"duplicate approved-reference id: {reference_id}")
        reference_ids.append(reference_id)
        _substantive_reviewed(
            reference.get("path_or_url"), f"{prefix}.path_or_url", min_alnum=3
        )
        if "quality_reference" not in roles:
            continue
        quality_reference_ids.append(reference_id)
        snapshot_value = _nonempty(
            reference.get("snapshot_path"), f"{prefix}.snapshot_path"
        )
        snapshot = _resolve_inside(
            brief_path.parent, snapshot_value, f"{prefix}.snapshot_path"
        )
        if brief_path.parent.resolve() not in snapshot.parents:
            raise BriefError(
                f"{prefix}.snapshot_path must stay inside the design-brief directory"
            )
        snapshot_hash = _nonempty(
            reference.get("snapshot_sha256"), f"{prefix}.snapshot_sha256"
        )
        if not re.fullmatch(r"[0-9a-fA-F]{64}", snapshot_hash):
            raise BriefError(f"{prefix}.snapshot_sha256 must be a SHA-256 digest")
        actual_snapshot_hash = sha256_file(snapshot)
        if actual_snapshot_hash.casefold() != snapshot_hash.casefold():
            raise BriefError(f"{prefix}.snapshot_sha256 does not match snapshot_path")
        if snapshot_hash.casefold() not in declared_reference_hashes:
            raise BriefError(
                f"{prefix}.snapshot_sha256 must appear in generation.reference_hashes"
            )
        traits = reference.get("transferable_traits")
        if not isinstance(traits, list) or len(traits) < 2:
            raise BriefError(
                f"{prefix}.transferable_traits must name at least two observable traits"
            )
        for trait_index, trait in enumerate(traits):
            _review_evidence(
                trait,
                f"{prefix}.transferable_traits[{trait_index}]",
                min_alnum=8,
                min_words=2,
            )

    design_review = brief.get("design_review")
    if not isinstance(design_review, Mapping):
        raise BriefError("design brief design_review must be an object")
    reviewed_candidate_hash = _nonempty(
        design_review.get("reviewed_candidate_sha256"),
        "design_review.reviewed_candidate_sha256",
    )
    if not re.fullmatch(r"[0-9a-fA-F]{64}", reviewed_candidate_hash):
        raise BriefError(
            "design_review.reviewed_candidate_sha256 must be a 64-character "
            "hexadecimal digest"
        )
    hero_anchor_index = design_review.get("hero_anchor_index")
    if type(hero_anchor_index) is not int:
        raise BriefError("design_review.hero_anchor_index must be an integer")
    if not 0 <= hero_anchor_index < len(anchors):
        raise BriefError("design_review.hero_anchor_index is outside anchors")
    if anchor_commitments[hero_anchor_index] != "structural":
        raise BriefError(
            "design_review.hero_anchor_index must select an anchor whose "
            "motif_commitment is structural"
        )
    hero_anchor_id = _stable_id(
        design_review.get("hero_anchor_id"), "design_review.hero_anchor_id"
    )
    if hero_anchor_id != anchor_ids[hero_anchor_index]:
        raise BriefError(
            "design_review.hero_anchor_id must match the anchor selected by "
            "hero_anchor_index"
        )
    consequences = design_review.get("anchor_system_consequences")
    if not isinstance(consequences, list) or len(consequences) < 2:
        raise BriefError(
            "design_review.anchor_system_consequences must contain at least two "
            "observable consequences"
        )
    consequence_systems: set[str] = set()
    for index, consequence in enumerate(consequences):
        if not isinstance(consequence, Mapping):
            raise BriefError(
                f"design_review.anchor_system_consequences[{index}] must be an object"
            )
        prefix = f"design_review.anchor_system_consequences[{index}]"
        consequence_anchor_id = _stable_id(
            consequence.get("anchor_id"), f"{prefix}.anchor_id"
        )
        if consequence_anchor_id != hero_anchor_id:
            raise BriefError(
                f"{prefix}.anchor_id must equal design_review.hero_anchor_id"
            )
        system = _nonempty(consequence.get("system"), f"{prefix}.system")
        if system not in _ANCHOR_CONSEQUENCE_SYSTEMS:
            raise BriefError(
                f"{prefix}.system must be one of "
                + ", ".join(sorted(_ANCHOR_CONSEQUENCE_SYSTEMS))
            )
        if system in consequence_systems:
            raise BriefError(
                "design_review.anchor_system_consequences must use distinct systems; "
                "repetition or scaling alone is not a second consequence"
            )
        consequence_systems.add(system)
        _review_evidence(
            consequence.get("effect"),
            f"{prefix}.effect",
            min_alnum=12,
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
            raise BriefError(f"design_review.{field} must be true before a 3MF release")
    _review_evidence(
        design_review.get("structural_thesis"),
        "design_review.structural_thesis",
        min_alnum=20,
    )
    _review_evidence(
        design_review.get("finish_target_note"),
        "design_review.finish_target_note",
        min_alnum=20,
    )
    _review_evidence(
        design_review.get("reviewer_note"),
        "design_review.reviewer_note",
        min_alnum=32,
    )
    reference_checks = design_review.get("quality_reference_checks")
    if not isinstance(reference_checks, list):
        raise BriefError("design_review.quality_reference_checks must be a list")
    checked_reference_ids: list[str] = []
    for index, check in enumerate(reference_checks):
        if not isinstance(check, Mapping):
            raise BriefError(
                f"design_review.quality_reference_checks[{index}] must be an object"
            )
        prefix = f"design_review.quality_reference_checks[{index}]"
        reference_id = _stable_id(
            check.get("reference_id"), f"{prefix}.reference_id"
        )
        if reference_id in checked_reference_ids:
            raise BriefError(f"duplicate quality-reference check: {reference_id}")
        checked_reference_ids.append(reference_id)
        if check.get("met") is not True:
            raise BriefError(f"{prefix}.met must be true")
        _review_evidence(
            check.get("comparison_note"),
            f"{prefix}.comparison_note",
            min_alnum=20,
        )
    if set(checked_reference_ids) != set(quality_reference_ids) or len(
        checked_reference_ids
    ) != len(quality_reference_ids):
        raise BriefError(
            "design_review.quality_reference_checks must cover every quality_reference "
            "exactly once and no other reference"
        )

    provenance = brief.get("provenance")
    if not isinstance(provenance, Mapping):
        raise BriefError("design brief provenance must be an object")
    for field in (
        "artwork_license",
        "brand_mark_license",
        "film_or_history_permissions",
        "notes",
    ):
        _provenance_statement(
            provenance.get(field),
            f"provenance.{field}",
            min_alnum=8 if field == "notes" else 3,
        )

    candidate_value = _nonempty(generation.get("candidate_path"), "generation.candidate_path")
    candidate = _resolve_inside(brief_path.parent, candidate_value, "generation.candidate_path")
    candidate_hash = _nonempty(generation.get("candidate_sha256"), "generation.candidate_sha256")
    if not re.fullmatch(r"[0-9a-fA-F]{64}", candidate_hash):
        raise BriefError("generation.candidate_sha256 must be a 64-character hexadecimal digest")
    actual_candidate_hash = sha256_file(candidate)
    if actual_candidate_hash.lower() != candidate_hash.lower():
        raise BriefError("generation.candidate_sha256 does not match the approved raster")
    if actual_candidate_hash.lower() != reviewed_candidate_hash.lower():
        raise BriefError(
            "design_review.reviewed_candidate_sha256 does not match the approved raster; "
            "repeat the full-resolution composition review for this exact candidate"
        )
    actual_source_hash = sha256_file(config.source_path)
    if actual_source_hash.lower() != actual_candidate_hash.lower():
        raise BriefError(
            "job source_art differs from generation.candidate_path; copy the approved raster "
            "into the job before running the 3MF bridge"
        )
    declared_source_hash = config.source_sha256
    if declared_source_hash and declared_source_hash.lower() != actual_source_hash.lower():
        raise BriefError("job source_sha256 does not match the approved raster")

    metadata = config.metadata if isinstance(config.metadata, Mapping) else {}
    metadata_display = metadata.get("display_text")
    if (
        not isinstance(metadata_display, list)
        or len(metadata_display) < 2
        or not all(isinstance(item, str) and item.strip() for item in metadata_display)
    ):
        raise BriefError(
            "job metadata.display_text must bind the complete ordered closed text set"
        )
    try:
        metadata_focal = canonical_focal_display(
            metadata_display[0], field="metadata.display_text[0]"
        )
        metadata_aperture = canonical_aperture_display(
            metadata_display[1], field="metadata.display_text[1]"
        )
    except BriefError as exc:
        raise BriefError(f"job metadata.display_text is invalid: {exc}") from exc
    if metadata_focal != focal_display or metadata_aperture != aperture_display:
        raise BriefError("job metadata.display_text disagrees with the design brief")
    if metadata_display != display:
        raise BriefError(
            "job metadata.display_text disagrees with the design brief complete closed text set"
        )

    # A fitted release must preserve the grouped physical intake as explicit
    # job data. Parser defaults are useful for drafts, but they cannot prove
    # that diameter, foam, and the default-on rib decision were actually
    # recorded before production. ``friction_ribs_explicit=false`` is a valid
    # recorded choice: it means the user accepted the documented default-on
    # strategy, not that the field may be omitted.
    physical_intake_checked = False
    if config.measured_diameter_mm is not None:
        raw_fit = config.raw.get("fit")
        if not isinstance(raw_fit, Mapping):
            raise BriefError(
                "fitted 3MF release requires an explicit [fit] physical-intake section"
            )
        required_intake_fields = {
            "foam_liner_status",
            "friction_ribs_enabled",
            "friction_ribs_explicit",
        }
        missing_intake = sorted(required_intake_fields.difference(raw_fit))
        if missing_intake:
            raise BriefError(
                "fitted 3MF release is missing recorded physical-intake fields: "
                + ", ".join(missing_intake)
            )
        physical_intake_checked = True

    # Bind every geometry-driving fit value. Null diameter fields remain the
    # explicit escape hatch for a brief shared by several size variants; all
    # other body, liner, rib, nozzle, and adapter fields are strict snapshots.
    physical_fit = brief.get("physical_fit")
    if not isinstance(physical_fit, Mapping):
        raise BriefError("design brief physical_fit must be an object")
    active_fit = config.fit.public()
    required_physical_fields = {
        "measured_diameter_mm",
        "face_target_mm",
        *active_fit,
        "nozzle_mm",
        "adapter_nominal_ring_mm",
        "adapter_radial_wall_mm",
        "adapter_derived_mating_diameter_mm",
    }
    missing_physical = sorted(required_physical_fields.difference(physical_fit))
    if missing_physical:
        raise BriefError(
            "design brief physical_fit is missing required fields: "
            + ", ".join(missing_physical)
        )
    active_physical: dict[str, Any] = {
        "measured_diameter_mm": config.measured_diameter_mm,
        "face_target_mm": config.face_diameter_mm,
        **active_fit,
        "nozzle_mm": config.nozzle_mm,
        "adapter_nominal_ring_mm": metadata.get("adapter_nominal_ring_mm"),
        "adapter_radial_wall_mm": metadata.get("adapter_radial_wall_mm"),
        "adapter_derived_mating_diameter_mm": metadata.get(
            "adapter_derived_mating_diameter_mm"
        ),
    }
    checked_physical_fields: list[str] = []
    adapter_fields = {
        "adapter_nominal_ring_mm",
        "adapter_radial_wall_mm",
        "adapter_derived_mating_diameter_mm",
    }
    adapter_values = [physical_fit[field] for field in sorted(adapter_fields)]
    if any(value is None for value in adapter_values) and not all(
        value is None for value in adapter_values
    ):
        raise BriefError("physical_fit adapter fields must be all null or all numeric")
    # Optional values are nullable only when the active job also resolves them
    # to null. Shared artwork may additionally wildcard its size identity and
    # the all-or-none adapter triplet so one approved face can drive several
    # ring diameters. Liner, rib profile, nozzle, and process values remain
    # strict snapshots.
    nullable_fields = {
        field for field, actual in active_physical.items() if actual is None
    }
    if binding_mode == "shared_artwork_variants":
        nullable_fields.update(
            {"measured_diameter_mm", "face_target_mm", *adapter_fields}
        )
    for field in sorted(required_physical_fields):
        declared = physical_fit[field]
        if declared is None and field not in nullable_fields:
            raise BriefError(
                f"physical_fit.{field} cannot be null in {binding_mode} mode"
            )
        if field not in active_physical:
            continue
        if declared is None:
            continue
        _same_declared_value(active_physical[field], declared, field)
        checked_physical_fields.append(field)

    job_binding = brief.get("job_binding")
    if not isinstance(job_binding, Mapping):
        raise BriefError("design brief job_binding must be an object")
    declared_identity = _nonempty(
        job_binding.get("metadata_lens_identity"),
        "job_binding.metadata_lens_identity",
    )
    metadata_identity = _nonempty(
        metadata.get("lens_identity"), "job metadata.lens_identity"
    )
    if metadata_identity != declared_identity:
        raise BriefError("job metadata.lens_identity disagrees with the approved design brief")
    _validate_metadata_identity_binding(
        metadata_identity,
        brand=brand,
        model=model,
        focal_display=focal_display,
        aperture=aperture_display,
    )
    _same_job_snapshot(
        config.circle.public(),
        job_binding.get("circle"),
        "job_binding.circle",
    )
    _same_job_snapshot(
        {item.name: item.public() for item in config.palette},
        job_binding.get("palette"),
        "job_binding.palette",
    )
    _same_job_snapshot(
        _artwork_process_snapshot(config),
        job_binding.get("artwork_process"),
        "job_binding.artwork_process",
    )
    binding_checked = True

    return {
        "status": "passed",
        "path": os.fspath(brief_path),
        "sha256": sha256_file(brief_path),
        "candidate_path": os.fspath(candidate),
        "candidate_sha256": actual_candidate_hash,
        "identity": {
            "brand": brand,
            "model": model,
            "focal_length_mm": focal,
            "focal_length_display": focal_display,
            "maximum_aperture": aperture,
            "maximum_aperture_display": aperture_display,
        },
        "display_text": list(display),
        "binding_mode": binding_mode,
        "anchor_count": len(anchors),
        "culture_anchor_count": culture_count,
        "hero_anchor_index": hero_anchor_index,
        "hero_anchor_id": hero_anchor_id,
        "anchor_consequence_systems": sorted(consequence_systems),
        "quality_reference_count": len(quality_reference_ids),
        "completion_quality_checked": True,
        "physical_fit_checked": checked_physical_fields,
        "job_identity_binding_checked": binding_checked,
        "job_circle_binding_checked": True,
        "job_palette_binding_checked": True,
        "job_artwork_process_binding_checked": True,
        "physical_intake_fields_checked": physical_intake_checked,
    }


def _relative(base: Path, target: Path) -> str:
    try:
        return Path(os.path.relpath(target.resolve(), base.resolve())).as_posix()
    except (OSError, ValueError):
        return str(target.resolve())


def _circle_suggestion(source: Path) -> dict[str, Any]:
    """Suggest a circle only when alpha provides an unambiguous bbox."""

    try:
        from PIL import Image

        with Image.open(source) as image:
            width, height = image.size
            if "A" not in image.getbands():
                return {
                    "center_px": None,
                    "radius_px": None,
                    "method": "manual_required_opaque_source",
                    "source_size_px": [width, height],
                }
            alpha = image.getchannel("A")
            bbox = alpha.getbbox()
            if bbox is None:
                return {
                    "center_px": None,
                    "radius_px": None,
                    "method": "manual_required_empty_alpha",
                    "source_size_px": [width, height],
                }
            left, top, right, bottom = bbox
            diameter = max(right - left, bottom - top)
            return {
                "center_px": [(left + right - 1) / 2.0, (top + bottom - 1) / 2.0],
                "radius_px": diameter / 2.0,
                "method": "alpha_bbox_suggestion_review_required",
                "source_size_px": [width, height],
                "alpha_bbox_px": [left, top, right, bottom],
            }
    except Exception as exc:  # pragma: no cover - provider/user file errors are reported in scaffold
        return {"center_px": None, "radius_px": None, "method": f"manual_required:{type(exc).__name__}"}


def create_design_brief_scaffold(
    config: PipelineConfig,
    output_path: str | Path | None = None,
    *,
    brand: str,
    model: str,
    focal_length_mm: float,
    maximum_aperture: str,
    focal_length_display: str | None = None,
    maximum_aperture_display: str | None = None,
    mount_or_revision: str | None = None,
    era: str | None = None,
    provider: str | None = None,
    prompt_path: str | Path | None = None,
    anchor_sources: list[str] | None = None,
    approved: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Write a review-required ``design-brief.json`` beside a job.

    The command computes the raster and prompt hashes when files are present,
    but leaves source-backed anchor and licence fields explicit. The public CLI
    always passes ``approved=False``; the argument remains available to
    controlled programmatic callers, which must still replace every placeholder
    before the strict validator can pass.
    """

    brand_value = _nonempty(brand, "--brand")
    model_value = _nonempty(model, "--model")
    focal = _positive_number(focal_length_mm, "--focal-length")
    aperture, aperture_from_argument = _aperture_components(
        maximum_aperture, "--maximum-aperture"
    )
    if maximum_aperture_display is None:
        aperture_display = aperture_from_argument
    else:
        display_aperture_anchor, aperture_display = _aperture_components(
            maximum_aperture_display,
            "--maximum-aperture-display",
        )
        if display_aperture_anchor != aperture:
            raise BriefError(
                "--maximum-aperture-display must start at --maximum-aperture"
            )
        if aperture_from_argument != aperture and aperture_from_argument != aperture_display:
            raise BriefError(
                "--maximum-aperture range disagrees with --maximum-aperture-display"
            )
    display_token = focal_length_display.strip() if focal_length_display is not None else f"{focal:g}"
    display_focal = canonical_focal_display(
        display_token,
        field="--focal-length-display",
    )
    if not math.isclose(float(display_focal.split("-", 1)[0]), focal, abs_tol=1e-9):
        raise BriefError("--focal-length-display must start at --focal-length")

    metadata = config.metadata if isinstance(config.metadata, Mapping) else {}
    metadata_identity = _nonempty(
        metadata.get("lens_identity"),
        "job metadata.lens_identity (create the job with lens-cap init --lens-identity)",
    )
    metadata_display_raw = metadata.get("display_text")
    if (
        not isinstance(metadata_display_raw, list)
        or len(metadata_display_raw) < 2
        or not all(isinstance(item, str) and item.strip() for item in metadata_display_raw)
    ):
        raise BriefError(
            "job metadata.display_text must be a non-empty ordered text list; "
            "create the job with lens-cap init --display-text"
        )
    metadata_display = list(metadata_display_raw)
    if canonical_focal_display(
        metadata_display[0], field="job metadata.display_text[0]"
    ) != display_focal:
        raise BriefError(
            "job metadata.display_text[0] disagrees with --focal-length/--focal-length-display"
        )
    if canonical_aperture_display(
        metadata_display[1], field="job metadata.display_text[1]"
    ) != aperture_display:
        raise BriefError(
            "job metadata.display_text[1] disagrees with the complete "
            "--maximum-aperture-display"
        )
    _validate_metadata_identity_binding(
        metadata_identity,
        brand=brand_value,
        model=model_value,
        focal_display=display_focal,
        aperture=aperture_display,
    )
    provider_value = _nonempty(provider, "--provider")

    brief_path = (
        Path(output_path).expanduser()
        if output_path is not None
        else config.config_path.parent / "design-brief.json"
    )
    if not brief_path.is_absolute():
        brief_path = (Path.cwd() / brief_path).resolve()
    else:
        brief_path = brief_path.resolve()
    if brief_path.exists() and not force:
        raise BriefError(f"refusing to overwrite existing design brief: {brief_path}; use --force")

    source_rel = _relative(brief_path.parent, config.source_path)
    source_hash = sha256_file(config.source_path)
    prompt_rel: str | None = None
    prompt_hash: str | None = None
    if prompt_path is not None:
        prompt = Path(prompt_path).expanduser()
        if not prompt.is_absolute():
            prompt = (Path.cwd() / prompt).resolve()
        else:
            prompt = prompt.resolve()
        if prompt.is_file():
            prompt_rel = _relative(brief_path.parent, prompt)
            prompt_hash = sha256_file(prompt)

    anchors: list[dict[str, Any]] = []
    for anchor_number, source in enumerate(anchor_sources or [], start=1):
        source_text = _nonempty(source, "--anchor-source")
        anchors.append(
            {
                "anchor_id": f"anchor-{anchor_number}",
                "claim_kind": "manufacturer_culture",
                "subject_scope": f"{brand_value} {model_value}",
                "identity_binding": {
                    "brand": brand_value,
                    "scope": "brand",
                },
                "evidence_state": "to_verify",
                "source_role": "proof",
                "source": source_text,
                "summary": "REPLACE with a source-backed manufacturer, system, history, or qualified rehouse fact.",
                "render_role": "visual_metaphor",
                "anchor_context": "REPLACE with the exact scope and uncertainty.",
                "motif_commitment": "REPLACE with structural for the hero anchor or supporting",
                "anchor_visual_motif": "REPLACE with original geometry; do not copy a film still or logo.",
                "recognition_cue": "REPLACE with the cue that remains when lore words are hidden.",
                "qualifier": "REPLACE if the association is family-level, folklore, or contested.",
            }
        )
    if not anchors:
        anchors = [
            {
                "anchor_id": "anchor-1",
                "claim_kind": "manufacturer_culture",
                "subject_scope": f"{brand_value} {model_value}",
                "identity_binding": {
                    "brand": brand_value,
                    "scope": "brand",
                },
                "evidence_state": "to_verify",
                "source_role": "proof",
                "source": "REPLACE_WITH_SOURCE_URL_OR_ARCHIVE_ID",
                "summary": "REPLACE with a source-backed manufacturer, system, history, or qualified rehouse fact.",
                "render_role": "visual_metaphor",
                "anchor_context": "REPLACE with the exact scope and uncertainty.",
                "motif_commitment": "REPLACE with structural for the hero anchor or supporting",
                "anchor_visual_motif": "REPLACE with original geometry; do not copy a film still or logo.",
                "recognition_cue": "REPLACE with the cue that remains when lore words are hidden.",
                "qualifier": "REPLACE if the association is family-level, folklore, or contested.",
            }
        ]

    fit = config.fit.public()
    physical_fit = {
        "measured_diameter_mm": config.measured_diameter_mm,
        "face_target_mm": config.face_diameter_mm,
        **fit,
        "nozzle_mm": config.nozzle_mm,
        "adapter_nominal_ring_mm": metadata.get("adapter_nominal_ring_mm"),
        "adapter_radial_wall_mm": metadata.get("adapter_radial_wall_mm"),
        "adapter_derived_mating_diameter_mm": metadata.get(
            "adapter_derived_mating_diameter_mm"
        ),
    }
    brief: dict[str, Any] = {
        "schema_version": 2,
        "binding_mode": "single_job",
        "job_slug": config.job_slug,
        "lens_identity": {
            "brand": brand_value,
            "model": model_value,
            "focal_length_mm": focal,
            "focal_length_display": focal_length_display.strip() if focal_length_display is not None else None,
            "maximum_aperture": aperture,
            "maximum_aperture_display": (
                aperture_display
                if aperture_display != aperture or maximum_aperture_display is not None
                else None
            ),
            "mount_or_revision": mount_or_revision,
            "era": era,
        },
        # The job created at intake owns the complete closed text set.  Do not
        # silently discard maker/model/system lines when scaffolding approval.
        "display_text": metadata_display,
        "allowed_text": list(metadata_display),
        "allowed_marks": [],
        "forbidden_legacy_tokens": [],
        "object_mode": "typographic_medallion",
        "narrative_mode": "balanced",
        # Handoff-init belongs to the production bridge.  Every loaded job has
        # a reviewed face diameter even when a fitted cap still lacks its
        # measured mating diameter, so a face-only relief remains printable.
        "production_target": "printable_front",
        "visual_direction": {
            "aspect_ratio": "1:1",
            "palette": ["black", "charcoal", "gray", "ivory"],
            "medium": "linocut or screen print",
            "dominant_hierarchy": ["focal_length", "maximum_aperture"],
            "omissions": ["lens barrel", "glass", "iris blades", "photographic clutter", "pointillism"],
        },
        "anchors": anchors,
        "approved_references": [],
        "generation": {
            "provider": provider_value,
            "mode": "generate",
            "model": None,
            "model_version": None,
            "prompt": prompt_rel,
            "prompt_sha256": prompt_hash,
            "reference_hashes": [],
            "candidate_path": source_rel,
            "candidate_sha256": source_hash,
            "approved": bool(approved),
            "approval_note": None,
        },
        "design_review": {
            "reviewed_candidate_sha256": None,
            "hero_anchor_index": None,
            "hero_anchor_id": None,
            "anchor_system_consequences": [],
            "full_resolution_reviewed": False,
            "text_off_anchor_recognizable": False,
            "identity_swap_requires_redesign": False,
            "anchor_drives_primary_composition": False,
            "composition_resolved": False,
            "visual_grammar_consistent": False,
            "finish_target_met": False,
            "production_reduction_preserves_authorship": False,
            "structural_thesis": None,
            "finish_target_note": None,
            "quality_reference_checks": [],
            "reviewer_note": None,
        },
        "circle_suggestion": _circle_suggestion(config.source_path),
        "physical_fit": physical_fit,
        "job_binding": {
            "metadata_lens_identity": metadata_identity,
            "circle": config.circle.public(),
            "palette": {item.name: item.public() for item in config.palette},
            "artwork_process": _artwork_process_snapshot(config),
        },
        "provenance": {
            "artwork_license": "REPLACE with provider/author/licence terms",
            "brand_mark_license": (
                "REPLACE with trademark/mark permission, or a semantic N/A such as "
                "'not applicable — no third-party mark rendered'"
            ),
            "film_or_history_permissions": "REPLACE with permissions and a note separating verified fact from visual inspiration",
            "notes": "REPLACE with review notes separating third-party terms from the Apache-2.0 code licence.",
        },
    }
    brief_path.parent.mkdir(parents=True, exist_ok=True)
    brief_path.write_text(json.dumps(brief, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # Always show the explicit brief path in follow-up commands.  This is
    # redundant for the conventional adjacent/ancestor location, but it is
    # essential when the caller used ``--brief``: neither handoff-check nor the
    # release bridge should silently discover a different nearby brief.
    brief_cli = os.fspath(brief_path)
    return {
        "status": "passed",
        "path": os.fspath(brief_path),
        "approved": bool(approved),
        "candidate_path": os.fspath(config.source_path),
        "candidate_sha256": source_hash,
        "circle_suggestion": brief["circle_suggestion"],
        "next": [
            f"Confirm generation.provider={provider_value!r} identifies the provider used for this candidate.",
            "Confirm display_text and allowed_text preserve the complete job metadata.display_text closed set, including secondary text.",
            "Review the job TOML circle, complete palette, and artwork-process settings (grid, safe border, prefilter, cleanup, and assembly mode); if they change, refresh job_binding before approval.",
            "Verify each anchor source, replace every REPLACE field, and change evidence_state from to_verify to a sourced/verified value.",
            "Complete provenance.artwork_license, provenance.brand_mark_license, provenance.film_or_history_permissions, and provenance.notes.",
            "Review the circular composition and copy the reviewed circle_suggestion center/radius into [circle] for opaque artwork.",
            "Choose a structural hero_anchor_index, copy its stable anchor_id into hero_anchor_id, and bind at least two observable anchor_system_consequences to that same anchor_id in different systems; repetition or scaling alone does not count.",
            "Pass every candidate-bound design_review check: copy the exact candidate hash only after full-resolution review, complete the structural thesis and observable finish target, cover every quality_reference exactly once, then pass text-off recognition, nearest-neighbour identity swap, anchor-system integration, whole-field resolution, visual grammar, finish, and printable-reduction integrity.",
            "Set generation.approved=true only after a human approves the exact text, hierarchy, sources, licences, circle, and design_review.",
            f"Run lens-cap handoff-check JOB.toml --brief {brief_cli} --json.",
            f"Run ./bin/lens-cap-3mf JOB.toml --brief {brief_cli} --force --json after the brief gate passes.",
        ],
    }

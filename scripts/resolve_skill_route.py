#!/usr/bin/env python3
"""Resolve a natural-language request against the repository lens-cap policy.

This is a small, dependency-free host shim.  Codex/Claude normally decides
whether to load a Skill from its description, but older or project-specific
routers may only consume a machine-readable manifest.  The shim evaluates the
same conjunction documented in ``skills/manifest.json``:

* a named-lens cue and a cap/front/production intent signal (verbs are one
  signal, but terse noun phrases such as “lens cap artwork” are valid);
* a cap/front/relief/printable surface cue; and
* an optical/repair/product-photo exclusion, unless the request also names an
  explicit cap surface.

It does not generate art or invoke CAD.  A matched production request is
reported as the ordered ``lens-cap-imagegen`` -> ``lens-cap-production`` route
unless ``--approved-artwork`` is supplied, in which case production is the
first required stage.  The result is intentionally advisory: the host still
owns the conversational decision and should preserve the exclusive route.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "skills" / "manifest.json"


class RouteError(ValueError):
    """Raised when the routing manifest or request cannot be evaluated."""


_FOCAL_RE = re.compile(
    r"(?<![\w.])[0-9]+(?:[.][0-9]+)?"
    r"(?:\s*(?:-|–|—|~|至|to)\s*[0-9]+(?:[.][0-9]+)?)?"
    r"\s*(?:mm|毫米)(?![\w.])",
    re.I,
)
# Do not use ``\w`` for the left boundary: Chinese users commonly type a
# compact token such as ``2870F2.8`` with the aperture directly after the
# focal range.  A preceding Latin letter is the meaningful false-positive
# boundary (``XF2.8``), while a digit/CJK character is valid lens notation.
_APERTURE_RE = re.compile(
    r"(?<![A-Za-z])f\s*/?\s*[0-9]+(?:[.][0-9]+)?(?![A-Za-z0-9.])", re.I
)
_LENS_NOUN_RE = re.compile(r"\blens(?:es)?\b|镜头", re.I)
_LATIN_TOKEN_RE = re.compile(
    r"(?<![A-Za-z])[A-Za-z][A-Za-z0-9]*(?:[-/.][A-Za-z0-9]+)*(?![A-Za-z])"
)
_CJK_TOKEN_RE = re.compile(r"[\u3400-\u9fff]{2,}")
_COMPACT_ID_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:[A-Za-z][A-Za-z0-9-]{1,}|[\u3400-\u9fff]{2,})"
    r"\s*[-/]?\s*[0-9]{2,}(?![A-Za-z0-9])",
    re.I,
)

# Generic words are not identity evidence by themselves.  The list is small
# and deliberately conservative: a host can pass ``--named-lens`` when it has
# identified a lens from an attached image or a catalog entry that is not
# represented in text.
_IDENTITY_STOPWORDS = {
    # English request grammar and generic object/design vocabulary.  A bare
    # focal/aperture specification must not become a "named lens" merely
    # because words such as *please* or *you* were present in the sentence.
    "please",
    "can",
    "you",
    "could",
    "would",
    "should",
    "will",
    "the",
    "this",
    "that",
    "these",
    "those",
    "my",
    "your",
    "our",
    "their",
    "attached",
    "identified",
    "generic",
    "old",
    "new",
    "vintage",
    "retro",
    "round",
    "roundel",
    "style",
    "styled",
    "inspired",
    "use",
    "using",
    "want",
    "need",
    "give",
    "get",
    "show",
    "turn",
    "into",
    "from",
    "with",
    "only",
    "same",
    "task",
    "history",
    "context",
    "without",
    "any",
    "one",
    "some",
    "an",
    "as",
    "on",
    "be",
    "it",
    "me",
    "then",
    "last",
    "camera",
    "cameras",
    "lens",
    "lenses",
    "design",
    "generate",
    "create",
    "make",
    "export",
    "print",
    "test",
    "try",
    "printable",
    "model",
    "image",
    "artwork",
    "graphic",
    "logo",
    "front",
    "surface",
    "relief",
    "cap",
    "lenscap",
    "circular",
    "circle",
    "pattern",
    "badge",
    "medallion",
    "cover",
    "art",
    "to",
    "a",
    "of",
    "is",
    "for",
    "first",
    "read",
    "output",
    "and",
    "mm",
    "f",
    "mf",
    "stl",
    "scad",
    "3mf",
    "镜头",
    "设计",
    "生成",
    "制作",
    "做",
    "图像",
    "图案",
    "图稿",
    "正面",
    "浮雕",
    "圆形",
    "模型",
    "可打印",
}

_CJK_IDENTITY_STOPWORDS = (
    # Function words/pronouns are intentionally listed before short nouns so
    # longest phrases are removed first.  This keeps “请把这个镜头做成…” from
    # being misread as a model named “把这个”.
    "没有任何历史上下文",
    "没有历史上下文",
    "请问",
    "我要",
    "我想要",
    "我想",
    "只做",
    "只要",
    "只是",
    "仅做",
    "仅要",
    "不要",
    "我",
    "你",
    "您",
    "请把",
    "帮我",
    "给我",
    "给它",
    "能不能",
    "可以吗",
    "可以",
    "吗",
    "这颗",
    "这支",
    "这个",
    "那个",
    "一颗",
    "一支",
    "一个",
    "某品牌",
    "某颗",
    "我的",
    "你的",
    "我们的",
    "请",
    "为",
    "把",
    "用",
    "将",
    "做成",
    "做个",
    "做一个",
    "作为",
    "用于",
    "想要",
    "想",
    "需要",
    "最后",
    "并且",
    "并",
    "然后",
    "给",
    "输出",
    "导出",
    "打印",
    "测试",
    "尝试",
    "设计",
    "生成",
    "制作",
    "修改",
    "做",
    "模型",
    "图像",
    "图稿",
    "图案",
    "正面",
    "浮雕",
    "徽章",
    "圆形",
    "毫米",
    "没有",
    "任何",
    "任意",
    "任一",
    "历史",
    "上下文",
    "镜头",
    "艺术",
    "镜头盖",
    "镜头帽",
    "镜头罩",
    "且",
    "该",
    "这",
    "那",
    "它",
    "颗",
    "支",
    "个",
    "可",
    "圆",
    "普通",
    "通用",
    "类似",
    "图",
)


def _normalise(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).casefold()
    return (
        value.replace("−", "-")
        .replace("–", "-")
        .replace("—", "-")
        .replace("‑", "-")
        .replace("‒", "-")
        .replace("－", "-")
        .replace("~", "-")
        .replace("毫米", "mm")
    )


def _load_policy(path: Path = MANIFEST) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RouteError(f"cannot read routing manifest {path}: {exc}") from exc
    try:
        return payload["routing_policy"]["lens_cap_intent"]
    except (KeyError, TypeError) as exc:
        raise RouteError("manifest is missing routing_policy.lens_cap_intent") from exc


def _terms(policy: Mapping[str, Any], section: str) -> tuple[str, ...]:
    value = policy.get(section, ())
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise RouteError(f"routing policy {section!r} must be a list of non-empty strings")
    return tuple(_normalise(item.strip()) for item in value)


def _contains(text: str, values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(value for value in values if value and value in text)


def _strip_cjk_stopwords(candidate: str) -> str:
    residue = candidate
    for stopword in sorted(_CJK_IDENTITY_STOPWORDS, key=len, reverse=True):
        residue = residue.replace(stopword, "")
    return residue


def _heuristic_named_lens(text: str) -> bool:
    """Return a conservative textual named-lens signal.

    A focal/aperture specification alone is not enough; require a non-generic
    Latin or CJK identity token as well.  Attached-image/catalog identities can
    bypass this heuristic with the explicit ``named_lens`` argument.
    """

    candidates = [*_LATIN_TOKEN_RE.findall(text), *_CJK_TOKEN_RE.findall(text)]
    has_lens_cue = bool(
        _FOCAL_RE.search(text) or _APERTURE_RE.search(text) or _LENS_NOUN_RE.search(text)
    )
    if not has_lens_cue:
        # Compact catalog shorthand such as “适马2870” can omit both “镜头”
        # and units.  Permit that only when a candidate itself carries a
        # strong model signal (digits/hyphen or a non-grammatical CJK residue);
        # ordinary prose like “design a generic round logo” stays out.
        strong_compact_cue = any(
            any(character.isdigit() for character in candidate)
            or ("-" in candidate and any(character.isalpha() for character in candidate))
            or (
                re.search(r"[\u3400-\u9fff]", candidate)
                and any(
                    residue
                    for residue in (
                        _strip_cjk_stopwords(candidate),
                    )
                    if len(residue) >= 2
                )
            )
            for candidate in candidates
        )
        if not strong_compact_cue:
            for match in _COMPACT_ID_RE.finditer(text):
                prefix = match.group(0).split(match.group(0)[-1], 1)[0]
                # Do not treat a bare F-number as a model shorthand.
                if re.fullmatch(r"f\s*[0-9]+", prefix.strip(), re.I):
                    continue
                token = re.sub(r"[0-9\s\-/]+$", "", match.group(0)).strip()
                if token.casefold() not in _IDENTITY_STOPWORDS and len(token) >= 2:
                    strong_compact_cue = True
                    break
        if not strong_compact_cue:
            return False
    for candidate in candidates:
        folded = candidate.casefold()
        if folded in _IDENTITY_STOPWORDS:
            continue
        # Focal/aperture tokens are specifications, not a named lens.  This
        # guard prevents a bare request such as “design a 50mm F1.4 lens
        # graphic” from being treated as an identified model.
        if re.fullmatch(r"f[0-9]+(?:\.[0-9]+)?", folded) or re.fullmatch(
            r"[0-9]+(?:[-.][0-9]+)*", folded
        ):
            continue
        if folded.startswith("f") and folded[1:].replace(".", "").isdigit():
            continue
        if re.search(r"[\u3400-\u9fff]", candidate):
            residue = _strip_cjk_stopwords(candidate)
            # One remaining character is usually a grammatical classifier or
            # adjective (e.g. “老镜头”); require at least two CJK characters
            # before treating the residue as a manufacturer/model cue.
            if len(residue) >= 2:
                return True
        # Pure unit/number-like fragments and one-letter grammar tokens do not
        # carry a useful lens identity.
        elif len(candidate) >= 2 and not candidate.isdigit():
            return True
    return False


def resolve_request(
    text: str,
    *,
    named_lens: bool | None = None,
    approved_artwork: bool = False,
    policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve one request and return a stable JSON-compatible report."""

    if not isinstance(text, str) or not text.strip():
        raise RouteError("request text must be non-empty")
    policy = policy or _load_policy()
    semantic = policy.get("semantic_match")
    if not isinstance(semantic, Mapping):
        raise RouteError("manifest semantic_match is missing")
    source = _normalise(text)
    verbs = _terms(semantic, "verbs")
    surfaces = _terms(semantic, "surface_terms")
    exclusions = _terms(semantic, "exclude_without_surface_intent")
    explicit = _terms(semantic, "explicit_cap_surface_terms")
    verb_hits = _contains(source, verbs)
    surface_hits = _contains(source, surfaces)
    exclusion_hits = _contains(source, exclusions)
    explicit_hits = _contains(source, explicit)
    named = _heuristic_named_lens(source) if named_lens is None else bool(named_lens)

    production_terms = (
        "3mf",
        "stl",
        "scad",
        "cad",
        "printable",
        "print",
        "relief model",
        "lens relief",
        "fitted",
        "fit",
        "printer",
        "build",
        "slice",
        "run",
        "invoke",
        "可打印",
        "打印",
        "模型",
        "浮雕模型",
        "建模",
        "切片",
        "卡合",
        "适配",
    )
    production = any(term in source for term in production_terms)
    # A named lens plus an explicit cap/front deliverable is already a clear
    # request even when the user writes a terse noun phrase (“Zeiss … lens cap
    # artwork”).  For less explicit “circular image” wording, a focal/aperture
    # token supplies the missing action context.  This keeps the shim useful
    # for real chat shorthand without allowing an unnamed generic lens through.
    numeric_identity = bool(_FOCAL_RE.search(source) or _APERTURE_RE.search(source))
    intent_evidence = bool(named or verb_hits or explicit_hits or production or numeric_identity)
    matched = bool(named and surface_hits and intent_evidence and (not exclusion_hits or explicit_hits))
    if matched and production:
        route = (
            ["lens-cap-production"]
            if approved_artwork
            else ["lens-cap-imagegen", "lens-cap-production"]
        )
    elif matched:
        route = ["lens-cap-imagegen"]
    else:
        route = []
    reasons: list[str] = []
    if not named:
        reasons.append("named_lens_required")
    if not verb_hits and not matched:
        reasons.append("design_verb_missing")
    if not surface_hits:
        reasons.append("cap_surface_term_missing")
    if exclusion_hits and not explicit_hits:
        reasons.append("optical_repair_or_product_exclusion")
    if matched:
        reasons.append("named_lens_cap_surface_intent")
    return {
        "status": "matched" if matched else "not_matched",
        "route": route,
        "named_lens_detected": named,
        "production_intent": production,
        "matched_terms": {
            "verbs": list(verb_hits),
            "surface": list(surface_hits),
            "explicit_cap_surface": list(explicit_hits),
            "exclusions": list(exclusion_hits),
        },
        "reasons": reasons,
        "generic_parallel_skills": [],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text", nargs="?", help="request text (otherwise read stdin)")
    parser.add_argument(
        "--named-lens",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="override named-lens detection",
    )
    parser.add_argument(
        "--approved-artwork",
        action="store_true",
        help="route production-only after an approved handoff",
    )
    parser.add_argument("--manifest", type=Path, default=MANIFEST, help="routing manifest path")
    parser.add_argument("--json", action="store_true", help="emit JSON (default; retained for symmetry)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    text = args.text if args.text is not None else sys.stdin.read()
    try:
        report = resolve_request(
            text,
            named_lens=args.named_lens,
            approved_artwork=args.approved_artwork,
            policy=_load_policy(args.manifest.expanduser().resolve()),
        )
    except RouteError as exc:
        print(f"lens-cap route: error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "matched" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

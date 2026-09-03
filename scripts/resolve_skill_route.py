#!/usr/bin/env python3
"""Resolve a natural-language request against the repository lens-cap policy.

This is a small, dependency-free host shim.  Codex/Claude normally decides
whether to load a Skill from its description, but older or project-specific
routers may only consume a machine-readable manifest.  The shim evaluates the
same conjunction documented in ``skills/manifest.json``:

* a positively identified cap/front-cap object (which may enter identity
  intake), or a credible named-lens cue for the controlled cap-noun-omission
  artwork forms;
* an explicit cap artwork/front-face or physical cap/model production cue; and
* an optical/repair/product-photo exclusion, unless the request also names an
  explicit cap surface.

It does not generate art or invoke CAD.  A matched production request is
reported as the ordered ``lens-cap-imagegen`` -> ``lens-cap-production`` route
unless ``--approved-artwork`` is supplied, in which case production is the
first required stage.  The result is intentionally advisory: the host still
owns the conversational decision and should preserve the exclusive route.
Terse try/test or production-format continuations without a cap/front noun are
accepted only when the caller supplies ``--lens-cap-context`` for an already
established task and the new turn still carries lens-identity evidence.
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
    "certain",
    "unspecified",
    "unknown",
    "old",
    "new",
    "vintage",
    "retro",
    "common",
    "classic",
    "rehouse",
    "rehoused",
    "cine",
    "cinema",
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
    "某镜头",
    "某个镜头",
    "某颗",
    "某个",
    "某",
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
    "得到",
    "获得",
    "完成",
    "结果",
    "并且",
    "并",
    "然后",
    "给",
    "输出",
    "导出",
    "打印",
    "测试",
    "再试试",
    "试试",
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

# A short “try this lens” turn is common in an ongoing design session (for
# example ``试试康泰时28F2`` or ``test Sigma 28-70``).  There is no explicit
# surface noun in that shorthand, so treat only these narrowly scoped verbs as
# a continuation signal.  Ordinary design verbs still require a cap/front
# surface term; that preserves the optical-design/product-photo exclusions.
_SHORTHAND_CONTINUATION_VERBS = frozenset({"试试", "试用", "测试", "尝试", "try", "test", "try out"})
_CJK_CONTEXTUAL_LENS_ALIASES = frozenset({"八羽怪", "八枚玉"})
_KNOWN_LATIN_LENS_IDENTITY = frozenset(
    {
        "angenieux",
        "arri",
        "canon",
        "contax",
        "cooke",
        "dallmeyer",
        "ddr",
        "fd",
        "fujinon",
        "fuji",
        "hasselblad",
        "helios",
        "jena",
        "kinoptik",
        "kowa",
        "leica",
        "mamiya",
        "meyer",
        "minolta",
        "nikkor",
        "nikon",
        "olympus",
        "pentax",
        "planar",
        "rokkor",
        "rodenstock",
        "schneider",
        "sekor",
        "sigma",
        "sony",
        "som",
        "takumar",
        "voigtlander",
        "zeiss",
        "zuiko",
    }
)
_KNOWN_CJK_LENS_IDENTITY = frozenset(
    {
        "佳能",
        "蔡司",
        "康泰时",
        "适马",
        "玛米亚",
        "尼康",
        "索尼",
        "徕卡",
        "奥林巴斯",
        "富士",
        "宾得",
        "美能达",
        "海鸥",
        "凤凰",
        "潘太康",
        "老蛙",
        "七工匠",
        *_CJK_CONTEXTUAL_LENS_ALIASES,
    }
)


def _identity_is_explicitly_ambiguous(text: str) -> bool:
    """Return true for language that positively says the identity is absent."""

    return bool(
        re.search(
            r"(?<![a-z0-9])(?:unknown|unnamed|unspecified)\s+"
            r"(?:brand|maker|manufacturer|model|lens)(?![a-z0-9])|"
            r"(?:未知|未命名|不明)(?:品牌|厂牌|制造商|型号|镜头)|"
            r"(?:通用|任意|某品牌|某型号)(?:的)?镜头",
            text,
        )
    )


def _near_identity_cue(
    candidate: re.Match[str], cue_matches: list[re.Match[str]], text: str
) -> bool:
    """Keep identity evidence inside the local lens/model phrase."""

    for cue in cue_matches:
        if candidate.end() <= cue.start():
            between = text[candidate.end() : cue.start()]
        elif cue.end() <= candidate.start():
            between = text[cue.end() : candidate.start()]
        else:
            return True
        # Two intervening catalog tokens covers ``Mamiya 645 80mm`` and
        # ``Zeiss Planar 50mm`` without borrowing a remote style adjective.
        if len(re.findall(r"[a-z0-9]+|[\u3400-\u9fff]", between)) <= 2:
            return True
    return False


def _overlaps_any(candidate: re.Match[str], cues: list[re.Match[str]]) -> bool:
    return any(candidate.start() < cue.end() and cue.start() < candidate.end() for cue in cues)


def _known_identity_present(text: str) -> bool:
    return any(
        match.group(0).casefold() in _KNOWN_LATIN_LENS_IDENTITY
        for match in _LATIN_TOKEN_RE.finditer(text)
    ) or any(token in text for token in _KNOWN_CJK_LENS_IDENTITY)


def _context_has_lens_identity_evidence(text: str) -> bool:
    """Prevent sticky cap context from swallowing a newly named object."""

    return bool(
        _LENS_NOUN_RE.search(text)
        or (_FOCAL_RE.search(text) and _APERTURE_RE.search(text))
        or _known_identity_present(text)
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
    hits: list[str] = []
    for value in values:
        if not value:
            continue
        # ASCII one-word aliases such as ``cap``/``cover`` must not fire on
        # ``capture``/``discover``.  Keep CJK matching as substring semantics
        # because Chinese has no whitespace word boundary.
        if re.fullmatch(r"[a-z0-9][a-z0-9 .+/_-]*[a-z0-9]", value):
            pattern = rf"(?<![a-z0-9]){re.escape(value)}(?![a-z0-9])"
            if re.search(pattern, text):
                hits.append(value)
        elif value in text:
            hits.append(value)
    return tuple(hits)


def _production_search_text(text: str) -> str:
    """Mask art-style compounds that contain a production verb by accident."""

    # ``screen-print style`` describes the requested visual language; it does
    # not ask Codex to manufacture or export a printable object.  Mask only
    # the compound when it directly modifies a style word so a genuine request
    # such as ``screen-print artwork, then print the cap`` retains its second
    # standalone production signal.
    return re.sub(
        r"(?<![a-z0-9])screen[- ]print(?:ing)?"
        r"(?=(?:[- ]inspired)?[- ](?:style|aesthetic|look|effect|graphic)\b)",
        "screenprocess",
        text,
    )


def _production_hits(text: str) -> tuple[str, ...]:
    """Return explicit physical-deliverable signals, not incidental verbs.

    Words such as ``run``, ``invoke``, ``fit``, ``print`` and ``slice`` are
    common inside ImageGen instructions and visual descriptions.  Treating
    them as naked substrings upgraded concept-art requests to CAD production.
    File formats and unambiguous Chinese production terms remain direct
    signals; ambiguous English words require a cap/model/file object.
    """

    source = _production_search_text(text)
    hits = list(
        _contains(
            source,
            (
                "3mf",
                "stl",
                "scad",
                "cad",
                "printable",
                "relief model",
                "lens relief model",
                "printer handoff",
                "可打印",
                "打印模型",
                "打印文件",
                "浮雕模型",
                "建模",
                "切片文件",
                "切片工程",
                "卡合",
            ),
        )
    )
    contextual_patterns = (
        (
            "print cap/model",
            r"(?<![a-z0-9])(?:3d[- ]?)?print(?:ed|ing)?\s+"
            r"(?:the\s+|this\s+|a\s+|an\s+)?"
            r"(?:lens[- ]?cap|cap|model|part|object|3mf)(?![a-z0-9])",
        ),
        (
            "cap/model fit",
            r"(?<![a-z0-9])(?:fit(?:ted|ting)?\s+(?:the\s+|this\s+|a\s+|an\s+)?"
            r"(?:lens[- ]?cap|cap|model)|(?:lens[- ]?cap|cap|model)\s+fit(?:ting)?)(?![a-z0-9])",
        ),
        (
            "slice cap/file",
            r"(?<![a-z0-9])(?:slice(?:d|ing)?\s+(?:the\s+|this\s+|a\s+|an\s+)?"
            r"(?:lens[- ]?cap|cap|model|3mf|file|project)|"
            r"(?:sliced|slicer)\s+(?:3mf|file|project))(?![a-z0-9])",
        ),
        (
            "build physical model",
            r"(?<![a-z0-9])build\s+(?:the\s+|this\s+|a\s+|an\s+)?"
            r"(?:lens[- ]?cap|cap|physical model|3d model)(?![a-z0-9])",
        ),
        (
            "cap relief",
            r"(?<![a-z0-9])(?:lens[- ]?cap|front[- ]cap|cap)(?:[- ]front)?"
            r"[- ]relief(?![a-z0-9])|(?:镜头盖|镜头帽|镜头闷盖|前盖)(?:正面)?浮雕",
        ),
        (
            "Chinese physical model",
            r"(?:3d|三维|立体|可打印|打印|浮雕|镜头盖|前盖)(?:的)?模型|"
            r"模型(?:用于|作为)?(?:镜头盖|前盖)",
        ),
        (
            "Chinese cap fit",
            r"(?:镜头盖|前盖|盖子)(?:的|口径|前口)?(?:卡合|适配)|"
            r"(?:卡合|适配)(?:的|这个|该)?(?:镜头盖|前盖|盖子|口径|前口)",
        ),
    )
    for label, pattern in contextual_patterns:
        if re.search(pattern, source):
            hits.append(label)
    return tuple(dict.fromkeys(hits))


def _cap_object_hits(text: str) -> tuple[str, ...]:
    """Identify a lens-cap object with positive grammar, not loose aliases.

    A nearby named lens does not turn an article ``cover``, review ``badge``,
    printable poster, or 3MF metadata into a cap.  The one omission-friendly
    artwork form is an explicit *circular lens-front artwork* compound: this is
    the established natural-language synonym for artwork on the cap's face.
    """

    patterns = (
        (
            "lens cap",
            r"(?<![a-z0-9])(?:camera[- ]?)?lens[- ]?cap(?:s)?(?![a-z0-9])",
        ),
        (
            "front cap",
            r"(?<![a-z0-9])front[- ]cap(?:s)?(?![a-z0-9])",
        ),
        (
            "physical cap",
            r"(?<![a-z0-9])(?:physical|fitted|printable|3d[- ]printed)\s+"
            r"(?:lens[- ]?)?cap(?:s)?(?![a-z0-9])|"
            r"(?<![a-z0-9])cap[- ](?:front|face|model|part|relief|artwork|"
            r"graphic|image|pattern|badge|medallion|design)(?![a-z0-9])",
        ),
        (
            "cap-owned surface",
            r"(?<![a-z0-9])(?:front|face|artwork|graphic|image|pattern|badge|"
            r"medallion|relief|design)\s+(?:of|for|on)\s+"
            r"(?:a\s+|an\s+|the\s+)?(?:camera[- ]?)?(?:lens[- ]?)?cap"
            r"(?![a-z0-9])",
        ),
        (
            "cap for named lens",
            r"(?<![a-z0-9])(?:lens[- ]?)?cap\s+for\s+"
            r"(?:my\s+|the\s+|a\s+|an\s+)?(?=[a-z0-9\u3400-\u9fff])",
        ),
        (
            "circular lens-front artwork",
            r"(?<![a-z0-9])circular\s+lens[- ]front\s+"
            r"(?:artwork|graphic|image|pattern|design|badge|medallion|relief)"
            r"(?![a-z0-9])",
        ),
        (
            "Chinese lens cap",
            r"镜头前盖|镜头盖|镜头帽|镜头闷盖",
        ),
        (
            "Chinese physical/front cap",
            r"实体(?:镜头)?盖(?:正面)?|(?:镜头)?前盖正面|盖体正面",
        ),
        (
            "Chinese circular lens-front artwork",
            r"(?:圆形镜头正面|镜头圆形正面|镜头正面圆形)"
            r"(?:图稿|图案|图像|艺术图|设计|徽章|浮雕)",
        ),
    )
    return tuple(label for label, pattern in patterns if re.search(pattern, text))


_CAP_OBJECT_MENTION_PATTERN = (
    r"(?<![a-z0-9])(?:camera[- ]?)?(?:lens[- ]?|front[- ]?)?cap(?:s)?"
    r"(?![a-z0-9])|镜头前盖|镜头盖|镜头帽|镜头闷盖|前盖"
)


def _negated_cap_object_mentions(text: str) -> tuple[tuple[str, int, int], ...]:
    """Return the exact cap-object spans governed by an explicit negation."""

    english_prefix = re.compile(
        r"(?<![a-z0-9])(?:do\s+not|don't|dont|isn't|isnt|aren't|arent|"
        r"never|not(?!\s+only\b)|no|without|except|skip|avoid|exclude|"
        r"anything\s+but|something\s+other\s+than|other\s+than)\b"
        r".{0,96}$"
    )
    english_suffix = re.compile(
        r"^.{0,24}?(?<![a-z0-9])(?:is\s+not|isn't|isnt|not)\s+"
        r"(?:wanted|needed|required|requested)(?![a-z0-9])"
    )
    chinese_prefix = re.compile(
        r"(?:不要|别|无需|不用|不是|并非|不需要|不想要|跳过|略过|排除|"
        r"除|不(?:再)?(?:设计|生成|制作|做|建模|打印|输出|导出))"
        r".{0,48}$"
    )
    chinese_suffix = re.compile(
        r"^(?:以?外|之外)|^.{0,12}?(?:不要|不做|不想要|不需要|不用|无需|"
        r"跳过|略过|排除)"
    )
    mentions: list[tuple[str, int, int]] = []
    seen: set[tuple[int, int]] = set()
    for clause_start, _, clause in _intent_clauses(text):
        for match in re.finditer(_CAP_OBJECT_MENTION_PATTERN, clause):
            prefix = clause[: match.start()]
            suffix = clause[match.end() :]
            cap_text = match.group(0)
            is_chinese = bool(re.search(r"[\u3400-\u9fff]", cap_text))
            negated = (
                chinese_prefix.search(prefix) or chinese_suffix.search(suffix)
                if is_chinese
                else english_prefix.search(prefix) or english_suffix.search(suffix)
            )
            if not negated:
                continue
            span = (clause_start + match.start(), clause_start + match.end())
            if span in seen:
                continue
            label = (
                "Chinese negated cap object"
                if is_chinese
                else "English negated cap object"
            )
            mentions.append((label, *span))
            seen.add(span)
    return tuple(mentions)


def _span_overlaps(span: tuple[int, int], others: tuple[tuple[int, int], ...]) -> bool:
    return any(span[0] < other[1] and other[0] < span[1] for other in others)


def _intent_clauses(text: str) -> tuple[tuple[int, int, str], ...]:
    """Split coordinated intents while retaining source offsets."""

    separator = re.compile(
        r"(?<![0-9])[.](?![0-9])|[;!?。！？；，,\n]+|"
        r"(?<![a-z0-9])(?:then|instead|(?<!anything )but)(?![a-z0-9])|"
        r"(?<![a-z0-9])and\s+(?=(?:please\s+)?(?:design|generate|create|"
        r"make|build|produce|manufacture|print|model|export|output|revise|"
        r"redesign|draw|want|need|audit|review|inspect|check|update|edit|"
        r"explain|describe|debug|test|compare|open|archive)\b)|"
        r"(?:然后|而是|但是)|(?:并且|并|且)(?=(?:请)?(?:设计|生成|制作|创建|"
        r"做|建模|打印|输出|导出|修改|重做|审计|审核|检查|更新|调试|测试))"
    )
    clauses: list[tuple[int, int, str]] = []
    start = 0
    for match in separator.finditer(text):
        if text[start : match.start()].strip():
            clauses.append((start, match.start(), text[start : match.start()]))
        start = match.end()
    if text[start:].strip():
        clauses.append((start, len(text), text[start:]))
    return tuple(clauses)


def _affirmative_cap_deliverable_hits(
    text: str,
    *,
    negated_spans: tuple[tuple[int, int], ...],
) -> tuple[str, ...]:
    """Find a direct cap-output request outside negated or read-only clauses."""

    english_action = re.compile(
        r"(?<![a-z0-9])(?:design|generate|create|make|build|produce|manufacture|"
        r"print|model|export|output|revise|redesign|draw|want|need)\b"
        r"(?:(?![.;!?\n]).){0,96}$"
    )
    chinese_action = re.compile(
        r"(?:设计|生成|制作|创建|做|建模|打印|输出|导出|修改|重做|想要|需要|我要)"
        r"(?:(?![，。；！？\n]).){0,48}$"
    )
    output_suffix = re.compile(
        r"^(?:(?![.;!?。！？；\n]).){0,64}?"
        r"(?:artwork|graphic|image|pattern|badge|medallion|relief|model|part|"
        r"3mf|stl|scad|cad|图稿|图案|图像|徽章|浮雕|模型|零件)(?![a-z0-9])"
    )
    hits: list[str] = []
    for clause_start, _, clause in _intent_clauses(text):
        if _meta_or_read_only_task_hits(clause):
            continue
        for mention in re.finditer(_CAP_OBJECT_MENTION_PATTERN, clause):
            absolute_span = (
                clause_start + mention.start(),
                clause_start + mention.end(),
            )
            if _span_overlaps(absolute_span, negated_spans):
                continue
            prefix = clause[: mention.start()]
            suffix = clause[mention.end() :]
            if english_action.search(prefix):
                hits.append("English affirmative cap deliverable")
            elif chinese_action.search(prefix):
                hits.append("Chinese affirmative cap deliverable")
            elif output_suffix.search(suffix):
                hits.append("affirmative cap output noun phrase")
    return tuple(dict.fromkeys(hits))


def _natural_cap_surface_hits(text: str) -> tuple[str, ...]:
    """Recognise established cap-front ownership phrasing without a cap noun."""

    patterns = (
        (
            "circular lens artwork",
            r"(?<![a-z0-9])circular\s+(?:lens[- ]?)?(?:front\s+)?"
            r"(?:image|artwork|graphic|pattern|badge|medallion|relief|design)"
            r"(?![a-z0-9])",
        ),
        (
            "lens front artwork",
            r"(?<![a-z0-9])lens[- ](?:front|face)\s+"
            r"(?:image|artwork|graphic|pattern|badge|medallion|relief|design)"
            r"(?![a-z0-9])",
        ),
        (
            "lens-owned artwork",
            r"(?<![a-z0-9])lens[- ](?:badge|medallion|artwork|relief)"
            r"(?![a-z0-9])|(?<![a-z0-9])lens\s+cover\s+"
            r"(?:artwork|model|relief)(?![a-z0-9])",
        ),
        (
            "Chinese circular lens artwork",
            r"(?:圆形镜头|镜头圆形|圆形正面)(?:正面)?"
            r"(?:图像|艺术图|图稿|图案|设计|徽章|浮雕)?|"
            r"圆形(?:图像|艺术图|图稿|图案|正面|浮雕)",
        ),
        (
            "Chinese lens-front artwork",
            r"镜头(?:正面|前口)(?:图像|图稿|图案|艺术图|设计|徽章|浮雕)|"
            r"镜头(?:图像|图稿|图案|艺术图|浮雕|徽章|铭牌)|前口(?:图像|图案)",
        ),
    )
    return tuple(label for label, pattern in patterns if re.search(pattern, text))


def _non_cap_deliverable_hits(text: str) -> tuple[str, ...]:
    """Identify nearby named-lens requests whose requested object is not a cap."""

    patterns = (
        (
            "editorial/manual deliverable",
            r"(?<![a-z0-9])(?:cover[- ]article|article[- ]cover|user[- ]manual|"
            r"lens[- ]manual|manual\s+(?:for|about)|magazine[- ]cover|book[- ]cover)"
            r"(?![a-z0-9])|(?:封面文章|文章封面|说明书|用户手册|杂志封面|书籍封面)",
        ),
        (
            "review/award/warranty deliverable",
            r"(?<![a-z0-9])(?:lens[- ]review|review[- ]badge|award[- ]badge|"
            r"warranty[- ]card)(?![a-z0-9])|"
            r"(?:镜头评测|评测.{0,8}徽章|奖项.{0,8}徽章|保修卡)",
        ),
        (
            "photograph/poster deliverable",
            r"(?<![a-z0-9])(?:printable[- ]poster|product[- ]poster|"
            r"photo(?:graph)?s?|snapshot)(?![a-z0-9])|"
            r"(?:可打印.{0,4}海报|产品海报|照片|相片|摄影图)",
        ),
        (
            "metadata deliverable",
            r"(?<![a-z0-9])meta[- ]?data(?![a-z0-9])|元数据",
        ),
        (
            "non-cap accessory",
            r"(?<![a-z0-9])(?:lens[- ]?)?(?:pouch|bag|case|hood)(?![a-z0-9])|"
            r"(?<![a-z0-9])dust[- ]cover(?![a-z0-9])|"
            r"(?:镜头包|镜头袋|镜头盒|镜头罩|遮光罩|防尘罩)",
        ),
        (
            "unrelated hardware/label deliverable",
            r"(?<![a-z0-9])(?:camera[- ]?)?(?:cage|plate|label|grip)"
            r"(?![a-z0-9])|(?:兔笼|快装板|铭牌标签|标签|手柄)",
        ),
    )
    return tuple(label for label, pattern in patterns if re.search(pattern, text))


def _lens_component_deliverable_hits(text: str) -> tuple[str, ...]:
    """Identify physical lens components that are not a cap deliverable."""

    patterns = (
        ("lens body", r"(?<![a-z0-9])lens[- ]body(?![a-z0-9])|镜身"),
        ("lens barrel", r"(?<![a-z0-9])(?:lens[- ]?)?barrel(?![a-z0-9])|镜筒"),
        (
            "focusing ring",
            r"(?<![a-z0-9])(?:focus|focusing)[- ]ring(?![a-z0-9])|(?:对焦|调焦)环",
        ),
        (
            "lens gear",
            r"(?<![a-z0-9])(?:lens|focus|focusing|follow[- ]focus)[- ]gear"
            r"(?![a-z0-9])|(?:镜头|跟焦|对焦)齿轮",
        ),
        ("lens hood", r"(?<![a-z0-9])lens[- ]hood(?![a-z0-9])|(?:镜头)?遮光罩"),
        ("lens mount", r"(?<![a-z0-9])lens[- ]mount(?![a-z0-9])|镜头卡口"),
    )
    return tuple(label for label, pattern in patterns if re.search(pattern, text))


def _physical_cap_hits(text: str) -> tuple[str, ...]:
    """Return cap-owned physical-deliverable words that imply production."""

    patterns = (
        (
            "physical/fitted cap",
            r"(?<![a-z0-9])(?:physical|fitted|printable|3d[- ]printed)\s+"
            r"(?:front[- ]|lens[- ]?)?cap(?:s)?(?![a-z0-9])",
        ),
        (
            "cap model/part",
            r"(?<![a-z0-9])(?:lens[- ]?cap|front[- ]cap|cap)[- ]"
            r"(?:model|part)(?![a-z0-9])",
        ),
        (
            "make cap for named lens",
            r"(?<![a-z0-9])(?:make|build|create|manufacture|print)\s+"
            r"(?:the\s+|this\s+|a\s+|an\s+)?(?:lens[- ]?)?cap\s+for\b",
        ),
        (
            "Chinese physical cap",
            r"实体(?:镜头)?盖(?:正面)?|可打印(?:的)?(?:镜头)?盖|"
            r"(?:镜头盖|前盖)(?:的)?(?:模型|零件|实体)",
        ),
    )
    return tuple(label for label, pattern in patterns if re.search(pattern, text))


def _optical_test_hits(text: str) -> tuple[str, ...]:
    """Return optical-test subjects that cannot borrow cap-task context."""

    patterns = (
        ("chromatic aberration", r"(?<![a-z0-9])chromatic[- ]aberration(?![a-z0-9])"),
        ("distortion test", r"(?<![a-z0-9])distortion(?![a-z0-9])"),
        ("bokeh test", r"(?<![a-z0-9])bokeh(?![a-z0-9])"),
        ("Chinese chromatic aberration", r"色差"),
        ("Chinese distortion", r"畸变"),
        ("Chinese bokeh", r"焦外"),
    )
    return tuple(label for label, pattern in patterns if re.search(pattern, text))


def _non_production_action_hits(text: str) -> tuple[str, ...]:
    """Reject questions/documentation *about* a format as deliverable intent.

    A terse named-lens plus ``3MF``/``STL`` remains supported as established
    output shorthand.  Read-only verbs such as “inspect” and “explain” bind
    the format to documentation or capability checking instead, so they must
    not be mistaken for a request to create that file.
    """

    if not re.search(r"(?<![a-z0-9])(?:3mf|stl|scad|cad)(?![a-z0-9])", text):
        return ()
    patterns = (
        (
            "inspect/explain format",
            r"(?:^|(?<![a-z0-9])(?:please|kindly|can you|could you|would you)\s+)"
            r"(?:inspect|explain|describe|document|check|review)\b",
        ),
        (
            "format capability question",
            r"(?<![a-z0-9])(?:whether|does|do|supports?|compatib(?:le|ility)|"
            r"how\s+to)\b.{0,80}(?:3mf|stl|scad|cad)|"
            r"(?:3mf|stl|scad|cad).{0,80}(?:support|compatib(?:le|ility)|workflow|process)\b",
        ),
        (
            "Chinese format inspection/explanation",
            r"(?:^|[，。；：:\s])(?:请)?(?:检查|解释|说明|审查|查看)|"
            r"(?:是否支持|支不支持|兼容(?:性)?|导出流程|工作流程|怎么导出|如何导出)",
        ),
    )
    return tuple(label for label, pattern in patterns if re.search(pattern, text))


def _meta_or_read_only_task_hits(text: str) -> tuple[str, ...]:
    """Identify requests *about* the cap workflow rather than a cap output."""

    artifact = (
        r"skill|documentation|docs?|instructions?|prompt|workflow|"
        r"rout(?:e|er|ing)|pipeline|triggers?|tools?|tasks?|tests?|"
        r"generators?|generation|formats?"
    )
    meta_action = (
        r"audit|review|inspect|check|update|edit|document|explain|describe|"
        r"debug|test|run\s+(?:the\s+)?tests?|fix|show|support|compare|open|archive"
    )
    patterns = (
        (
            "lens-cap skill or documentation task",
            rf"(?<![a-z0-9])(?:{meta_action})\b.{{0,128}}?"
            rf"(?<![a-z0-9])(?:{artifact})(?![a-z0-9])",
        ),
        (
            "lens-cap skill or documentation task (object first)",
            rf"(?<![a-z0-9])(?:{artifact})(?![a-z0-9]).{{0,128}}?"
            rf"(?<![a-z0-9])(?:{meta_action})\b",
        ),
        (
            "read-only cap inspection",
            r"(?:^|(?<![a-z0-9])(?:please|kindly|can you|could you|would you)\s+)"
            r"(?:audit|review|inspect|check|analy[sz]e|explain|describe|"
            r"compare|tell\s+me\s+about)\b.{0,128}?"
            rf"(?:{_CAP_OBJECT_MENTION_PATTERN})",
        ),
        (
            "read-only cap question",
            rf"^\s*(?:why|how(?:\s+come)?|what\s+(?:is|are|was|were))\b"
            rf".{{0,160}}?(?:{_CAP_OBJECT_MENTION_PATTERN})",
        ),
        (
            "cap task status question",
            rf"^\s*(?:is|are|was|were)\b.{{0,160}}?"
            rf"(?:{_CAP_OBJECT_MENTION_PATTERN}).{{0,96}}?"
            r"\b(?:done|complete|finished|ready)\b",
        ),
        (
            "read-only cap explanation",
            rf"^\s*(?:please\s+)?tell\s+me\s+about\b.{{0,128}}?"
            rf"(?:{_CAP_OBJECT_MENTION_PATTERN})",
        ),
        (
            "Chinese lens-cap skill or documentation task",
            r"(?:审计|审核|检查|更新|修改|编辑|解释|说明|复盘|测试|调试|"
            r"支持|比较|打开|归档).{0,96}?"
            r"(?:skill|技能|文档|说明|流程|路由|提示词|管线|流水线|触发器|工具|任务|"
            r"生成器|格式)|"
            r"(?:skill|技能|文档|说明|流程|路由|提示词|管线|流水线|触发器|工具|任务|"
            r"生成器|格式)"
            r".{0,96}?(?:审计|审核|检查|更新|修改|编辑|解释|说明|复盘|测试|"
            r"调试|支持|比较|打开|归档)",
        ),
        (
            "Chinese cap format capability question",
            rf"(?:{_CAP_OBJECT_MENTION_PATTERN}).{{0,96}}?"
            r"(?:支持哪些?格式|有哪些格式|什么格式|可用格式)",
        ),
        (
            "Chinese read-only cap task",
            r"(?:为什么|为何|怎么|如何|审计|审核|检查|解释|说明|分析|复盘)"
            r".{0,96}?(?:镜头前盖|镜头盖|镜头帽|镜头闷盖|前盖)",
        ),
    )
    hits: list[str] = []
    for _, _, clause in _intent_clauses(text):
        if not re.search(_CAP_OBJECT_MENTION_PATTERN, clause):
            continue
        for label, pattern in patterns:
            if re.search(pattern, clause):
                hits.append(label)
    return tuple(dict.fromkeys(hits))


def _meta_wrapper_hits(text: str) -> tuple[str, ...]:
    """Catch top-level audit/simulation wrappers around quoted cap requests.

    Clause-local matching is deliberate for mixed turns such as "audit the
    Skill, then design a cap".  A read-only or prompt-simulation wrapper is
    different: any cap wording inside it is test data, not a deliverable.
    Treat these explicit wrappers as hard vetoes even when the quoted sample
    itself contains an affirmative creation verb.
    """

    patterns = (
        (
            "quoted/simulated request task",
            r"(?<![a-z0-9])(?:simulate|replay)\b.{0,128}?"
            r"(?<![a-z0-9])(?:user[- ]request|natural[- ]language[- ]request|"
            r"prompt|interaction|conversation)(?![a-z0-9])|"
            r"(?<![a-z0-9])(?:test|audit|inspect|check)\b.{0,64}?"
            r"(?<![a-z0-9])(?:this|the\s+following|quoted)\s+"
            r"(?:prompt|request)(?![a-z0-9])|"
            r"(?:模拟|复演).{0,80}?(?:用户(?:自然语言)?请求|自然请求|提示词|交互|对话)|"
            r"(?:测试|审计|审核|检查)(?:下面|以下|这条|这个).{0,16}?提示词",
        ),
        (
            "explicit read-only wrapper",
            r"(?<![a-z0-9])read[- ]only\s+(?:audit|review|inspection|test)"
            r"(?![a-z0-9])|"
            r"(?<![a-z0-9])do\s+not\s+(?:generate|create|modify|edit|write)"
            r"\s+(?:any\s+)?files?\b|"
            r"(?:只做)?只读(?:审计|审核|检查|分析|测试)|"
            r"不(?:生成|创建|修改|改动|写入)(?:任何)?文件",
        ),
        (
            "quoted trigger test",
            r"(?<![a-z0-9])(?:test|audit|inspect|check)\b.{0,96}?"
            r"(?<![a-z0-9])(?:quoted\s+)?(?:prompt|request|trigger|routing)"
            r"(?![a-z0-9])|"
            r"(?:测试|审计|审核|检查).{0,64}?(?:提示词|请求|触发(?:器|率)?|路由)",
        ),
    )
    return tuple(label for label, pattern in patterns if re.search(pattern, text))


def _contextual_named_lens(text: str, verb_hits: tuple[str, ...]) -> bool:
    """Allow a model/family name shorthand only in an established cap task."""

    if not any(verb in _SHORTHAND_CONTINUATION_VERBS for verb in verb_hits):
        return False
    if _identity_is_explicitly_ambiguous(text):
        return False
    for match in _LATIN_TOKEN_RE.finditer(text):
        candidate = match.group(0).casefold()
        if candidate in _KNOWN_LATIN_LENS_IDENTITY:
            return True
        if (
            (_LENS_NOUN_RE.search(text) or (_FOCAL_RE.search(text) and _APERTURE_RE.search(text)))
            and not re.fullmatch(r"iso[0-9]+", candidate)
            and re.search(r"[a-z]", candidate)
            and re.search(r"[0-9]", candidate)
            and not re.fullmatch(r"f[0-9]+(?:[.][0-9]+)?", candidate)
        ):
            return True
    return any(token in text for token in _KNOWN_CJK_LENS_IDENTITY)


def _implicit_surface_hit(
    source: str,
    *,
    named: bool,
    verb_hits: tuple[str, ...],
    surface_hits: tuple[str, ...],
    exclusion_hits: tuple[str, ...],
    explicit_hits: tuple[str, ...],
) -> tuple[str, ...]:
    """Recognise a deliberately small class of terse continuation turns."""

    if not named or surface_hits:
        return ()
    if exclusion_hits and not explicit_hits:
        return ()
    if not any(verb in _SHORTHAND_CONTINUATION_VERBS for verb in verb_hits):
        return ()
    # ``named`` already rejects generic “this/a lens” wording.  In an explicit
    # lens-cap continuation context, a well-known nickname such as 八羽怪 is a
    # sufficient identity even when the follow-up omits focal/aperture digits.
    return ("implicit lens-cap surface (terse continuation)",)


def _strip_cjk_stopwords(candidate: str) -> str:
    residue = candidate
    for stopword in sorted(_CJK_IDENTITY_STOPWORDS, key=len, reverse=True):
        residue = residue.replace(stopword, "")
    return residue


def _heuristic_named_lens(text: str, *, original_text: str | None = None) -> bool:
    """Return a conservative textual named-lens signal.

    A focal/aperture specification alone is not enough; require a non-generic
    Latin or CJK identity token as well.  Attached-image/catalog identities can
    bypass this heuristic with the explicit ``named_lens`` argument.
    """

    if _identity_is_explicitly_ambiguous(text):
        return False
    latin_matches = list(_LATIN_TOKEN_RE.finditer(text))
    compact_matches = list(_COMPACT_ID_RE.finditer(text))
    numeric_cue_matches = [*_FOCAL_RE.finditer(text), *_APERTURE_RE.finditer(text)]
    cue_matches = [
        *numeric_cue_matches,
        *_LENS_NOUN_RE.finditer(text),
        *compact_matches,
    ]
    if not cue_matches:
        return False
    for match in latin_matches:
        candidate = match.group(0)
        folded = candidate.casefold()
        if _overlaps_any(match, numeric_cue_matches):
            continue
        if not _near_identity_cue(match, cue_matches, text):
            continue
        if folded in _KNOWN_LATIN_LENS_IDENTITY:
            return True
        # Unknown but catalog-like alphanumeric model codes are positive
        # evidence; prose adjectives are not.
        if (
            re.search(r"[a-z]", folded)
            and re.search(r"[0-9]", folded)
            and not re.fullmatch(r"iso[0-9]+", folded)
            and not re.fullmatch(r"f[0-9]+(?:[.][0-9]+)?", folded)
        ):
            return True

    for token in _KNOWN_CJK_LENS_IDENTITY:
        for match in re.finditer(re.escape(token), text):
            if _near_identity_cue(match, cue_matches, text):
                return True

    # A capitalized proper-name token immediately inside the specification
    # phrase supports makers beyond the optional known-token accelerators.
    # This is intentionally evaluated on the user's original casing; generic
    # lowercase adjectives remain non-identity, while catalog/model codes are
    # already handled above regardless of case.
    if original_text is not None:
        original = unicodedata.normalize("NFKC", original_text)
        original_cues = [
            *_FOCAL_RE.finditer(original),
            *_APERTURE_RE.finditer(original),
            *_LENS_NOUN_RE.finditer(original),
            *_COMPACT_ID_RE.finditer(original),
        ]
        original_numeric_cues = [
            *_FOCAL_RE.finditer(original),
            *_APERTURE_RE.finditer(original),
        ]
        for match in _LATIN_TOKEN_RE.finditer(original):
            candidate = match.group(0)
            if (
                candidate[0].isupper()
                and candidate.casefold() not in _IDENTITY_STOPWORDS
                and not _overlaps_any(match, original_numeric_cues)
                and not re.fullmatch(r"iso[0-9]+", candidate.casefold())
                and _near_identity_cue(match, original_cues, original)
            ):
                return True

    explicit_latin = re.compile(
        r"(?<![a-z0-9])(?:brand|maker|manufacturer|model|series)\s*"
        r"(?:is\s+|named\s+)?[:=]?\s*"
        r"(?P<identity>[a-z][a-z0-9./-]{1,})(?![a-z0-9])"
    )
    for match in explicit_latin.finditer(text):
        if match.group("identity") not in _IDENTITY_STOPWORDS:
            return True
    return False


def resolve_request(
    text: str,
    *,
    named_lens: bool | None = None,
    approved_artwork: bool = False,
    lens_cap_context: bool = False,
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
    exclusion_hits = tuple(
        dict.fromkeys((*_contains(source, exclusions), *_optical_test_hits(source)))
    )
    explicit_hits = _contains(source, explicit)
    cap_object_hits = _cap_object_hits(source)
    negated_cap_mentions = _negated_cap_object_mentions(source)
    negated_cap_spans = tuple((start, end) for _, start, end in negated_cap_mentions)
    negated_cap_object_hits = tuple(
        dict.fromkeys(label for label, _, _ in negated_cap_mentions)
    )
    natural_cap_surface_hits = _natural_cap_surface_hits(source)
    lens_component_hits = _lens_component_deliverable_hits(source)
    affirmative_cap_deliverable_hits = _affirmative_cap_deliverable_hits(
        source,
        negated_spans=negated_cap_spans,
    )
    non_cap_deliverable_hits = tuple(
        dict.fromkeys(
            (
                *_non_cap_deliverable_hits(source),
                # A mention of an earlier cap task is context, not permission
                # to reinterpret a newly requested hood/ring/barrel as a cap.
                # Keep component-as-inspiration prompts valid only when this
                # turn also contains an affirmative cap deliverable.
                *(
                    lens_component_hits
                    if not cap_object_hits or not affirmative_cap_deliverable_hits
                    else ()
                ),
            )
        )
    )
    non_production_action_hits = _non_production_action_hits(source)
    meta_wrapper_hits = _meta_wrapper_hits(source)
    meta_or_read_only_task_hits = tuple(
        dict.fromkeys((*_meta_or_read_only_task_hits(source), *meta_wrapper_hits))
    )
    cap_negation_veto = bool(
        negated_cap_object_hits and not affirmative_cap_deliverable_hits
    )
    hard_meta_wrapper = any(
        label in {"quoted/simulated request task", "explicit read-only wrapper"}
        for label in meta_wrapper_hits
    )
    meta_task_veto = bool(
        hard_meta_wrapper
        or (meta_or_read_only_task_hits and not affirmative_cap_deliverable_hits)
    )
    heuristic_named = _heuristic_named_lens(source, original_text=text)
    contextual_named = bool(
        lens_cap_context
        and named_lens is None
        and not heuristic_named
        and _contextual_named_lens(source, verb_hits)
    )
    named = (
        heuristic_named or contextual_named
        if named_lens is None
        else bool(named_lens)
    )
    implicit_surface_hits = (
        _implicit_surface_hit(
            source,
            named=named,
            verb_hits=verb_hits,
            surface_hits=surface_hits,
            exclusion_hits=exclusion_hits,
            explicit_hits=explicit_hits,
        )
        if lens_cap_context and _context_has_lens_identity_evidence(source)
        else ()
    )

    raw_production_hits = _production_hits(source)
    physical_cap_hits = _physical_cap_hits(source)
    production_has_cap_scope = bool(
        cap_object_hits
        or natural_cap_surface_hits
        or (lens_cap_context and _context_has_lens_identity_evidence(source))
    )
    production_hits = (
        tuple(dict.fromkeys((*raw_production_hits, *physical_cap_hits)))
        if production_has_cap_scope
        and not non_cap_deliverable_hits
        and not non_production_action_hits
        and not cap_negation_veto
        and not meta_task_veto
        else ()
    )
    production = bool(production_hits)
    # A named lens plus an explicit cap/front deliverable is already a clear
    # request even when the user writes a terse noun phrase (“Zeiss … lens cap
    # artwork”).  For less explicit “circular image” wording, a focal/aperture
    # token supplies the missing action context.  This keeps the shim useful
    # for real chat shorthand without allowing an unnamed generic lens through.
    numeric_identity = bool(_FOCAL_RE.search(source) or _APERTURE_RE.search(source))
    intent_evidence = bool(verb_hits or cap_object_hits or production or numeric_identity)
    matched = bool(
        (named or cap_object_hits)
        and (
            cap_object_hits
            or (named and natural_cap_surface_hits)
            or (named and implicit_surface_hits)
            or (named and lens_cap_context and production_hits)
        )
        and intent_evidence
        and (not exclusion_hits or cap_object_hits)
        and not non_cap_deliverable_hits
        and not non_production_action_hits
        and not cap_negation_veto
        and not meta_task_veto
    )
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
    if not named and not cap_object_hits:
        reasons.append("named_lens_required")
    elif not named:
        reasons.append("lens_identity_intake_required")
    if not verb_hits and not matched:
        reasons.append("design_verb_missing")
    if (
        not cap_object_hits
        and not natural_cap_surface_hits
        and not implicit_surface_hits
        and not production_hits
    ):
        reasons.append("cap_surface_term_missing")
    if exclusion_hits and not cap_object_hits:
        reasons.append("optical_repair_or_product_exclusion")
    if non_cap_deliverable_hits:
        reasons.append("non_cap_deliverable")
    if non_production_action_hits:
        reasons.append("non_production_action")
    if cap_negation_veto:
        reasons.append("negated_cap_object")
    if meta_task_veto:
        reasons.append("meta_or_read_only_task")
    if (
        affirmative_cap_deliverable_hits
        and (negated_cap_object_hits or meta_or_read_only_task_hits)
        and not cap_negation_veto
        and not meta_task_veto
    ):
        reasons.append("affirmative_cap_deliverable_override")
    elif hard_meta_wrapper and cap_object_hits and verb_hits:
        # Clause-local read-only filtering can deliberately remove the quoted
        # creation clause from ``affirmative_cap_deliverable_hits``.  Preserve
        # one stable diagnostic for the outer wrapper whenever its sample text
        # still contains a cap object and creation verb.
        reasons.append("quoted_affirmative_deliverable_ignored")
    if matched:
        reasons.append("named_lens_cap_surface_intent")
    return {
        "status": "matched" if matched else "not_matched",
        "route": route,
        "named_lens_detected": named,
        "identity_needs_normalization": contextual_named,
        "production_intent": production,
        "lens_cap_context": bool(lens_cap_context),
        "matched_terms": {
            "verbs": list(verb_hits),
            "surface": list(surface_hits),
            "implicit_surface": list(implicit_surface_hits),
            "explicit_cap_surface": list(explicit_hits),
            "cap_object": list(cap_object_hits),
            "negated_cap_object": list(negated_cap_object_hits),
            "affirmative_cap_deliverable": list(affirmative_cap_deliverable_hits),
            "natural_cap_surface": list(natural_cap_surface_hits),
            "exclusions": list(exclusion_hits),
            "non_cap_deliverable": list(non_cap_deliverable_hits),
            "lens_component_deliverable": list(lens_component_hits),
            "non_production_action": list(non_production_action_hits),
            "meta_or_read_only_task": list(meta_or_read_only_task_hits),
            "meta_wrapper": list(meta_wrapper_hits),
            "production": list(production_hits),
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
    parser.add_argument(
        "--lens-cap-context",
        action="store_true",
        help="allow terse try/test continuation wording only in an established lens-cap task",
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
            lens_cap_context=args.lens_cap_context,
            policy=_load_policy(args.manifest.expanduser().resolve()),
        )
    except RouteError as exc:
        print(f"lens-cap route: error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "matched" else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

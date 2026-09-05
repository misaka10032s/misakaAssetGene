"""Character fidelity checklist derivation (spec §5.15 / C-spec.md §2).

Parses a character's SSOT markdown pair (``setting.md`` + ``outfits.md``) into
a list of :class:`~core.models.schemas.FidelityCheck` items that a VLM critic
(``core.llm.vision``) can later judge a render against.

Design notes (read before touching the keyword tables below)
--------------------------------------------------------------
- ``setting.md``'s ``## 🎨 外型特徵`` section: every top-level bullet
  (``- **label**：description``, indent 0) becomes one check. Its
  ``pass_criteria`` is the ORIGINAL text verbatim (the bullet line plus any
  more-indented continuation/nested lines belonging to it) — never
  summarized, so a human can trace a check back to its source sentence.
- ``outfits.md``: the selected outfit item (matched by its ``[Tag]`` bracket,
  case-insensitive) contributes one check per DIRECT sub-bullet (indent 4)
  EXCEPT ``生成提示詞``, which is not a visual assertion — instead its nested
  ``ComfyUI`` line is extracted as the outfit's tag pool for fix_tags.
- ``region_hint`` and ``fix_tags`` are both derived from the SAME keyword
  scan (see ``_CATEGORIES`` below): the check's own raw text block is
  scanned, top-to-bottom (head → toe), for the first category whose Chinese
  trigger substrings appear; that category's English filter words then
  select the relevant subset of the tag pool. This is a heuristic, not a
  translation — it will occasionally misclassify (documented at the call
  site / dispatch report), and callers should treat a derived checklist as a
  first draft, not a guaranteed-correct one.
- ``fix_tags`` falls back to the FULL tag pool whenever the keyword filter
  matches nothing (spec §2.1: "找不到就整段 fallback") — better a slightly
  noisy prompt than a starved one.
- Parsing failure (missing section, unknown outfit variant, missing
  ComfyUI line) always raises loudly with the available options listed —
  never returns an empty list silently (spec §2.1: "解析失敗 → 明確拋錯
  列出可用變體，不回空清單").
"""

from __future__ import annotations

import re
from pathlib import Path

from core.models.schemas import BodyRegion, FidelityCheck

# ---------------------------------------------------------------------------
# Region + fix-tag keyword table (see module docstring "Design notes")
# ---------------------------------------------------------------------------

# Ordered head-to-toe. First entry whose zh_triggers substring is found in a
# check's raw text wins; its en_filters then selects the fix_tags subset.
_CATEGORIES: list[tuple[BodyRegion, tuple[str, ...], tuple[str, ...]]] = [
    (
        BodyRegion.HEAD,
        ("頭部", "頭飾", "髮", "瀏海", "貝蕾", "帽", "馬尾", "髮夾", "簪"),
        ("hair", "bang", "beret", "hairpin", "clip", "ribbon", "flower", "bun", "kanzashi", "hat", "brooch"),
    ),
    (
        BodyRegion.FACE,
        ("臉部", "瞳", "眼鏡", "眼睛", "妝容", "表情"),
        ("eye", "eyewear", "glass", "expression", "smile"),
    ),
    (
        BodyRegion.TORSO,
        ("身體服裝", "上衣", "胸", "頸", "領", "圍裙", "洋裝", "連身裙", "背心", "內衣"),
        ("dress", "outfit", "uniform", "top", "apron", "collar", "sailor", "cleavage", "petite", "bust"),
    ),
    (
        BodyRegion.WAIST,
        ("腰", "飾品", "束帶", "蝴蝶結", "繫帶"),
        ("waist", "belt", "strap", "obi", "bow", "dagger", "blade", "lace-up"),
    ),
    (
        BodyRegion.LEGS,
        ("裙", "鞋", "襪", "腿"),
        ("skirt", "pleat", "shoe", "sandal", "clog", "boot", "sock", "stocking", "mary jane"),
    ),
    (
        BodyRegion.BACKGROUND,
        ("背景",),
        ("background",),
    ),
]

_DEFAULT_REGION = BodyRegion.TORSO


def _classify(block_text: str) -> tuple[BodyRegion, tuple[str, ...]]:
    for region, zh_triggers, en_filters in _CATEGORIES:
        if any(trigger in block_text for trigger in zh_triggers):
            return region, en_filters
    return _DEFAULT_REGION, ()


# Finer, per-BULLET-LABEL keyword set for fix_tags — tried BEFORE the coarse
# per-region ``_CATEGORIES`` filter above. Without this, every check sharing
# one region (e.g. 裙子/鞋子/襪子 all under LEGS) collapsed onto the exact
# same fix_tags subset (the union of every LEGS keyword), which is correct
# for region_hint but too broad for a single check's own fix. Matched by
# substring against the bullet's bold label text (not the full block), in
# order, first match wins. A label matching none of these falls back to the
# region-category filter, then (if still empty) the full tag pool.
_LABEL_FIX_TAG_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("髮型", ("hair", "bang")),
    # "eyes" (plural), not the bare substring "eye" — "eye" also matches
    # "eyewear", which would wrongly pull glasses tags into a pupil-colour
    # check (found while proving this parser against a synthetic fixture).
    ("瞳色", ("eyes",)),
    ("特殊變換", ("hair", "bang")),
    ("配件", ("eyewear", "glass")),
    ("妝容", ("expression", "smile")),
    ("服裝原則", ("petite", "cleavage", "bust")),
    ("頭部", ("hair", "beret", "hairpin", "clip", "ribbon", "flower", "bun", "kanzashi", "hat", "brooch")),
    ("頭飾", ("hair", "beret", "hairpin", "clip", "ribbon", "flower", "bun", "kanzashi", "hat", "brooch")),
    ("臉部", ("eyewear", "glass", "eye")),
    ("身體服裝", ("dress", "outfit", "uniform", "top", "apron", "collar", "sailor")),
    ("頸部", ("collar",)),
    ("飾品", ("bow", "brooch", "strap", "belt", "obi", "dagger", "blade", "lace-up")),
    ("裙子", ("skirt", "pleat")),
    ("鞋子", ("shoe", "sandal", "clog", "boot", "mary jane")),
    ("襪子", ("sock", "stocking")),
]


def _label_fix_tag_keywords(label: str) -> tuple[str, ...]:
    for key, keywords in _LABEL_FIX_TAG_KEYWORDS:
        if key in label:
            return keywords
    return ()


def _split_tag_line(line: str) -> list[str]:
    return [tag.strip() for tag in line.split(",") if tag.strip()]


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _derive_fix_tags(label: str, block_text: str, tag_pool: list[str]) -> list[str]:
    if not tag_pool:
        return []
    keywords = _label_fix_tag_keywords(label)
    if not keywords:
        _, keywords = _classify(block_text)
    if keywords:
        matched = [
            tag for tag in tag_pool
            if any(keyword in tag.lower() for keyword in keywords)
        ]
        if matched:
            return _dedupe_preserve_order(matched)
    # spec §2.1: "找不到就整段 fallback" — no keyword match, use the whole pool.
    return _dedupe_preserve_order(tag_pool)


# ---------------------------------------------------------------------------
# Generic bullet-block scanner (shared by setting.md and outfits.md parsing)
# ---------------------------------------------------------------------------

_BULLET_RE = re.compile(r"^(?P<indent>[ \t]*)-\s+\*\*(?P<label>[^*]+)\*\*[:：]?\s*(?P<rest>.*)$")
_HEADING_RE = re.compile(r"^#{1,6}\s+(?P<title>.*)$")


class _Bullet:
    __slots__ = ("block_lines", "indent", "label")

    def __init__(self, indent: int, label: str, first_rest: str) -> None:
        self.indent = indent
        self.label = label.strip()
        self.block_lines: list[str] = [first_rest] if first_rest else []

    def text(self) -> str:
        return "\n".join(line for line in [self.label, *self.block_lines] if line)


def _extract_bullets_at_indent(lines: list[str], target_indent: int) -> list[_Bullet]:
    """Collect bullets whose ``**label**`` line sits at EXACTLY ``target_indent``.

    Any subsequent line indented deeper than ``target_indent`` (nested detail,
    including further sub-bullets) is appended verbatim to the current
    bullet's block as pass_criteria continuation. A line at or shallower than
    ``target_indent`` that is NOT itself a matching bullet closes the current
    bullet silently (e.g. a blank line, or prose).
    """
    bullets: list[_Bullet] = []
    current: _Bullet | None = None
    for raw_line in lines:
        if not raw_line.strip():
            continue
        match = _BULLET_RE.match(raw_line)
        indent = len(raw_line) - len(raw_line.lstrip(" \t"))
        if match and indent == target_indent:
            current = _Bullet(indent, match.group("label"), match.group("rest").strip())
            bullets.append(current)
            continue
        if current is not None and indent > target_indent:
            current.block_lines.append(raw_line.strip())
            continue
        # shallower or equal indent, non-matching line: bullet block ends.
        current = None
    return bullets


def _section_lines(full_text: str, heading_substring: str) -> list[str]:
    """Lines strictly between a heading containing ``heading_substring`` and
    the next ``#`` heading (or EOF)."""
    lines = full_text.splitlines()
    start = None
    for idx, line in enumerate(lines):
        heading_match = _HEADING_RE.match(line)
        if heading_match and heading_substring in heading_match.group("title"):
            start = idx + 1
            break
    if start is None:
        return []
    end = len(lines)
    for idx in range(start, len(lines)):
        if _HEADING_RE.match(lines[idx]):
            end = idx
            break
    return lines[start:end]


# ---------------------------------------------------------------------------
# setting.md
# ---------------------------------------------------------------------------

_TAG_LINE_RE = re.compile(r"`([^`]+)`")


def _extract_backtick_tags(section_lines: list[str]) -> list[str]:
    for line in section_lines:
        tag_match = _TAG_LINE_RE.search(line)
        if tag_match:
            return _split_tag_line(tag_match.group(1))
    return []


def _parse_setting_checks(setting_md_text: str) -> list[FidelityCheck]:
    visual_lines = _section_lines(setting_md_text, "外型特徵")
    if not visual_lines:
        raise ValueError(
            "setting.md missing a '## 🎨 外型特徵' (or any heading containing "
            "外型特徵) section — cannot derive checklist."
        )
    tag_pool = _extract_backtick_tags(_section_lines(setting_md_text, "標籤"))

    bullets = _extract_bullets_at_indent(visual_lines, 0)
    checks: list[FidelityCheck] = []
    for index, bullet in enumerate(bullets, start=1):
        block_text = bullet.text()
        region, _ = _classify(block_text)
        fix_tags = _derive_fix_tags(bullet.label, block_text, tag_pool)
        checks.append(
            FidelityCheck(
                id=f"setting-{index}",
                label_zh=bullet.label,
                pass_criteria=block_text,
                region_hint=region,
                fix_tags=fix_tags,
                source="setting",
            )
        )
    return checks


# ---------------------------------------------------------------------------
# outfits.md
# ---------------------------------------------------------------------------

_OUTFIT_ITEM_RE = re.compile(r"^\d+\.\s+\*\*\[(?P<tag>[^\]]+)\]\s*(?P<name>[^*]*)\*\*:?\s*$")
_SKIP_OUTFIT_LABELS = {"生成提示詞"}


def _slugify_variant_tag(tag: str) -> str:
    return tag.strip().lower()


def list_outfit_variants(outfits_md_text: str) -> list[str]:
    """Every ``[Tag]`` bracket found in ``outfits.md``, slugified + deduped,
    in file order (spec §2.1)."""
    variants: list[str] = []
    for line in outfits_md_text.splitlines():
        item_match = _OUTFIT_ITEM_RE.match(line.strip())
        if item_match:
            slug = _slugify_variant_tag(item_match.group("tag"))
            if slug not in variants:
                variants.append(slug)
    return variants


def _outfit_item_lines(outfits_md_text: str, outfit_variant: str) -> list[str]:
    """Lines belonging to the outfit item matching ``outfit_variant``
    (case-insensitive bracket-tag match), from its own ``N. **[Tag] ...**``
    line (exclusive) up to the next top-level outfit item or heading."""
    lines = outfits_md_text.splitlines()
    target_slug = _slugify_variant_tag(outfit_variant)
    start = None
    for idx, line in enumerate(lines):
        item_match = _OUTFIT_ITEM_RE.match(line.strip())
        if item_match and _slugify_variant_tag(item_match.group("tag")) == target_slug:
            start = idx + 1
            break
    if start is None:
        available = list_outfit_variants(outfits_md_text)
        raise ValueError(
            f"Unknown outfit_variant {outfit_variant!r}. Available variants: {available}"
        )
    end = len(lines)
    for idx in range(start, len(lines)):
        stripped = lines[idx].strip()
        if _HEADING_RE.match(lines[idx]) or _OUTFIT_ITEM_RE.match(stripped):
            end = idx
            break
    return lines[start:end]


def _extract_comfyui_line(item_lines: list[str]) -> str:
    for line in item_lines:
        if "ComfyUI" in line:
            tag_match = _TAG_LINE_RE.search(line)
            if tag_match:
                return tag_match.group(1)
    return ""


def _parse_outfit_checks(outfits_md_text: str, outfit_variant: str) -> list[FidelityCheck]:
    item_lines = _outfit_item_lines(outfits_md_text, outfit_variant)
    comfyui_line = _extract_comfyui_line(item_lines)
    tag_pool = _split_tag_line(comfyui_line)

    bullets = _extract_bullets_at_indent(item_lines, 4)
    checks: list[FidelityCheck] = []
    index = 0
    for bullet in bullets:
        if bullet.label in _SKIP_OUTFIT_LABELS:
            continue
        index += 1
        block_text = bullet.text()
        region, _ = _classify(block_text)
        fix_tags = _derive_fix_tags(bullet.label, block_text, tag_pool)
        checks.append(
            FidelityCheck(
                id=f"outfits-{index}",
                label_zh=bullet.label,
                pass_criteria=block_text,
                region_hint=region,
                fix_tags=fix_tags,
                source="outfits",
            )
        )
    return checks


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def parse_character_checklist(
    setting_md_text: str,
    outfits_md_text: str,
    outfit_variant: str,
) -> list[FidelityCheck]:
    """Derive the full fidelity checklist for one character + outfit variant.

    Combines ``setting.md``'s ``外型特徵`` bullets with the selected outfit
    item's direct sub-bullets from ``outfits.md``. Raises ``ValueError``
    (never returns an empty list silently) when either source cannot be
    parsed or ``outfit_variant`` does not match any ``[Tag]`` in
    ``outfits.md`` — the error message lists the available variants.
    """
    setting_checks = _parse_setting_checks(setting_md_text)
    outfit_checks = _parse_outfit_checks(outfits_md_text, outfit_variant)
    checklist = setting_checks + outfit_checks
    if not checklist:
        raise ValueError(
            "Parsed checklist is empty — setting.md/outfits.md structure did "
            "not match any known bullet pattern."
        )
    return checklist


def load_character_sources(sheet_source_path: str) -> tuple[str, str]:
    """Read ONLY ``setting.md`` and ``outfits.md`` from ``sheet_source_path``.

    Never reads any other file in the folder (spec: character sources are
    exactly these two files). Validates the path is an existing directory
    via ``Path.resolve()`` + ``is_dir()`` — no further traversal handling is
    needed since we only ever join two fixed, hardcoded filenames onto it.
    """
    folder = Path(sheet_source_path).resolve()
    if not folder.is_dir():
        raise ValueError(f"sheet_source_path is not a directory: {sheet_source_path!r}")

    setting_path = folder / "setting.md"
    outfits_path = folder / "outfits.md"
    if not setting_path.is_file():
        raise FileNotFoundError(f"setting.md not found under {folder}")
    if not outfits_path.is_file():
        raise FileNotFoundError(f"outfits.md not found under {folder}")

    setting_md_text = setting_path.read_text(encoding="utf-8")
    outfits_md_text = outfits_path.read_text(encoding="utf-8")
    return setting_md_text, outfits_md_text

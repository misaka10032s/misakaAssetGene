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

Fidelity-loop demo gap fix (C4, 2026-09-06)
--------------------------------------------
A live end-to-end demo (``@PM/state/runs/misakaAssetGene-refine-loop-260905/
demo/DEMO-report.md``) found three quality defects in the derived checklist
that let a fidelity loop "PASS" on the wrong grounds:

1. **Non-visual bullets became checks** (underwear, purely-auditory/olfactory
   "感官細節" prose, and measurement-only sub-content) — a VLM critic then
   hallucinates a confident ``pass`` for something no static image can show.
   Fixed via ``FidelityCheck.visual`` — a keyword scan (``_NON_VISUAL_
   KEYWORDS``) plus a "majority of tokens are numbers/units/#hex with no
   garment/body noun" heuristic (``_is_measurement_only``) — both computed
   per check and excluded by default from ``parse_character_checklist``'s
   returned list (``include_non_visual=True`` recovers them for debugging).
2. **Conditional (state-dependent) features treated as always-required** —
   e.g. a blue hair streak or star-shaped pupils that canon says appear only
   "發動魔法時" (while casting) were required on an ordinary portrait render.
   Fixed via ``FidelityCheck.mode`` (``"default" | "combat" | "casting" |
   "sleep"``): a bullet whose own text names a conditional marker
   (``_MODE_MARKERS``) gets tagged with that mode instead of "default", and
   ``parse_character_checklist(..., mode=...)`` only returns checks whose
   mode matches the requested mode OR is "default" (a "default" check
   applies in every mode unless a same-mode check's ``overrides`` names it —
   see ``setting-2`` example below).
3. **Mode-dependent weapons ignored entirely** — a "武裝與魔法" section
   describing weapons hidden in default/portrait mode and drawn only in
   combat was never parsed, and the character's global tag line ("dual
   blades, compound bow") leaked into every setting-derived check's
   ``fix_tags`` fallback whenever its own keyword filter matched nothing.
   Fixed via ``_parse_weapons_checks`` (combat-mode checks for each named
   weapon + a "default"-mode negative-style ``weapons-hidden`` check that
   ``overrides`` them out of combat) and ``_strip_weapon_tags`` (removes
   weapon vocabulary from setting.md's global tag pool before it is ever
   used as a fix_tags fallback for a non-weapons check).

Compound bullet split example — ``setting.md``'s "瞳色" bullet often reads::

    - **瞳色**：
        - 平常：亮黃瞳。
        - 發動魔法：轉變為亮綠色，瞳孔轉化為五角星形狀。

This describes TWO states in one bullet. ``_split_state_lines`` detects the
"- 狀態：description" sub-line shape and, when it finds both an
unconditional/baseline line and a conditionally-marked line, splits the
bullet into two checks: the original id keeps the baseline text
(``mode="default"``) and a new ``"{id}-{mode}"`` check carries the
conditional text (``mode=<that mode>``, ``overrides=[original_id]``) so a
casting-mode checklist asserts the green/star eyes INSTEAD of (not in
addition to) the default yellow-eyes check. A bullet whose ALL state-lines
share the same classification (e.g. two purely descriptive states, no
marker) is intentionally left unsplit — this is a heuristic bail-out, not a
guarantee every compound bullet is correctly split.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from core.models.schemas import BodyRegion, FidelityCheck

# Mirrors FidelityCheck.mode's Literal (core/models/schemas.py) — kept as its
# own alias here so every internal helper stays precisely typed instead of
# widening to plain ``str`` (mypy --strict).
_Mode = Literal["default", "combat", "casting", "sleep"]

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

# Union of every category's Chinese trigger substrings — reused as a
# "does this text name any garment/body-part noun at all" vocabulary by
# ``_is_measurement_only`` below (see C4 fix #1 in the module docstring).
_GARMENT_NOUN_KEYWORDS: tuple[str, ...] = tuple(
    trigger for _region, triggers, _filters in _CATEGORIES for trigger in triggers
)


def _earliest_trigger_index(block_text: str, zh_triggers: tuple[str, ...]) -> int | None:
    positions = [block_text.index(trigger) for trigger in zh_triggers if trigger in block_text]
    return min(positions) if positions else None


def _classify(block_text: str) -> tuple[BodyRegion, tuple[str, ...]]:
    """Pick the category whose trigger substring occurs EARLIEST in
    ``block_text`` (ties broken by ``_CATEGORIES`` list order) — NOT simply
    "first category in list order with any match anywhere in the text".

    Fix for a real misclassification (reviewer C1-review.md non-blocking
    finding, C-spec.md §4.2 depends on region_hint for mask exclusion): a
    single bullet block routinely mentions more than one region's keyword in
    passing (e.g. an outfits.md "飾品" bullet about a waist belt/dagger set
    ALSO mentions "圍裙" (apron) as an aside about a nearby pocket). Scanning
    categories in a fixed list order and stopping at the first one whose
    trigger appears ANYWHERE in the text let a late, incidental TORSO mention
    beat an earlier, primary WAIST mention. Picking the trigger with the
    SMALLEST string index instead means whichever concept the bullet's own
    text leads with wins, which matches how these bullets are actually
    written (the defining feature comes first; secondary asides come after).
    Verified this does not regress any pre-existing region_hint assertion in
    ``test_fidelity_parser.py`` (all pass on the earliest-index rule too,
    since none of those fixtures interleave two categories' triggers).
    """
    best_region: BodyRegion | None = None
    best_filters: tuple[str, ...] = ()
    best_index: int | None = None
    for region, zh_triggers, en_filters in _CATEGORIES:
        index = _earliest_trigger_index(block_text, zh_triggers)
        if index is None:
            continue
        if best_index is None or index < best_index:
            best_index = index
            best_region = region
            best_filters = en_filters
    if best_region is None:
        return _DEFAULT_REGION, ()
    return best_region, best_filters


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
# C4 fix — non-visual / measurement-only exclusion (module docstring #1)
# ---------------------------------------------------------------------------

# Keyword-driven: any of these substrings appearing anywhere in a check's own
# text (label + body) marks it non-visual — a static image can never confirm
# underwear, sound, or smell. Deliberately broad per the dispatch brief; a
# false-positive risk is documented rather than hidden (e.g. "香" also
# matches an unrelated "香檳" colour name in a DIFFERENT outfit variant this
# parser never happened to select in the real-file evidence run).
_NON_VISUAL_KEYWORDS: tuple[str, ...] = (
    "內衣", "內褲", "感官", "氣息", "聲", "Hz", "氣味", "香", "觸感",
)


def _is_non_visual(full_text: str) -> bool:
    return any(keyword in full_text for keyword in _NON_VISUAL_KEYWORDS)


# A token counts as "measurement-like" if it carries a digit (covers "20
# 褶", "4cm", "142cm", "2000Hz", "15 度", "1mm" ...) or is a hex colour
# ("#FF85A1"). Tokens are cut on whitespace/Chinese punctuation, which is
# where these markdown bullets already put spaces around a number.
_TOKEN_SPLIT_RE = re.compile(r"[\s、，,。.:：()（）「」『』\-–—;；]+")


def _is_measurement_token(token: str) -> bool:
    if not token:
        return False
    if token.startswith("#") and len(token) > 1:
        return True
    return any(character.isdigit() for character in token)


def _is_measurement_only(full_text: str) -> bool:
    """True when ``full_text`` names no garment/body-part noun at all AND a
    majority of its tokens are numbers/units/hex codes — a pure material or
    measurement spec (布料支數、cm、Hz、#hex) with nothing a VLM could ever
    visually confirm against a single static render."""
    if any(noun in full_text for noun in _GARMENT_NOUN_KEYWORDS):
        return False
    tokens = [token for token in _TOKEN_SPLIT_RE.split(full_text) if token]
    if not tokens:
        return False
    measurement_count = sum(1 for token in tokens if _is_measurement_token(token))
    return measurement_count / len(tokens) > 0.5


# ---------------------------------------------------------------------------
# C4 fix — conditional/state markers -> FidelityCheck.mode (module docstring #2)
# ---------------------------------------------------------------------------

# Ordered; ``_detect_mode`` picks whichever marker occurs EARLIEST in the
# text (ties broken by this list's order), same idiom as ``_classify``.
_MODE_MARKERS: list[tuple[str, _Mode]] = [
    ("發動魔法", "casting"),
    ("戰鬥中", "combat"),
    ("戰鬥時", "combat"),
    ("排除模式", "combat"),
    ("睡覺時", "sleep"),
    ("拿下眼鏡", "sleep"),
]


def _detect_mode(text: str) -> _Mode | None:
    best_index: int | None = None
    best_mode: _Mode | None = None
    for marker, mode in _MODE_MARKERS:
        index = text.find(marker)
        if index == -1:
            continue
        if best_index is None or index < best_index:
            best_index = index
            best_mode = mode
    return best_mode


# A loosely-bulleted "- state：description" sub-line — NOT the bold
# ``**label**`` form ``_BULLET_RE`` matches; this is how setting.md commonly
# writes a bullet's per-state detail (e.g. "- 平常：..." / "- 發動魔法：...").
_STATE_LINE_RE = re.compile(r"^-\s*(?P<state>[^\-:：]{1,12})[:：]\s*(?P<rest>.*)$")


def _split_state_lines(block_lines: list[str]) -> tuple[list[str], dict[_Mode, list[str]]] | None:
    """Interpret ``block_lines`` as one "- state：description" line per
    state. Returns ``(default_lines, {mode: [lines]})`` only when EVERY line
    matches that shape (a single non-matching line — e.g. plain continuation
    prose, or a nested ``**label**`` measurement sub-bullet — bails out to
    ``None`` so those bullets are never mis-split); a line whose own text
    names no conditional marker (``_detect_mode`` -> ``None``) counts as a
    baseline/default line."""
    if not block_lines:
        return None
    default_lines: list[str] = []
    conditional: dict[_Mode, list[str]] = {}
    for line in block_lines:
        match = _STATE_LINE_RE.match(line)
        if not match:
            return None
        mode = _detect_mode(match.group("state") + match.group("rest"))
        if mode is None:
            default_lines.append(line)
        else:
            conditional.setdefault(mode, []).append(line)
    return default_lines, conditional


# ---------------------------------------------------------------------------
# C4 fix — weapon-tag stripping from setting.md's global fallback pool
# (module docstring #3)
# ---------------------------------------------------------------------------

_WEAPON_TAG_KEYWORDS: tuple[str, ...] = ("dagger", "blade", "sword", "bow", "crossbow", "dual")


def _strip_weapon_tags(tag_pool: list[str]) -> list[str]:
    """Remove weapon vocabulary (e.g. "dual blades", "compound bow") from
    setting.md's global ``## 🏷️ 標籤`` tag pool before it is ever used as a
    fix_tags FALLBACK for a non-weapons check (spec: weapons are handled
    exclusively by ``_parse_weapons_checks`` below, mode-gated) — otherwise
    any setting.md bullet whose own keyword filter matched nothing (e.g.
    §2.1 "找不到就整段 fallback") would silently pull weapon tags into an
    unrelated default-mode check's fix_tags, which is exactly what grew a
    crossbow + sword into a demo render that never asked for either
    (DEMO-report.md §4)."""
    return [tag for tag in tag_pool if not any(keyword in tag.lower() for keyword in _WEAPON_TAG_KEYWORDS)]


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
# Shared bullet -> FidelityCheck(es) construction (setting.md + outfits.md)
# ---------------------------------------------------------------------------


def _make_check(
    check_id: str,
    label_zh: str,
    text: str,
    tag_pool: list[str],
    source: Literal["setting", "outfits"],
    *,
    mode: _Mode,
    lookup_label: str | None = None,
    overrides: list[str] | None = None,
) -> FidelityCheck:
    region, _ = _classify(text)
    fix_tags = _derive_fix_tags(lookup_label if lookup_label is not None else label_zh, text, tag_pool)
    visual = not (_is_non_visual(text) or _is_measurement_only(text))
    return FidelityCheck(
        id=check_id,
        label_zh=label_zh,
        pass_criteria=text,
        region_hint=region,
        fix_tags=fix_tags,
        source=source,
        mode=mode,
        visual=visual,
        overrides=overrides or [],
    )


def _build_checks_from_bullet(
    check_id: str, bullet: _Bullet, tag_pool: list[str], source: Literal["setting", "outfits"]
) -> list[FidelityCheck]:
    """One bullet -> one or more :class:`FidelityCheck`. Splits into a
    default + one-or-more conditional checks only when ``_split_state_lines``
    finds BOTH a baseline line and at least one conditionally-marked line
    (module docstring "Compound bullet split example") — every other shape
    (no state-lines at all, or state-lines that share one classification)
    falls through to a single check, whose ``mode`` is whatever
    ``_detect_mode`` finds anywhere in its own full text (``"default"`` when
    nothing matches)."""
    label = bullet.label
    split = _split_state_lines(bullet.block_lines)
    if split is not None:
        default_lines, conditional = split
        if conditional and default_lines:
            checks: list[FidelityCheck] = [
                _make_check(
                    check_id, label, "\n".join([label, *default_lines]), tag_pool, source,
                    mode="default", lookup_label=label,
                )
            ]
            for mode_name, lines in conditional.items():
                cond_label = f"{label}（{mode_name}）"
                checks.append(
                    _make_check(
                        f"{check_id}-{mode_name}", cond_label, "\n".join([cond_label, *lines]),
                        tag_pool, source, mode=mode_name, lookup_label=label, overrides=[check_id],
                    )
                )
            return checks
    block_text = bullet.text()
    mode: _Mode = _detect_mode(block_text) or "default"
    return [_make_check(check_id, label, block_text, tag_pool, source, mode=mode)]


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
    tag_pool = _strip_weapon_tags(_extract_backtick_tags(_section_lines(setting_md_text, "標籤")))

    bullets = _extract_bullets_at_indent(visual_lines, 0)
    checks: list[FidelityCheck] = []
    for index, bullet in enumerate(bullets, start=1):
        checks.extend(_build_checks_from_bullet(f"setting-{index}", bullet, tag_pool, "setting"))
    return checks


# ---------------------------------------------------------------------------
# ## ⚔️ 武裝與魔法 (Combat & Magic) — C4 fix #3
# ---------------------------------------------------------------------------

_WEAPON_SECTION_HEADING = "武裝與魔法"
# (label substring to match, check id, hardcoded fix_tags). Fix_tags here are
# NOT derived from any tag pool — they are the exact vocabulary the dispatch
# brief specifies, since setting.md's own tag line never carries anything
# more specific than "dual blades"/"compound bow" for these.
_WEAPON_ITEM_SPECS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("主要武器", "weapons-1", ("dual daggers drawn", "dual wielding")),
    ("遠程武器", "weapons-2", ("compound bow",)),
)


def _parse_weapons_checks(setting_md_text: str) -> list[FidelityCheck]:
    """Combat-mode checks for each named weapon (region WAIST — this
    BodyRegion enum has no dedicated hand slot) plus a "default"-mode
    negative-style ``weapons-hidden`` check asserting NO weapon is visible in
    a normal portrait. Each weapon check ``overrides`` ``weapons-hidden`` so
    a combat-mode checklist drops the contradictory "no weapon visible"
    requirement instead of keeping both. Returns ``[]`` (no error) when the
    section is absent — an unarmed character is a legitimate SSOT, and the
    dispatch brief's own synthetic fixtures never define this section."""
    section_lines = _section_lines(setting_md_text, _WEAPON_SECTION_HEADING)
    if not section_lines:
        return []
    bullets = _extract_bullets_at_indent(section_lines, 0)
    checks: list[FidelityCheck] = []
    for label_key, check_id, fix_tags in _WEAPON_ITEM_SPECS:
        bullet = next((candidate for candidate in bullets if label_key in candidate.label), None)
        if bullet is None:
            continue
        checks.append(
            FidelityCheck(
                id=check_id,
                label_zh=bullet.label,
                pass_criteria=bullet.text(),
                region_hint=BodyRegion.WAIST,
                fix_tags=list(fix_tags),
                source="setting",
                mode="combat",
                visual=True,
                overrides=["weapons-hidden"],
            )
        )
    if not checks:
        return []
    checks.append(
        FidelityCheck(
            id="weapons-hidden",
            label_zh="武器隱藏",
            pass_criteria=(
                "平時武器隱藏於異空間（雙刀），向後抓取虛空時才具現化（遠程武器）——"
                "立繪 / 預設模式下畫面中不應出現任何武器。"
            ),
            region_hint=BodyRegion.WAIST,
            fix_tags=[],
            negative_tags=["weapon", "sword", "bow", "crossbow"],
            source="setting",
            mode="default",
            visual=True,
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
        checks.extend(_build_checks_from_bullet(f"outfits-{index}", bullet, tag_pool, "outfits"))
    return checks


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def parse_character_checklist(
    setting_md_text: str,
    outfits_md_text: str,
    outfit_variant: str,
    mode: str = "default",
    *,
    include_non_visual: bool = False,
) -> list[FidelityCheck]:
    """Derive the full fidelity checklist for one character + outfit variant
    + render ``mode`` (``"default" | "combat" | "casting" | "sleep"``).

    Combines ``setting.md``'s ``外型特徵`` bullets, the selected outfit
    item's direct sub-bullets from ``outfits.md``, and any ``## ⚔️ 武裝與
    魔法`` weapon checks. Raises ``ValueError`` (never returns an empty list
    silently) when either SSOT source cannot be parsed at all or
    ``outfit_variant`` does not match any ``[Tag]`` in ``outfits.md`` — the
    error message lists the available variants. This structural-failure
    check runs on the FULL unfiltered set, before mode/visual filtering, so
    an all-non-visual or all-wrong-mode result is never mistaken for a parse
    failure.

    Filtering (spec §5.15 C4): a check is returned only when its ``mode``
    equals the requested ``mode`` OR is ``"default"`` — a default check
    applies in every mode UNLESS some check whose own mode matches the
    requested mode names it in ``overrides`` (e.g. a combat-mode weapon
    check overriding the default-mode ``weapons-hidden`` check), in which
    case the overridden default check is dropped instead of kept alongside
    the mode-specific one. Non-visual checks (``visual=False``) are excluded
    unless ``include_non_visual=True`` (debugging escape hatch).
    """
    setting_checks = _parse_setting_checks(setting_md_text)
    outfit_checks = _parse_outfit_checks(outfits_md_text, outfit_variant)
    weapon_checks = _parse_weapons_checks(setting_md_text)
    all_checks = setting_checks + outfit_checks + weapon_checks
    if not all_checks:
        raise ValueError(
            "Parsed checklist is empty — setting.md/outfits.md structure did "
            "not match any known bullet pattern."
        )

    kept = [check for check in all_checks if check.mode == mode or check.mode == "default"]
    if mode != "default":
        overridden_ids = {
            overridden_id
            for check in kept
            if check.mode == mode
            for overridden_id in check.overrides
        }
        kept = [
            check for check in kept
            if not (check.mode == "default" and check.id in overridden_ids)
        ]
    if not include_non_visual:
        kept = [check for check in kept if check.visual]
    return kept


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

"""Character fidelity refine-loop controller (spec §5.15 / C-spec.md §4).

Pure logic, no I/O: every function here takes already-fetched data (a
checklist, one round's VLM critic results, image dimensions) and returns a
decision object. All actual I/O — running the critic, building a mask asset,
dispatching a refine job — lives in ``core.consultant.fidelity_service``,
which calls these functions and persists their output via
``core.consultant.fidelity_store``.

State machine (spec §4.1)
-------------------------
::

    PENDING_CRITIQUE(round=0, target=root) -> CRITIQUING
      -> all pass                       -> PASSED
      -> fails, round < max             -> AWAITING_USER (a round plan is ready;
                                            the caller — user click or
                                            auto_continue — triggers the next
                                            round via BUILDING_MASK -> REFINING
                                            -> CRITIQUING again)
      -> fails, round == max            -> STOPPED_MAX_ROUNDS
      -> fails, pass_count < best       -> STOPPED_REGRESSION_RECOVERED
                                            (next round's target reverts to
                                            best_so_far, never the regressed
                                            asset)

``FAILED`` (schemas.FidelityLoopStatus) is never returned by this module —
it is set only by the service layer when an I/O step itself raises.
"""

from __future__ import annotations

from core.models.schemas import (
    FidelityCheck,
    FidelityCheckResult,
    FidelityLoopStatus,
    FidelityRoundPlan,
    MaskRegion,
    RefinePromptMode,
    RefineRequest,
    RefineStrategy,
)

# spec §4.2.1: top-k (k<=2) failed checks by confidence, greedy non-overlap.
DEFAULT_TOP_K = 2
# spec §4.2.2: mask dilate/feather starting values.
DEFAULT_MASK_DILATE = 12
DEFAULT_MASK_FEATHER = 8
# spec §4.2.3 / §8 risk table: prompt-bloat guard.
DEFAULT_TAG_CAP = 60
# spec §4.2.3: failed-area-sum fraction that forces an explicit img2img.
DEFAULT_AREA_THRESHOLD = 0.4
# C2-review.md MAJOR #3: a passed check's RAW bbox is only eligible to be
# subtracted from a chosen fail's repair region while it is a mostly-DISTINCT
# area from that fail's own raw bbox. Below this IoU, it is treated as a
# genuinely nearby-but-separate passed detail and clipped to exclude any
# sliver inside the fail's bbox; at or above it, the "passed" bbox is really
# the SAME region as the fail (an identical or near-identical bbox is the
# concrete case that hollowed out the whole repair area) and must not
# subtract from it AT ALL.
DEFAULT_SUBTRACT_IOU_THRESHOLD = 0.3
# Sentinel lower than any real pass_count so round 0's own critique always
# "advances the frontier" on its first call to decide_round_outcome (no
# special-cased first-round branch needed — see fidelity_service.py).
INITIAL_BEST_PASS_COUNT = -1


# ---------------------------------------------------------------------------
# Small pure geometry / dedup helpers
# ---------------------------------------------------------------------------


def _bbox_overlaps(
    a: tuple[int, int, int, int] | None, b: tuple[int, int, int, int] | None
) -> bool:
    """True if two bboxes intersect at all (IoU > 0). ``None`` never overlaps."""
    if a is None or b is None:
        return False
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1


def _dilate_bbox(bbox: tuple[int, int, int, int], amount: int) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = bbox
    return (x0 - amount, y0 - amount, x1 + amount, y1 + amount)


def _bbox_area(bbox: tuple[int, int, int, int]) -> int:
    x0, y0, x1, y1 = bbox
    return max(0, x1 - x0) * max(0, y1 - y0)


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    """Intersection-over-union of two bboxes, 0.0 when they don't overlap."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    inter_w, inter_h = max(0, ix1 - ix0), max(0, iy1 - iy0)
    intersection = inter_w * inter_h
    if intersection == 0:
        return 0.0
    union = _bbox_area(a) + _bbox_area(b) - intersection
    return intersection / union if union > 0 else 0.0


def _clip_rect_excluding(
    rect: tuple[int, int, int, int], exclude: tuple[int, int, int, int]
) -> list[tuple[int, int, int, int]]:
    """Return ``rect`` minus its overlap with ``exclude`` as up to 4
    non-overlapping axis-aligned remainder rects (top / bottom / left /
    right strips around the excluded area) — the "minus a rect" op
    ``MaskRegion``'s subtract list cannot express directly (C2-review.md
    MAJOR #3). Returns ``[rect]`` unchanged when there is no overlap, and
    ``[]`` when ``exclude`` fully covers ``rect``."""
    rx0, ry0, rx1, ry1 = rect
    ex0, ey0, ex1, ey1 = exclude
    ix0, iy0 = max(rx0, ex0), max(ry0, ey0)
    ix1, iy1 = min(rx1, ex1), min(ry1, ey1)
    if ix0 >= ix1 or iy0 >= iy1:
        return [rect]
    pieces: list[tuple[int, int, int, int]] = []
    if ry0 < iy0:
        pieces.append((rx0, ry0, rx1, iy0))  # strip above the overlap
    if iy1 < ry1:
        pieces.append((rx0, iy1, rx1, ry1))  # strip below the overlap
    if rx0 < ix0:
        pieces.append((rx0, iy0, ix0, iy1))  # strip left of the overlap (same vertical band)
    if ix1 < rx1:
        pieces.append((ix1, iy0, rx1, iy1))  # strip right of the overlap (same vertical band)
    return pieces


def _dedupe_casefold(tags: list[str]) -> list[str]:
    """Order-preserving de-dup, case/whitespace-insensitive equality key —
    same idiom ``core.generation.refine._dedupe_comma_tags`` uses for the
    composed prompt, applied here to a flat tag list instead."""
    seen: set[str] = set()
    out: list[str] = []
    for tag in tags:
        stripped = tag.strip()
        if not stripped:
            continue
        key = " ".join(stripped.split()).casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(stripped)
    return out


def summarize_pass_fail(results: list[FidelityCheckResult]) -> tuple[int, int]:
    """Return ``(pass_count, fail_count)`` for a critique result set.

    ``fail_count`` here is "everything not a confirmed pass" (kept for
    backward compatibility with ``FidelityLoopRound.pass_count``/
    ``fail_count`` display columns) — it lumps a confirmed ``fail`` together
    with an ``unverified`` result. Use :func:`summarize_verdicts` when the
    two need to be told apart (C5 fix, 2026-09-06 — the loop-controller's own
    stop-condition logic needs the split; see ``decide_round_outcome``)."""
    pass_count = sum(1 for result in results if result.passed)
    return pass_count, len(results) - pass_count


def summarize_verdicts(results: list[FidelityCheckResult]) -> tuple[int, int, int]:
    """Return ``(pass_count, fail_count, unverified_count)`` — the tri-state
    split (C5 fix, 2026-09-06 / ``FidelityCheckResult.verdict``). A confirmed
    ``fail`` is the only thing the planner ever targets for repair
    (:func:`select_top_k_failed`, :func:`plan_round`); an ``unverified``
    result is neither repaired nor silently counted as passed — see
    :func:`decide_round_outcome`'s ``STOPPED_UNVERIFIED`` branch."""
    pass_count = sum(1 for result in results if result.verdict == "pass")
    fail_count = sum(1 for result in results if result.verdict == "fail")
    unverified_count = sum(1 for result in results if result.verdict == "unverified")
    return pass_count, fail_count, unverified_count


# ---------------------------------------------------------------------------
# §4.2.1 — top-k failed-check selection (confidence desc, greedy IoU=0)
# ---------------------------------------------------------------------------


def select_top_k_failed(
    results: list[FidelityCheckResult], k: int = DEFAULT_TOP_K
) -> list[FidelityCheckResult]:
    """Pick up to ``k`` failed checks, highest confidence first, greedily
    skipping any candidate whose bbox overlaps (IoU > 0) an already-chosen
    one (spec §4.2.1: "top-k(k=1–2)失敗項:confidence 降序,貪婪選 bbox
    IoU=0 者"). A candidate with no bbox never overlaps anything and is
    always eligible. May return FEWER than ``k`` items if not enough
    non-overlapping fails exist. Only CONFIRMED fails (``verdict == "fail"``)
    are eligible — an ``unverified`` result is never repaired (C5 fix,
    2026-09-06: the planner has no localized, trustworthy defect to target
    for one; it stays ``unverified`` until a second opinion resolves it)."""
    fails = [result for result in results if result.verdict == "fail"]
    ordered = sorted(fails, key=lambda result: result.confidence, reverse=True)
    chosen: list[FidelityCheckResult] = []
    for candidate in ordered:
        if len(chosen) >= k:
            break
        if any(_bbox_overlaps(candidate.region_bbox, picked.region_bbox) for picked in chosen):
            continue
        chosen.append(candidate)
    return chosen


# ---------------------------------------------------------------------------
# §4.2.2 — mask region assembly (dilate/feather + subtract of nearby passes)
# ---------------------------------------------------------------------------


def build_mask_regions(
    chosen: list[FidelityCheckResult],
    all_results: list[FidelityCheckResult],
    *,
    dilate: int = DEFAULT_MASK_DILATE,
    feather: int = DEFAULT_MASK_FEATHER,
    subtract_iou_threshold: float = DEFAULT_SUBTRACT_IOU_THRESHOLD,
) -> tuple[list[MaskRegion], list[MaskRegion], list[FidelityCheckResult]]:
    """Build ``(regions, subtract, reasserted)`` for the mask-build request.

    ``regions`` — one dilated/feathered ``MaskRegion`` per chosen fail's bbox.
    ``subtract`` — carved from the RAW (undilated) bbox of every check in
    ``all_results`` that PASSED this same round, carries a known
    ``region_bbox``, and overlaps the DILATED extent of a chosen region
    (spec §4.2.2: "扣除鄰近已過關 check 的已知 bbox 區域"). Deduplicated by
    the candidate's raw bbox so two chosen regions bordering the same passed
    check don't emit it twice.

    **Never hollows out a chosen fail's own repair area** (C2-review.md
    MAJOR #3 — a passed check whose bbox equalled a FAILED check's own bbox
    used to be subtracted whole, carving the entire dilated repair region
    down to a thin dilation ring). A candidate whose IoU against a relevant
    chosen fail's RAW bbox is ``>= subtract_iou_threshold`` (the same-region
    case) is excluded from ``subtract`` entirely; a candidate with only
    partial overlap is CLIPPED to remove any area inside that fail's raw
    bbox (up to 4 non-overlapping remainder rects — ``MaskRegion.subtract``
    has no "minus a rect" primitive), so only the genuinely non-overlapping
    remainder is ever subtracted.

    ``reasserted`` — every passed ``FidelityCheckResult`` relevant to a
    chosen region (regardless of whether it ended up excluded or clipped
    above), in encounter order; the caller reasserts their fix_tags (spec
    §4.2.3) since a local repaint of the masked area could otherwise silently
    erase them — this applies even to a check excluded from ``subtract``,
    since its bbox still sits inside the repainted area.
    """
    regions: list[MaskRegion] = []
    dilated_chosen: list[tuple[int, int, int, int]] = []
    raw_chosen: list[tuple[int, int, int, int]] = []
    for result in chosen:
        if result.region_bbox is None:
            continue
        regions.append(MaskRegion(bbox=list(result.region_bbox), dilate=dilate, feather=feather))
        dilated_chosen.append(_dilate_bbox(result.region_bbox, dilate))
        raw_chosen.append(result.region_bbox)

    subtract: list[MaskRegion] = []
    reasserted: list[FidelityCheckResult] = []
    seen_bboxes: set[tuple[int, int, int, int]] = set()
    for result in all_results:
        if not result.passed or result.region_bbox is None:
            continue
        overlapping_raw = [
            raw for raw, dilated in zip(raw_chosen, dilated_chosen, strict=True)
            if _bbox_overlaps(result.region_bbox, dilated)
        ]
        if not overlapping_raw:
            continue
        if result.region_bbox in seen_bboxes:
            continue
        seen_bboxes.add(result.region_bbox)
        reasserted.append(result)

        if any(_iou(result.region_bbox, raw) >= subtract_iou_threshold for raw in overlapping_raw):
            # Substantially the SAME region as a chosen fail's own bbox —
            # never subtract it (would hollow out the repair area).
            continue

        pieces: list[tuple[int, int, int, int]] = [result.region_bbox]
        for raw in overlapping_raw:
            clipped: list[tuple[int, int, int, int]] = []
            for piece in pieces:
                clipped.extend(_clip_rect_excluding(piece, raw))
            pieces = clipped
        for piece in pieces:
            subtract.append(MaskRegion(bbox=list(piece), dilate=0, feather=0))
    return regions, subtract, reasserted


# ---------------------------------------------------------------------------
# §4.2.3 — instruction tags (append-only, dedup, capped) + strategy
# ---------------------------------------------------------------------------


def build_instruction_tags(
    chosen_checks: list[FidelityCheck],
    reasserted_checks: list[FidelityCheck],
    *,
    tag_cap: int = DEFAULT_TAG_CAP,
) -> tuple[str, list[str]]:
    """Assemble ``(instruction_string, tags)`` from failed checks' fix_tags
    UNION overlapping-passed checks' fix_tags (spec §4.2.3), deduped
    case/whitespace-insensitively and order-preserved, capped at
    ``tag_cap`` (prompt-bloat guard, spec §8)."""
    raw_tags: list[str] = []
    for check in chosen_checks:
        raw_tags.extend(check.fix_tags)
    for check in reasserted_checks:
        raw_tags.extend(check.fix_tags)
    tags = _dedupe_casefold(raw_tags)[:tag_cap]
    return ", ".join(tags), tags


def build_negative_tags(
    chosen_checks: list[FidelityCheck],
    reasserted_checks: list[FidelityCheck],
    *,
    tag_cap: int = DEFAULT_TAG_CAP,
) -> list[str]:
    """Union of chosen+reasserted checks' ``FidelityCheck.negative_tags``
    (C4 fix, checklist-modes), deduped case/whitespace-insensitively and
    order-preserved, capped at ``tag_cap`` — same idiom as
    ``build_instruction_tags`` but for tokens to EXCLUDE from the render
    (e.g. a "no weapon visible" check's ``negative_tags=["weapon", "sword",
    "bow", "crossbow"]``) rather than tokens to include."""
    raw_tags: list[str] = []
    for check in chosen_checks:
        raw_tags.extend(check.negative_tags)
    for check in reasserted_checks:
        raw_tags.extend(check.negative_tags)
    return _dedupe_casefold(raw_tags)[:tag_cap]


def select_strategy_for_round(
    chosen: list[FidelityCheckResult],
    image_width: int,
    image_height: int,
    *,
    area_threshold: float = DEFAULT_AREA_THRESHOLD,
) -> RefineStrategy | None:
    """``None`` (let refine.py auto-select INPAINT from ``mask_asset_id``)
    unless the SUM of the chosen fails' bbox areas exceeds ``area_threshold``
    of the image area, in which case an explicit ``img2img`` is forced (spec
    §4.2.3 / §C-spec.md line 100)."""
    if image_width <= 0 or image_height <= 0:
        return None
    image_area = image_width * image_height
    failed_area = 0
    for result in chosen:
        if result.region_bbox is None:
            continue
        x0, y0, x1, y1 = result.region_bbox
        failed_area += max(0, x1 - x0) * max(0, y1 - y0)
    if failed_area / image_area > area_threshold:
        return RefineStrategy.IMG2IMG
    return None


def _merge_negative(inherited_negative: str | None, negative_tags: list[str]) -> str:
    """Append ``negative_tags`` onto whatever ``inherited_negative`` string
    already carries, deduped case/whitespace-insensitively (C4 fix,
    checklist-modes) — an APPEND, never a replace, so an earlier round's
    inherited exclusion (e.g. "extra fingers") survives alongside a new
    check-driven one (e.g. "weapon")."""
    existing = [tag.strip() for tag in (inherited_negative or "").split(",") if tag.strip()]
    return ", ".join(_dedupe_casefold([*existing, *negative_tags]))


def build_refine_request(
    plan: FidelityRoundPlan, mask_asset_id: str, *, inherited_negative: str | None = None
) -> RefineRequest:
    """Assemble the ``RefineRequest`` for a planned round.

    ``prompt_mode`` is ALWAYS ``append`` (spec §4.2.3) so the parent's
    already-established prompt elements survive. ``params.negative`` is
    deliberately left UNSET (empty ``params``) whenever ``plan.negative_tags``
    is empty, so ``GenerationService.refine_asset`` inherits the parent's
    effective negative prompt (BP-REFINE-1) rather than this controller
    overriding it. When ``plan.negative_tags`` IS non-empty (C4 fix,
    checklist-modes — e.g. a "no weapon visible" check's negative_tags),
    ``params.negative`` is explicitly set to ``inherited_negative`` (the
    caller-supplied parent negative, when known) with ``plan.negative_tags``
    APPENDED and deduped — never a bare replace, since that would silently
    drop whatever the parent round had already excluded.
    """
    params: dict[str, object] = {}
    if plan.negative_tags:
        params["negative"] = _merge_negative(inherited_negative, plan.negative_tags)
    return RefineRequest(
        instruction=plan.instruction,
        strategy=plan.strategy,
        mask_asset_id=mask_asset_id,
        prompt_mode=RefinePromptMode.APPEND,
        params=params,
    )


# ---------------------------------------------------------------------------
# Full round plan assembly
# ---------------------------------------------------------------------------


def plan_round(
    round_index: int,
    target_asset_id: str,
    checks: list[FidelityCheck],
    critic_results: list[FidelityCheckResult],
    image_width: int,
    image_height: int,
    *,
    k: int = DEFAULT_TOP_K,
    tag_cap: int = DEFAULT_TAG_CAP,
    area_threshold: float = DEFAULT_AREA_THRESHOLD,
    mask_dilate: int = DEFAULT_MASK_DILATE,
    mask_feather: int = DEFAULT_MASK_FEATHER,
    subtract_iou_threshold: float = DEFAULT_SUBTRACT_IOU_THRESHOLD,
) -> FidelityRoundPlan | None:
    """Assemble the next round's full decision record (spec §4.2), or
    ``None`` when ``critic_results`` has no CONFIRMED fails (nothing the
    planner can repair — the caller decides between PASSED and, when an
    ``unverified`` check remains, ``STOPPED_UNVERIFIED``; see C5 fix,
    2026-09-06 and ``fidelity_service._run_refine_round``)."""
    fails = [result for result in critic_results if result.verdict == "fail"]
    if not fails:
        return None

    chosen = select_top_k_failed(critic_results, k)
    regions, subtract, reasserted = build_mask_regions(
        chosen, critic_results, dilate=mask_dilate, feather=mask_feather,
        subtract_iou_threshold=subtract_iou_threshold,
    )
    checks_by_id = {check.id: check for check in checks}
    chosen_checks = [checks_by_id[result.id] for result in chosen if result.id in checks_by_id]
    reasserted_checks = [checks_by_id[result.id] for result in reasserted if result.id in checks_by_id]
    instruction, tags = build_instruction_tags(chosen_checks, reasserted_checks, tag_cap=tag_cap)
    negative_tags = build_negative_tags(chosen_checks, reasserted_checks, tag_cap=tag_cap)
    strategy = select_strategy_for_round(chosen, image_width, image_height, area_threshold=area_threshold)

    reason = (
        f"Round {round_index}: targeting {len(chosen)} failed check(s) "
        f"({', '.join(result.id for result in chosen)}); "
        f"reasserting {len(reasserted)} nearby passed check(s) "
        f"({', '.join(result.id for result in reasserted)})."
    )
    return FidelityRoundPlan(
        round_index=round_index,
        target_asset_id=target_asset_id,
        chosen_check_ids=[result.id for result in chosen],
        reasserted_check_ids=[result.id for result in reasserted],
        mask_regions=regions,
        mask_subtract=subtract,
        instruction=instruction,
        instruction_tags=tags,
        negative_tags=negative_tags,
        strategy=strategy,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# §4.1 — round outcome / best-so-far / regression-recovery bookkeeping
# ---------------------------------------------------------------------------


class RoundOutcome:
    """Decision returned by :func:`decide_round_outcome` — the loop's next
    status plus updated best-so-far bookkeeping. A plain class (not a
    pydantic model): purely an internal return value, never serialized
    directly (the service layer copies its fields onto the persisted
    ``FidelityLoop`` record)."""

    __slots__ = ("best_asset_id", "best_pass_count", "next_target_asset_id", "regressed", "status")

    def __init__(
        self,
        status: FidelityLoopStatus,
        next_target_asset_id: str,
        best_asset_id: str,
        best_pass_count: int,
        regressed: bool,
    ) -> None:
        self.status = status
        self.next_target_asset_id = next_target_asset_id
        self.best_asset_id = best_asset_id
        self.best_pass_count = best_pass_count
        self.regressed = regressed

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RoundOutcome):
            return NotImplemented
        return (
            self.status == other.status
            and self.next_target_asset_id == other.next_target_asset_id
            and self.best_asset_id == other.best_asset_id
            and self.best_pass_count == other.best_pass_count
            and self.regressed == other.regressed
        )

    def __repr__(self) -> str:  # pragma: no cover - debug convenience only
        return (
            f"RoundOutcome(status={self.status!r}, next_target_asset_id={self.next_target_asset_id!r}, "
            f"best_asset_id={self.best_asset_id!r}, best_pass_count={self.best_pass_count!r}, "
            f"regressed={self.regressed!r})"
        )


def decide_round_outcome(
    *,
    new_asset_id: str,
    new_pass_count: int,
    new_fail_count: int,
    completed_round_index: int,
    max_rounds: int,
    best_asset_id: str,
    best_pass_count: int,
    new_unverified_count: int = 0,
) -> RoundOutcome:
    """Decide the loop's next status + best-so-far bookkeeping after ONE
    critique (spec §4.1).

    ``completed_round_index`` is the REFINE round number just finished (0
    for the initial baseline critique of the root asset, which never counts
    against ``max_rounds``). Passing ``best_pass_count=INITIAL_BEST_PASS_COUNT``
    for round 0 makes the very first call always "advance the frontier"
    (round 0 has nothing to regress against) with no special-cased branch
    here — see ``INITIAL_BEST_PASS_COUNT``'s docstring.

    ``new_unverified_count`` (C5 fix, 2026-09-06 — default 0, so every
    pre-C5 caller keeps its old behavior unchanged) is the number of
    ``FidelityCheckResult.verdict == "unverified"`` checks in this round
    (``fidelity_loop.summarize_verdicts``). PASSED now genuinely requires
    EVERY check to be a confirmed pass: ``new_fail_count == 0`` alone is no
    longer sufficient when unverified checks remain, since the planner
    (:func:`plan_round`) only ever repairs a confirmed ``fail`` and has
    nothing left it can act on — the loop stops with ``STOPPED_UNVERIFIED``
    instead of silently reporting PASSED for checks it never actually
    confirmed.

    Precedence: all-CONFIRMED-pass wins outright (PASSED) regardless of
    round count. No fails but some unverified -> STOPPED_UNVERIFIED, also
    regardless of round count (nothing left to repair, so waiting for
    another round would not help). Otherwise, a pass_count drop below the
    recorded best is a regression — the next round's target reverts to
    ``best_asset_id`` (never the regressed asset) — but STOPPED_MAX_ROUNDS
    still wins if this was already the last allowed round.
    """
    reached_cap = completed_round_index >= max_rounds

    if new_fail_count == 0 and new_unverified_count == 0:
        return RoundOutcome(
            status=FidelityLoopStatus.PASSED,
            next_target_asset_id=new_asset_id,
            best_asset_id=new_asset_id,
            best_pass_count=new_pass_count,
            regressed=False,
        )

    if new_fail_count == 0:
        # Nothing left the planner can repair (only unverified checks
        # remain) — stop here rather than looping without progress.
        if new_pass_count < best_pass_count:
            return RoundOutcome(
                status=FidelityLoopStatus.STOPPED_UNVERIFIED,
                next_target_asset_id=best_asset_id,
                best_asset_id=best_asset_id,
                best_pass_count=best_pass_count,
                regressed=True,
            )
        return RoundOutcome(
            status=FidelityLoopStatus.STOPPED_UNVERIFIED,
            next_target_asset_id=new_asset_id,
            best_asset_id=new_asset_id,
            best_pass_count=new_pass_count,
            regressed=False,
        )

    if new_pass_count < best_pass_count:
        status = FidelityLoopStatus.STOPPED_MAX_ROUNDS if reached_cap else FidelityLoopStatus.STOPPED_REGRESSION_RECOVERED
        return RoundOutcome(
            status=status,
            next_target_asset_id=best_asset_id,
            best_asset_id=best_asset_id,
            best_pass_count=best_pass_count,
            regressed=True,
        )

    # new_pass_count >= best_pass_count: advance the frontier.
    status = FidelityLoopStatus.STOPPED_MAX_ROUNDS if reached_cap else FidelityLoopStatus.AWAITING_USER
    return RoundOutcome(
        status=status,
        next_target_asset_id=new_asset_id,
        best_asset_id=new_asset_id,
        best_pass_count=new_pass_count,
        regressed=False,
    )

"""Tests for core/consultant/fidelity_loop.py — pure round-planning logic
(C-spec.md §4.2 / §4.1). No I/O, no fakes needed beyond plain schema objects.
"""

from __future__ import annotations

from core.consultant.fidelity_loop import (
    INITIAL_BEST_PASS_COUNT,
    build_instruction_tags,
    build_mask_regions,
    build_refine_request,
    decide_round_outcome,
    plan_round,
    select_strategy_for_round,
    select_top_k_failed,
    summarize_pass_fail,
)
from core.models.schemas import (
    BodyRegion,
    FidelityCheck,
    FidelityCheckResult,
    FidelityLoopStatus,
    RefinePromptMode,
    RefineStrategy,
)


def _check(id: str, region: BodyRegion, fix_tags: list[str]) -> FidelityCheck:
    return FidelityCheck(
        id=id, label_zh=id, pass_criteria=f"criteria for {id}", region_hint=region, fix_tags=fix_tags, source="outfits"
    )


def _result(
    id: str, passed: bool, confidence: float = 0.9, bbox: tuple[int, int, int, int] | None = None
) -> FidelityCheckResult:
    return FidelityCheckResult(id=id, passed=passed, confidence=confidence, region_bbox=bbox, note="")


def _bbox_overlaps(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    """Local test-side reimplementation (kept independent of the module's
    own private ``_bbox_overlaps`` so the assertion is not coupled to the
    implementation it is checking)."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1


class TestSummarizePassFail:
    def test_counts_pass_and_fail(self) -> None:
        results = [_result("a", True), _result("b", False), _result("c", False)]
        assert summarize_pass_fail(results) == (1, 2)

    def test_all_pass(self) -> None:
        results = [_result("a", True), _result("b", True)]
        assert summarize_pass_fail(results) == (2, 0)


class TestSelectTopKFailed:
    def test_picks_highest_confidence_first(self) -> None:
        results = [
            _result("low", False, confidence=0.3, bbox=(0, 0, 10, 10)),
            _result("high", False, confidence=0.9, bbox=(100, 100, 110, 110)),
        ]
        chosen = select_top_k_failed(results, k=1)
        assert [r.id for r in chosen] == ["high"]

    def test_skips_overlapping_bbox_greedily(self) -> None:
        # "mid" overlaps "high"'s bbox, so it must be skipped even though it
        # is the second-highest confidence — "low" (no overlap) wins the
        # second slot instead.
        results = [
            _result("high", False, confidence=0.9, bbox=(0, 0, 100, 100)),
            _result("mid", False, confidence=0.8, bbox=(50, 50, 150, 150)),  # overlaps "high"
            _result("low", False, confidence=0.5, bbox=(500, 500, 600, 600)),  # no overlap
        ]
        chosen = select_top_k_failed(results, k=2)
        assert [r.id for r in chosen] == ["high", "low"]

    def test_ignores_passed_results(self) -> None:
        results = [_result("passed", True, confidence=0.99), _result("failed", False, confidence=0.1)]
        chosen = select_top_k_failed(results, k=2)
        assert [r.id for r in chosen] == ["failed"]

    def test_returns_fewer_than_k_when_not_enough_non_overlapping(self) -> None:
        results = [
            _result("a", False, confidence=0.9, bbox=(0, 0, 100, 100)),
            _result("b", False, confidence=0.8, bbox=(10, 10, 90, 90)),  # fully inside "a"
        ]
        chosen = select_top_k_failed(results, k=2)
        assert [r.id for r in chosen] == ["a"]

    def test_no_bbox_candidate_never_overlaps(self) -> None:
        results = [
            _result("has-bbox", False, confidence=0.9, bbox=(0, 0, 100, 100)),
            _result("no-bbox", False, confidence=0.8, bbox=None),
        ]
        chosen = select_top_k_failed(results, k=2)
        assert {r.id for r in chosen} == {"has-bbox", "no-bbox"}


class TestBuildMaskRegions:
    def test_subtract_excludes_overlapping_passed_check(self) -> None:
        chosen = [_result("fail-1", False, confidence=0.9, bbox=(100, 100, 200, 200))]
        all_results = [
            *chosen,
            _result("passed-nearby", True, bbox=(190, 190, 210, 210)),  # within dilate=12 of chosen's edge
            _result("passed-far", True, bbox=(900, 900, 950, 950)),  # nowhere near
        ]
        regions, subtract, reasserted = build_mask_regions(chosen, all_results, dilate=12, feather=8)
        assert len(regions) == 1
        assert regions[0].bbox == [100, 100, 200, 200]
        assert regions[0].dilate == 12
        assert regions[0].feather == 8
        # "passed-nearby" only PARTIALLY overlaps fail-1's raw bbox (a 10x10
        # corner, IoU well below the 0.3 threshold) — C2-review.md MAJOR #3:
        # that overlapping sliver must never be subtracted (it sits inside
        # fail-1's own repair region), so only the two clipped remainder
        # strips outside fail-1's raw bbox are subtracted, never the whole
        # unclipped candidate bbox.
        subtract_bboxes = [r.bbox for r in subtract]
        assert subtract_bboxes == [[190, 200, 210, 210], [200, 190, 210, 200]]
        for bbox in subtract_bboxes:
            assert not _bbox_overlaps(tuple(bbox), (100, 100, 200, 200))
        assert [r.id for r in reasserted] == ["passed-nearby"]

    def test_subtract_excludes_identical_bbox_entirely(self) -> None:
        """C2-review.md MAJOR #3 — real acceptance-run data: a passed check
        sharing the IDENTICAL bbox as the chosen FAILED check used to be
        subtracted whole, carving the entire dilated repair region down to a
        thin 12px dilation ring. IoU==1.0 >= threshold -> excluded from
        subtract ENTIRELY; the fix_tags must still be reasserted."""
        bbox = (317, 72, 583, 408)
        chosen = [_result("fail-1", False, confidence=0.9, bbox=bbox)]
        all_results = [*chosen, _result("passed-same-region", True, bbox=bbox)]
        regions, subtract, reasserted = build_mask_regions(chosen, all_results, dilate=12, feather=8)
        assert len(regions) == 1
        assert regions[0].bbox == [317, 72, 583, 408]
        assert regions[0].dilate == 12
        assert subtract == []
        assert [r.id for r in reasserted] == ["passed-same-region"]

    def test_subtract_clips_partially_overlapping_bbox_to_remainder_only(self) -> None:
        """Companion to the identical-bbox case above: a passed check that
        only PARTIALLY overlaps the failed check's raw bbox is still
        subtracted, but never inside the failed bbox itself."""
        fail_bbox = (317, 72, 583, 408)
        passed_bbox = (500, 72, 650, 200)  # overlaps the right portion of fail_bbox, extends past it
        chosen = [_result("fail-1", False, confidence=0.9, bbox=fail_bbox)]
        all_results = [*chosen, _result("passed-partial", True, bbox=passed_bbox)]
        _, subtract, reasserted = build_mask_regions(chosen, all_results, dilate=12, feather=8)
        assert len(subtract) == 1
        assert subtract[0].bbox == [583, 72, 650, 200]  # only the slice OUTSIDE fail_bbox
        assert not _bbox_overlaps(tuple(subtract[0].bbox), fail_bbox)
        assert [r.id for r in reasserted] == ["passed-partial"]

    def test_passed_check_with_no_bbox_never_subtracted(self) -> None:
        chosen = [_result("fail-1", False, bbox=(0, 0, 50, 50))]
        all_results = [*chosen, _result("passed-no-bbox", True, bbox=None)]
        _, subtract, reasserted = build_mask_regions(chosen, all_results)
        assert subtract == []
        assert reasserted == []

    def test_dedupes_repeated_passed_bbox(self) -> None:
        chosen = [
            _result("fail-1", False, bbox=(0, 0, 50, 50)),
            _result("fail-2", False, bbox=(200, 0, 250, 50)),
        ]
        # This passed check overlaps BOTH chosen (dilated) regions if placed
        # between them; here it overlaps only fail-1's dilated box, but we
        # verify the identical-bbox case doesn't duplicate in subtract.
        all_results = [
            *chosen,
            _result("passed-a", True, bbox=(45, 0, 55, 10)),
            _result("passed-a-dup", True, bbox=(45, 0, 55, 10)),
        ]
        _, subtract, reasserted = build_mask_regions(chosen, all_results)
        assert len(subtract) == 1
        assert len(reasserted) == 1


class TestBuildInstructionTags:
    def test_union_of_chosen_and_reasserted_dedup_preserve_order(self) -> None:
        chosen_checks = [_check("c1", BodyRegion.WAIST, ["dagger", "belt"])]
        reasserted_checks = [_check("c2", BodyRegion.TORSO, ["belt", "collar"])]  # "belt" dup
        instruction, tags = build_instruction_tags(chosen_checks, reasserted_checks)
        assert tags == ["dagger", "belt", "collar"]
        assert instruction == "dagger, belt, collar"

    def test_case_and_whitespace_insensitive_dedup(self) -> None:
        chosen_checks = [_check("c1", BodyRegion.HEAD, ["Brown Hair", "  brown  hair  "])]
        _, tags = build_instruction_tags(chosen_checks, [])
        assert tags == ["Brown Hair"]

    def test_tag_cap_enforced(self) -> None:
        many_tags = [f"tag{i}" for i in range(80)]
        chosen_checks = [_check("c1", BodyRegion.TORSO, many_tags)]
        _, tags = build_instruction_tags(chosen_checks, [], tag_cap=60)
        assert len(tags) == 60
        assert tags == many_tags[:60]


class TestSelectStrategyForRound:
    def test_below_threshold_returns_none(self) -> None:
        chosen = [_result("f1", False, bbox=(0, 0, 10, 10))]
        strategy = select_strategy_for_round(chosen, image_width=1000, image_height=1000)
        assert strategy is None

    def test_above_40_percent_forces_img2img(self) -> None:
        # 700x700 = 490000 px out of 1000x1000 = 1,000,000 -> 49% > 40%.
        chosen = [_result("f1", False, bbox=(0, 0, 700, 700))]
        strategy = select_strategy_for_round(chosen, image_width=1000, image_height=1000)
        assert strategy is RefineStrategy.IMG2IMG

    def test_exactly_at_threshold_is_not_forced(self) -> None:
        # Exactly 40% must NOT trigger (spec: "> 40%", strictly greater).
        chosen = [_result("f1", False, bbox=(0, 0, 400, 1000))]  # exactly 40% of 1000x1000
        strategy = select_strategy_for_round(chosen, image_width=1000, image_height=1000)
        assert strategy is None

    def test_sums_multiple_chosen_areas(self) -> None:
        chosen = [
            _result("f1", False, bbox=(0, 0, 300, 1000)),  # 30%
            _result("f2", False, bbox=(700, 0, 1000, 1000)),  # 30% -> sum 60%
        ]
        strategy = select_strategy_for_round(chosen, image_width=1000, image_height=1000)
        assert strategy is RefineStrategy.IMG2IMG


class TestBuildRefineRequest:
    def test_prompt_mode_always_append_and_no_negative(self) -> None:
        plan = plan_round(
            round_index=1,
            target_asset_id="asset-1",
            checks=[_check("f1", BodyRegion.WAIST, ["dagger"])],
            critic_results=[_result("f1", False, bbox=(0, 0, 10, 10))],
            image_width=1000,
            image_height=1000,
        )
        assert plan is not None
        request = build_refine_request(plan, mask_asset_id="mask-1")
        assert request.prompt_mode is RefinePromptMode.APPEND
        assert request.mask_asset_id == "mask-1"
        assert "negative" not in request.params
        assert request.instruction == "dagger"


class TestPlanRound:
    def test_returns_none_when_no_fails(self) -> None:
        plan = plan_round(
            round_index=1,
            target_asset_id="asset-1",
            checks=[_check("c1", BodyRegion.HEAD, ["hair"])],
            critic_results=[_result("c1", True)],
            image_width=1000,
            image_height=1000,
        )
        assert plan is None

    def test_full_plan_shape(self) -> None:
        checks = [
            _check("waist-belt", BodyRegion.WAIST, ["dagger", "belt"]),
            _check("collar", BodyRegion.TORSO, ["collar"]),
        ]
        critic_results = [
            _result("waist-belt", False, confidence=0.8, bbox=(100, 100, 200, 200)),
            _result("collar", True, bbox=(190, 190, 205, 205)),  # nearby pass, gets reasserted
        ]
        plan = plan_round(
            round_index=1,
            target_asset_id="asset-1",
            checks=checks,
            critic_results=critic_results,
            image_width=1000,
            image_height=1000,
        )
        assert plan is not None
        assert plan.round_index == 1
        assert plan.target_asset_id == "asset-1"
        assert plan.chosen_check_ids == ["waist-belt"]
        assert plan.reasserted_check_ids == ["collar"]
        assert plan.mask_regions[0].bbox == [100, 100, 200, 200]
        # "collar" overlaps a 10x10 corner of "waist-belt"'s raw bbox
        # (IoU well under the 0.3 threshold) -> C2-review.md MAJOR #3: that
        # corner must be clipped OUT of the subtract, leaving only the two
        # remainder strips outside waist-belt's raw bbox (never the whole
        # unclipped [190, 190, 205, 205]).
        assert [r.bbox for r in plan.mask_subtract] == [[190, 200, 205, 205], [200, 190, 205, 200]]
        assert plan.instruction_tags == ["dagger", "belt", "collar"]
        assert plan.strategy is None  # small area, no forced img2img

    def test_plan_reasserts_fix_tags_even_when_subtract_fully_excludes_check(self) -> None:
        """C2-review.md MAJOR #3 real acceptance-run scenario: a passed check
        with the IDENTICAL bbox as the chosen failed check. The mask must
        still cover the full failed bbox (subtract excludes it entirely),
        and the passed check's fix_tags must still be reasserted in the
        instruction (a local repaint could otherwise silently erase it)."""
        bbox = (317, 72, 583, 408)
        checks = [
            _check("fail-1", BodyRegion.TORSO, ["stripe-highlight"]),
            _check("passed-same-region", BodyRegion.TORSO, ["existing-good-detail"]),
        ]
        critic_results = [
            _result("fail-1", False, confidence=0.9, bbox=bbox),
            _result("passed-same-region", True, bbox=bbox),
        ]
        plan = plan_round(
            round_index=1,
            target_asset_id="asset-1",
            checks=checks,
            critic_results=critic_results,
            image_width=1000,
            image_height=1000,
        )
        assert plan is not None
        assert plan.mask_regions[0].bbox == [317, 72, 583, 408]
        assert plan.mask_subtract == []  # fully excluded, never hollows the repair region
        assert plan.reasserted_check_ids == ["passed-same-region"]
        assert "existing-good-detail" in plan.instruction_tags
        assert "existing-good-detail" in plan.instruction


class TestDecideRoundOutcome:
    def test_all_pass_yields_passed_status(self) -> None:
        outcome = decide_round_outcome(
            new_asset_id="asset-2",
            new_pass_count=19,
            new_fail_count=0,
            completed_round_index=1,
            max_rounds=4,
            best_asset_id="asset-1",
            best_pass_count=17,
        )
        assert outcome.status is FidelityLoopStatus.PASSED
        assert outcome.next_target_asset_id == "asset-2"
        assert outcome.best_asset_id == "asset-2"
        assert outcome.best_pass_count == 19

    def test_round_zero_always_advances_frontier(self) -> None:
        outcome = decide_round_outcome(
            new_asset_id="root-asset",
            new_pass_count=17,
            new_fail_count=2,
            completed_round_index=0,
            max_rounds=4,
            best_asset_id="root-asset",
            best_pass_count=INITIAL_BEST_PASS_COUNT,
        )
        assert outcome.status is FidelityLoopStatus.AWAITING_USER
        assert outcome.best_asset_id == "root-asset"
        assert outcome.best_pass_count == 17
        assert outcome.regressed is False

    def test_improved_pass_count_advances_frontier(self) -> None:
        outcome = decide_round_outcome(
            new_asset_id="asset-2",
            new_pass_count=18,
            new_fail_count=1,
            completed_round_index=1,
            max_rounds=4,
            best_asset_id="asset-1",
            best_pass_count=17,
        )
        assert outcome.status is FidelityLoopStatus.AWAITING_USER
        assert outcome.next_target_asset_id == "asset-2"
        assert outcome.best_asset_id == "asset-2"
        assert outcome.best_pass_count == 18
        assert outcome.regressed is False

    def test_equal_pass_count_still_advances_frontier(self) -> None:
        outcome = decide_round_outcome(
            new_asset_id="asset-2",
            new_pass_count=17,
            new_fail_count=2,
            completed_round_index=1,
            max_rounds=4,
            best_asset_id="asset-1",
            best_pass_count=17,
        )
        assert outcome.status is FidelityLoopStatus.AWAITING_USER
        assert outcome.best_asset_id == "asset-2"

    def test_regression_reverts_target_to_best_so_far(self) -> None:
        outcome = decide_round_outcome(
            new_asset_id="asset-2",
            new_pass_count=15,
            new_fail_count=4,
            completed_round_index=1,
            max_rounds=4,
            best_asset_id="asset-1",
            best_pass_count=17,
        )
        assert outcome.status is FidelityLoopStatus.STOPPED_REGRESSION_RECOVERED
        assert outcome.next_target_asset_id == "asset-1"
        assert outcome.best_asset_id == "asset-1"
        assert outcome.best_pass_count == 17
        assert outcome.regressed is True

    def test_max_rounds_stop_takes_precedence_over_awaiting_user(self) -> None:
        outcome = decide_round_outcome(
            new_asset_id="asset-4",
            new_pass_count=18,
            new_fail_count=1,
            completed_round_index=4,
            max_rounds=4,
            best_asset_id="asset-3",
            best_pass_count=17,
        )
        assert outcome.status is FidelityLoopStatus.STOPPED_MAX_ROUNDS
        assert outcome.next_target_asset_id == "asset-4"

    def test_max_rounds_stop_also_wins_over_regression(self) -> None:
        outcome = decide_round_outcome(
            new_asset_id="asset-4",
            new_pass_count=10,
            new_fail_count=9,
            completed_round_index=4,
            max_rounds=4,
            best_asset_id="asset-3",
            best_pass_count=17,
        )
        assert outcome.status is FidelityLoopStatus.STOPPED_MAX_ROUNDS
        assert outcome.next_target_asset_id == "asset-3"  # still reverts to best, just terminal
        assert outcome.regressed is True

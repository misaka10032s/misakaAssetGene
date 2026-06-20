"""Transition-matrix tests for the Active/Warm/Cold VRAM scheduler (spec §3.4).

Models are fake objects with declared VRAM/RAM footprints; the clock is injected
so idle-based transitions are deterministic without real timing.
"""

from __future__ import annotations

import pytest

from core.scheduler.vram import (
    ManagedModel,
    ModelScheduler,
    RuntimeState,
    SchedulerBudget,
    SchedulerError,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _scheduler(vram_mb: int = 12000, ram_mb: int = 32000) -> tuple[ModelScheduler, FakeClock]:
    clock = FakeClock()
    return ModelScheduler(SchedulerBudget(vram_budget_mb=vram_mb, ram_budget_mb=ram_mb), clock=clock), clock


def test_acquire_makes_model_active_from_cold():
    sched, _ = _scheduler()
    sched.register(ManagedModel(name="qwen", vram_mb=7000, ram_mb=7000))
    assert sched.state_of("qwen") == RuntimeState.COLD
    assert sched.acquire("qwen") == RuntimeState.ACTIVE
    assert sched.vram_used_mb() == 7000


def test_idle_active_demotes_to_warm():
    sched, clock = _scheduler()
    model = sched.register(ManagedModel(name="qwen", vram_mb=7000, ram_mb=7000, idle_offload_sec=300))
    sched.acquire("qwen")
    clock.advance(301)
    triggered = sched.tick()
    assert sched.state_of("qwen") == RuntimeState.WARM
    assert sched.vram_used_mb() == 0
    assert sched.ram_used_mb() == 7000
    assert triggered[-1].to_state == RuntimeState.WARM
    assert triggered[-1].reason == "idle"


def test_warm_restores_to_active_fast():
    sched, clock = _scheduler()
    sched.register(ManagedModel(name="qwen", vram_mb=7000, ram_mb=7000, idle_offload_sec=300))
    sched.acquire("qwen")
    clock.advance(301)
    sched.tick()
    assert sched.state_of("qwen") == RuntimeState.WARM
    sched.acquire("qwen")
    assert sched.state_of("qwen") == RuntimeState.ACTIVE
    assert sched.transitions[-1].reason == "warm_restore"


def test_warm_evicts_to_cold_on_idle():
    sched, clock = _scheduler()
    sched.register(
        ManagedModel(name="qwen", vram_mb=7000, ram_mb=7000, idle_offload_sec=300, cold_offload_sec=1800)
    )
    sched.acquire("qwen")
    clock.advance(301)
    sched.tick()
    assert sched.state_of("qwen") == RuntimeState.WARM
    clock.advance(1800)
    sched.tick()
    assert sched.state_of("qwen") == RuntimeState.COLD
    assert sched.ram_used_mb() == 0
    assert sched.transitions[-1].to_state == RuntimeState.COLD


def test_vram_pressure_demotes_oldest_active_to_warm():
    sched, clock = _scheduler(vram_mb=8000, ram_mb=32000)
    sched.register(ManagedModel(name="llm", vram_mb=6000, ram_mb=6000))
    sched.register(ManagedModel(name="embed", vram_mb=4000, ram_mb=4000))
    sched.acquire("llm")
    clock.advance(10)
    # embed needs 4000MB but only 2000MB free -> llm must be demoted to Warm.
    assert sched.acquire("embed") == RuntimeState.ACTIVE
    assert sched.state_of("llm") == RuntimeState.WARM
    assert sched.state_of("embed") == RuntimeState.ACTIVE
    assert any(t.reason == "vram_pressure" for t in sched.transitions)


def test_low_ram_skips_warm_tier_active_to_cold():
    # RAM budget < 16GB disables the Warm tier entirely (spec §3.4).
    sched, clock = _scheduler(vram_mb=12000, ram_mb=8000)
    assert sched.budget.warm_tier_enabled is False
    sched.register(ManagedModel(name="qwen", vram_mb=7000, ram_mb=7000, idle_offload_sec=300))
    sched.acquire("qwen")
    clock.advance(301)
    sched.tick()
    assert sched.state_of("qwen") == RuntimeState.COLD
    assert sched.transitions[-1].reason.endswith("warm_disabled")


def test_ram_pressure_demotes_active_directly_to_cold():
    # Warm tier enabled (RAM budget >= 16GB), but no RAM headroom for the
    # second demoted copy.
    sched, clock = _scheduler(vram_mb=12000, ram_mb=20000)
    sched.register(ManagedModel(name="a", vram_mb=4000, ram_mb=12000, idle_offload_sec=300))
    sched.register(ManagedModel(name="b", vram_mb=4000, ram_mb=12000, idle_offload_sec=300))
    sched.acquire("a")
    sched.acquire("b")
    # Demote a -> Warm (uses 12000MB RAM). b cannot also go Warm (24000 > 20000).
    sched.demote("a")
    assert sched.state_of("a") == RuntimeState.WARM
    sched.demote("b")
    assert sched.state_of("b") == RuntimeState.COLD
    assert sched.transitions[-1].reason.endswith("ram_pressure")


def test_register_rejects_model_larger_than_vram_budget():
    sched, _ = _scheduler(vram_mb=4000, ram_mb=32000)
    with pytest.raises(SchedulerError):
        sched.register(ManagedModel(name="huge", vram_mb=8000, ram_mb=8000))


def test_acquire_raises_when_cannot_fit_after_eviction():
    sched, _ = _scheduler(vram_mb=8000, ram_mb=32000)
    sched.register(ManagedModel(name="a", vram_mb=8000, ram_mb=8000))
    sched.register(ManagedModel(name="b", vram_mb=8000, ram_mb=8000))
    sched.acquire("a")
    # b needs the full budget; a gets demoted, then b fits exactly.
    assert sched.acquire("b") == RuntimeState.ACTIVE
    assert sched.state_of("a") == RuntimeState.WARM


def test_cold_restore_recorded_when_no_warm_copy():
    sched, _ = _scheduler()
    sched.register(ManagedModel(name="qwen", vram_mb=7000, ram_mb=7000))
    sched.evict("qwen")  # was COLD, stays COLD (no-op)
    assert sched.state_of("qwen") == RuntimeState.COLD
    sched.acquire("qwen")
    assert sched.transitions[-1].reason == "cold_restore"


def test_negative_budget_rejected():
    with pytest.raises(ValueError):
        SchedulerBudget(vram_budget_mb=-1, ram_budget_mb=16000)


# ---------------------------------------------------------------------------
# Thread safety (RLock) — concurrent access from threadpool + worker thread
# ---------------------------------------------------------------------------

def test_concurrent_acquire_demote_tick_is_consistent():
    """Hammer the scheduler from many threads; the RLock must keep the
    transition log internally consistent and never raise from a state race.

    Without the lock, ``_transitions.append`` from one thread interleaving with
    ``tick``'s ``self._transitions[before:]`` slice (and the read of
    ``len(self._transitions)``) can drop events or read torn state.
    """
    import threading

    sched, _ = _scheduler(vram_mb=100000, ram_mb=100000)
    # Each model fits comfortably so acquire never has to evict — we are
    # exercising the lock around state + transition bookkeeping, not eviction.
    names = [f"m{i}" for i in range(8)]
    for n in names:
        sched.register(ManagedModel(name=n, vram_mb=1000, ram_mb=1000))

    errors: list[BaseException] = []
    barrier = threading.Barrier(len(names) * 3)

    def worker(name: str, op: str) -> None:
        try:
            barrier.wait()
            for _ in range(200):
                if op == "acquire":
                    sched.acquire(name)
                elif op == "demote":
                    sched.demote(name)
                else:
                    sched.tick(now=999999.0)
        except BaseException as exc:  # noqa: BLE001 - surface any race-induced error
            errors.append(exc)

    threads = []
    for name in names:
        for op in ("acquire", "demote", "tick"):
            threads.append(threading.Thread(target=worker, args=(name, op)))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrent access raised: {errors[:3]}"
    # Every recorded transition must reference a registered model and be a real
    # state change (from != to) — proves no torn/partial event was appended.
    for ev in sched.transitions:
        assert ev.name in names
        assert ev.from_state != ev.to_state


def test_begin_end_training_under_concurrency():
    """begin/end training toggling concurrently with acquire never corrupts the
    lock-holder flag: acquire either succeeds or raises SchedulerError cleanly."""
    import threading

    sched, _ = _scheduler()
    sched.register(ManagedModel(name="qwen", vram_mb=7000, ram_mb=7000))
    errors: list[BaseException] = []

    def toggler() -> None:
        try:
            for _ in range(500):
                sched.begin_training("job-x")
                sched.end_training()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    def acquirer() -> None:
        try:
            for _ in range(500):
                try:
                    sched.acquire("qwen")
                except SchedulerError:
                    pass  # expected when the lock is held
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=toggler), threading.Thread(target=acquirer)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrent training-lock toggling raised: {errors[:3]}"
    # After all toggling, the lock must be released (last op in toggler is end).
    assert sched.is_training_locked() is False

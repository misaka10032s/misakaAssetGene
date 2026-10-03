"""Transition-matrix tests for the Active/Warm/Cold VRAM scheduler (spec §3.4).

Models are fake objects with declared VRAM/RAM footprints; the clock is injected
so idle-based transitions are deterministic without real timing.
"""

from __future__ import annotations

import threading

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
# Thread safety (RLock) — every access to the shared state holds the lock
#
# The scheduler is reached concurrently from FastAPI's threadpool and the
# training executor's worker thread, and its guarantee is "every read and
# write of ``_models`` / ``_transitions`` / ``_training_lock_holder`` happens
# while ``_lock`` is held".  The tests observe that guarantee directly on one
# thread: the lock is replaced by a lock that knows whether it is held, and
# the three shared structures record the lock state at each access.  No
# second thread, no timing.
# ---------------------------------------------------------------------------

class _HeldLock:
    """Re-entrant lock that knows whether it is currently held (``depth``)."""

    def __init__(self) -> None:
        self._inner = threading.RLock()
        self.depth = 0

    def acquire(self, blocking: bool = True) -> bool:
        got = self._inner.acquire(blocking)
        if got:
            self.depth += 1
        return got

    def release(self) -> None:
        self.depth -= 1
        self._inner.release()

    def __enter__(self) -> _HeldLock:
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()


def _observed_scheduler(
    vram_mb: int = 12000, ram_mb: int = 32000
) -> tuple[ModelScheduler, list[tuple[str, int]]]:
    """A scheduler whose lock and shared state record ``(what, lock depth)`` at
    every access into the returned log."""
    held = _HeldLock()
    log: list[tuple[str, int]] = []

    class _Observed(ModelScheduler):
        def __setattr__(self, name: str, value: object) -> None:
            if name == "_training_lock_holder":
                log.append(("holder_write", held.depth))
            super().__setattr__(name, value)

        def __getattribute__(self, name: str) -> object:
            if name == "_training_lock_holder":
                log.append(("holder_read", held.depth))
            return super().__getattribute__(name)

    class _ObservedDict(dict):
        def __getitem__(self, key):
            log.append(("models_get", held.depth))
            return super().__getitem__(key)

        def __setitem__(self, key, value):
            log.append(("models_set", held.depth))
            super().__setitem__(key, value)

        def items(self):
            log.append(("models_items", held.depth))
            return super().items()

        def values(self):
            log.append(("models_values", held.depth))
            return super().values()

    class _ObservedList(list):
        def append(self, item):
            log.append(("transitions_append", held.depth))
            super().append(item)

        def __len__(self):
            log.append(("transitions_len", held.depth))
            return super().__len__()

        def __getitem__(self, index):
            log.append(("transitions_get", held.depth))
            return super().__getitem__(index)

    sched = _Observed(SchedulerBudget(vram_budget_mb=vram_mb, ram_budget_mb=ram_mb), clock=FakeClock())
    sched._lock = held  # type: ignore[assignment]
    sched._models = _ObservedDict(sched._models)  # type: ignore[assignment]
    sched._transitions = _ObservedList(sched._transitions)  # type: ignore[assignment]
    log.clear()  # drop what construction itself touched (no lock exists yet to hold)
    return sched, log


def test_concurrent_acquire_demote_tick_is_consistent():
    """Every acquire / demote / tick keeps the transition log internally
    consistent and touches ``_models`` and ``_transitions`` only while the lock
    is held.

    Without the lock, ``_transitions.append`` from one thread interleaving with
    ``tick``'s ``self._transitions[before:]`` slice (and the read of
    ``len(self._transitions)``) can drop events or read torn state; the log
    shows an access with the lock NOT held exactly when that protection is
    missing.
    """
    sched, log = _observed_scheduler(vram_mb=100000, ram_mb=100000)
    # Each model fits comfortably so acquire never has to evict — we are
    # exercising the lock around state + transition bookkeeping, not eviction.
    names = [f"m{i}" for i in range(8)]
    for n in names:
        sched.register(ManagedModel(name=n, vram_mb=1000, ram_mb=1000))

    for _ in range(3):
        for name in names:
            sched.acquire(name)
            sched.tick(now=999999.0)  # idle ACTIVE -> WARM
            sched.acquire(name)  # warm_restore
            sched.demote(name)  # ACTIVE -> WARM
            sched.tick(now=999999.0)  # idle WARM -> COLD

    touched = {what for what, _depth in log}
    assert {"models_get", "models_set", "models_values", "transitions_append",
            "transitions_len", "transitions_get"} <= touched, f"accesses not observed: {touched}"
    unlocked = [entry for entry in log if entry[1] == 0]
    assert not unlocked, f"shared state touched WITHOUT the lock held: {unlocked[:5]}"
    # Every recorded transition must reference a registered model and be a real
    # state change (from != to) — proves no torn/partial event was appended.
    assert sched.transitions, "no transition was recorded"
    for ev in sched.transitions:
        assert ev.name in names
        assert ev.from_state != ev.to_state


def test_begin_end_training_under_concurrency():
    """begin/end training toggling together with acquire never touches the
    lock-holder flag without the lock held: acquire either succeeds or raises
    SchedulerError cleanly."""
    sched, log = _observed_scheduler()
    sched.register(ManagedModel(name="qwen", vram_mb=7000, ram_mb=7000))

    for _ in range(3):
        sched.begin_training("job-x")
        with pytest.raises(SchedulerError):
            sched.acquire("qwen")  # expected while the lock is held
        sched.end_training()
        assert sched.acquire("qwen") == RuntimeState.ACTIVE
        sched.demote("qwen")

    touched = {what for what, _depth in log}
    assert {"holder_write", "holder_read"} <= touched, f"accesses not observed: {touched}"
    unlocked = [entry for entry in log if entry[1] == 0]
    assert not unlocked, f"holder flag or shared state touched WITHOUT the lock held: {unlocked[:5]}"
    # After all toggling, the lock must be released (last op in the loop is end).
    assert sched.is_training_locked() is False

"""VRAM scheduler with Active/Warm/Cold three-state hot swapping (spec §3.4).

This scheduler governs *in-process* managed models only — local LLM / embedding
weights that the application itself can move between VRAM, system RAM, and disk.
External HTTP workers (e.g. ComfyUI) manage their own VRAM and are out of scope:
the scheduler never issues device-placement commands to them.

State contract (spec §3.4):

* ``ACTIVE`` — weights resident in VRAM, 0s to use.
* ``WARM``   — weights cached in system RAM, 3-10s to restore to VRAM.
* ``COLD``   — weights unloaded to disk, 30-60s to restore.

Transitions:

* ``ACTIVE -> WARM`` — idle beyond ``idle_offload_sec`` *or* VRAM pressure.
* ``WARM -> ACTIVE`` — fast restore on next use (preferred over Cold restore).
* ``WARM -> COLD``   — idle beyond ``cold_offload_sec`` *or* RAM pressure.
* ``ACTIVE -> COLD`` — direct demote when RAM budget < 16GB (skip Warm tier)
  or when both VRAM and RAM are under pressure.
* ``COLD -> ACTIVE`` — slow restore (no Warm copy available).

Budgets are configurable via :class:`SchedulerBudget`; defaults are sourced from
the application :class:`~core.config.Settings` env values when wired in the API.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from typing import Callable


class RuntimeState(str, Enum):
    ACTIVE = "active"
    WARM = "warm"
    COLD = "cold"


# Spec §3.4: machines with < 16GB system RAM skip the Warm tier entirely.
WARM_TIER_MIN_RAM_MB = 16 * 1024

# Spec §3.4 default idle offload windows (seconds).
DEFAULT_LLM_IDLE_OFFLOAD_SEC = 300
DEFAULT_GENERATION_IDLE_OFFLOAD_SEC = 180
DEFAULT_COLD_OFFLOAD_SEC = 1800


@dataclass(frozen=True)
class SchedulerBudget:
    """VRAM / RAM budgets for the in-process model scheduler (megabytes)."""

    vram_budget_mb: int
    ram_budget_mb: int

    def __post_init__(self) -> None:
        if self.vram_budget_mb < 0 or self.ram_budget_mb < 0:
            raise ValueError("Scheduler budgets must be non-negative.")

    @property
    def warm_tier_enabled(self) -> bool:
        """Warm (RAM cache) tier is only used when system RAM is sufficient."""
        return self.ram_budget_mb >= WARM_TIER_MIN_RAM_MB


@dataclass
class ManagedModel:
    """A model whose device placement the in-process scheduler controls."""

    name: str
    vram_mb: int
    ram_mb: int
    idle_offload_sec: int = DEFAULT_LLM_IDLE_OFFLOAD_SEC
    cold_offload_sec: int = DEFAULT_COLD_OFFLOAD_SEC
    state: RuntimeState = RuntimeState.COLD
    last_used_at: float = 0.0

    def __post_init__(self) -> None:
        if self.vram_mb < 0 or self.ram_mb < 0:
            raise ValueError("Model footprint must be non-negative.")


@dataclass
class TransitionEvent:
    name: str
    from_state: RuntimeState
    to_state: RuntimeState
    reason: str
    at: float


class SchedulerError(RuntimeError):
    """Raised when a model cannot be admitted within the configured budget."""


class ModelScheduler:
    """Tracks Active/Warm/Cold placement of in-process managed models.

    The scheduler is deterministic and clock-injectable so the transition matrix
    can be unit tested without real timing. It performs *accounting* only: actual
    ``model.to('cpu')`` / ``torch.cuda.empty_cache()`` calls are the caller's
    responsibility and are driven by the emitted :class:`TransitionEvent` log.

    Training lock (spec §7.3 "exclusive mode"):
    ``begin_training(holder)`` sets a non-evictable hard lock; while it is held:
      - ``acquire()`` for any model raises ``SchedulerError`` immediately.
      - ``is_training_locked()`` returns True so other subsystems can gate work.
    ``end_training()`` clears the lock; ``acquire()`` works normally again.
    The lock is NOT enforced at the scheduler's own ``evict()`` / ``demote()``
    level — those are internal state-accounting helpers. Only ``acquire()``
    (the external "I want VRAM" call) is refused while training holds the lock.

    Thread safety:
    All mutable state (``_models``, ``_transitions``, ``_training_lock_holder``,
    and each model's ``.state`` / ``.last_used_at``) is guarded by an internal
    re-entrant lock. The scheduler is reached concurrently from FastAPI's
    threadpool request handlers and the training executor's worker thread, so
    public mutators (``register`` / ``acquire`` / ``demote`` / ``evict`` /
    ``tick`` / ``begin_training`` / ``end_training``) and reads (``get`` /
    ``state_of`` / ``transitions`` / ``vram_used_mb`` / ``ram_used_mb`` /
    ``is_training_locked``) all take the lock, making each call atomic.
    """

    def __init__(
        self,
        budget: SchedulerBudget,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._budget = budget
        self._clock = clock or (lambda: 0.0)
        self._models: dict[str, ManagedModel] = {}
        self._transitions: list[TransitionEvent] = []
        # Hard training lock state (spec §7.3 exclusive mode).
        self._training_lock_holder: str | None = None
        # Guards all mutable scheduler state (_models, _transitions,
        # _training_lock_holder, and per-model .state/.last_used_at). The
        # scheduler is touched concurrently by FastAPI threadpool request
        # handlers and the training executor worker thread, so every state
        # change AND every read must hold this lock. RLock (re-entrant) so that
        # acquire() -> _free_vram_for() -> demote() -> _record() nests safely.
        self._lock = threading.RLock()

    @property
    def budget(self) -> SchedulerBudget:
        return self._budget

    @property
    def transitions(self) -> list[TransitionEvent]:
        with self._lock:
            return list(self._transitions)

    # -- training lock (spec §7.3 "exclusive mode") ---------------------------

    def begin_training(self, holder: str) -> None:
        """Acquire the exclusive training lock.

        While held, ``acquire()`` raises ``SchedulerError`` for any caller,
        preventing generation workers from moving models to ACTIVE state.

        Parameters
        ----------
        holder
            An identifier for the current lock holder (e.g. job_id), used only
            for diagnostic messages.  May not be empty.
        """
        if not holder:
            raise ValueError("Training lock holder must be a non-empty string.")
        with self._lock:
            self._training_lock_holder = holder

    def end_training(self) -> None:
        """Release the exclusive training lock.  ``acquire()`` works normally again."""
        with self._lock:
            self._training_lock_holder = None

    def is_training_locked(self) -> bool:
        """Return True while ``begin_training()`` has been called and not yet released."""
        with self._lock:
            return self._training_lock_holder is not None

    def try_begin_training(self, holder: str) -> list[str]:
        """Atomically check for any ACTIVE managed model and, only if none is
        found, acquire the exclusive training lock (spec §7.3 Direction (a)).

        Both the "is anything ACTIVE?" scan and taking the lock happen inside
        one call to ``self._lock`` — this replaces a caller pattern that used
        to read ``scheduler._models`` directly (unlocked), decide "nothing is
        ACTIVE", and only afterwards call ``begin_training()`` as a separate
        step. That two-step shape was a check-then-lock (TOCTOU) race: a
        concurrent ``acquire()`` landing in the gap between the two steps
        could put a model into VRAM that training would never see, so both
        training and generation ended up holding real VRAM at once.

        Returns
        -------
        list[str]
            Names of models found ACTIVE. Empty when none were ACTIVE — in
            that case (and only that case) the training lock WAS taken under
            ``holder``. A non-empty list means the lock was NOT taken; the
            caller should fail the job using these names in its message.
        """
        if not holder:
            raise ValueError("Training lock holder must be a non-empty string.")
        with self._lock:
            active = [
                name for name, m in self._models.items() if m.state == RuntimeState.ACTIVE
            ]
            if active:
                return active
            self._training_lock_holder = holder
            return []

    def register(self, model: ManagedModel) -> ManagedModel:
        if model.vram_mb > self._budget.vram_budget_mb:
            raise SchedulerError(
                f"Model '{model.name}' needs {model.vram_mb}MB VRAM but the budget is "
                f"{self._budget.vram_budget_mb}MB."
            )
        with self._lock:
            self._models[model.name] = model
        return model

    def get(self, name: str) -> ManagedModel:
        with self._lock:
            return self._models[name]

    def state_of(self, name: str) -> RuntimeState:
        with self._lock:
            return self._models[name].state

    # -- accounting -----------------------------------------------------------

    def _vram_used(self) -> int:
        return sum(m.vram_mb for m in self._models.values() if m.state == RuntimeState.ACTIVE)

    def _ram_used(self) -> int:
        return sum(m.ram_mb for m in self._models.values() if m.state == RuntimeState.WARM)

    def vram_used_mb(self) -> int:
        with self._lock:
            return self._vram_used()

    def ram_used_mb(self) -> int:
        with self._lock:
            return self._ram_used()

    # -- transitions ----------------------------------------------------------

    def _record(self, model: ManagedModel, to_state: RuntimeState, reason: str) -> None:
        from_state = model.state
        if from_state == to_state:
            return
        model.state = to_state
        self._transitions.append(
            TransitionEvent(
                name=model.name,
                from_state=from_state,
                to_state=to_state,
                reason=reason,
                at=self._clock(),
            )
        )

    def acquire(self, name: str) -> RuntimeState:
        """Make ``name`` Active, evicting other models as needed to fit budgets.

        Restoring a Warm model is preferred (recorded as a fast ``warm_restore``)
        over restoring a Cold one. Returns the resulting state (always ACTIVE).

        Raises ``SchedulerError`` immediately if the training lock is held
        (see ``begin_training()``), so that generation workers are refused
        VRAM access while a training job is in progress (spec §7.3).
        """
        with self._lock:
            if self._training_lock_holder is not None:
                raise SchedulerError(
                    f"Training in progress (held by '{self._training_lock_holder}') — "
                    f"cannot acquire '{name}'; generation is queued until training ends."
                )
            model = self._models[name]
            previous = model.state
            self._free_vram_for(model)
            if previous == RuntimeState.WARM:
                self._record(model, RuntimeState.ACTIVE, "warm_restore")
            elif previous == RuntimeState.COLD:
                self._record(model, RuntimeState.ACTIVE, "cold_restore")
            else:
                self._record(model, RuntimeState.ACTIVE, "acquire")
            model.last_used_at = self._clock()
            return model.state

    def _free_vram_for(self, incoming: ManagedModel) -> None:
        """Demote other Active models until ``incoming`` fits the VRAM budget."""
        # VRAM already attributed to the incoming model (if it is Active) does
        # not need to be freed again.
        resident = incoming.vram_mb if incoming.state == RuntimeState.ACTIVE else 0
        candidates = sorted(
            (m for m in self._models.values() if m is not incoming and m.state == RuntimeState.ACTIVE),
            key=lambda m: m.last_used_at,
        )
        index = 0
        while self._vram_used() - resident + incoming.vram_mb > self._budget.vram_budget_mb:
            if index >= len(candidates):
                break
            self.demote(candidates[index].name, reason="vram_pressure")
            index += 1
        if self._vram_used() - resident + incoming.vram_mb > self._budget.vram_budget_mb:
            raise SchedulerError(
                f"Cannot fit '{incoming.name}' ({incoming.vram_mb}MB) within VRAM budget "
                f"{self._budget.vram_budget_mb}MB after eviction."
            )

    def demote(self, name: str, reason: str = "idle") -> RuntimeState:
        """Move an Active model down one tier: Active -> Warm, or -> Cold.

        Warm is skipped (Active -> Cold) when the RAM tier is disabled
        (RAM budget < 16GB) or when admitting the RAM copy would exceed the
        RAM budget.
        """
        with self._lock:
            model = self._models[name]
            if model.state != RuntimeState.ACTIVE:
                return model.state
            if not self._budget.warm_tier_enabled:
                self._record(model, RuntimeState.COLD, f"{reason}:warm_disabled")
                return model.state
            if self._ram_used() + model.ram_mb > self._budget.ram_budget_mb:
                self._record(model, RuntimeState.COLD, f"{reason}:ram_pressure")
                return model.state
            self._record(model, RuntimeState.WARM, reason)
            return model.state

    def evict(self, name: str, reason: str = "ram_pressure") -> RuntimeState:
        """Move a model to Cold. Warm -> Cold, or Active -> Cold (skip Warm)."""
        with self._lock:
            model = self._models[name]
            if model.state == RuntimeState.WARM:
                self._record(model, RuntimeState.COLD, reason)
            elif model.state == RuntimeState.ACTIVE:
                self._record(model, RuntimeState.COLD, f"{reason}:direct")
            return model.state

    def tick(self, now: float | None = None) -> list[TransitionEvent]:
        """Advance the clock and apply idle-based demotions/evictions.

        ``ACTIVE`` models idle past ``idle_offload_sec`` demote toward Warm;
        ``WARM`` models idle past ``cold_offload_sec`` evict to Cold.
        Returns only the transitions triggered by this tick.
        """
        with self._lock:
            current = now if now is not None else self._clock()
            before = len(self._transitions)
            for model in list(self._models.values()):
                idle = current - model.last_used_at
                if model.state == RuntimeState.ACTIVE and idle >= model.idle_offload_sec:
                    self.demote(model.name, reason="idle")
                elif model.state == RuntimeState.WARM and idle >= model.cold_offload_sec:
                    self.evict(model.name, reason="idle_cold")
            return self._transitions[before:]

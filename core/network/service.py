from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Callable, Deque

import httpx

from core.models.schemas import NetworkSnapshot, NetworkTransition
from core.network.state import NetworkMode, NetworkState

# Probe callables: given a list of URLs return whether any is reachable.
ProbeFn = Callable[[list[str]], bool]

MAX_TRANSITION_HISTORY = 20


class NetworkStateService:
    """Derives the effective three-state network state and logs transitions.

    The effective state gates which LLM providers may be used (see
    :meth:`provider_allowed`). Probes are injectable so the state-transition
    matrix can be unit tested without real network access.
    """

    def __init__(
        self,
        cloud_probe: ProbeFn | None = None,
        local_probe: ProbeFn | None = None,
    ) -> None:
        self._cloud_probe = cloud_probe or self._probe_targets
        self._local_probe = local_probe or self._probe_targets
        self._last_state: NetworkState | None = None
        self._transitions: Deque[NetworkTransition] = deque(maxlen=MAX_TRANSITION_HISTORY)

    @property
    def transitions(self) -> list[NetworkTransition]:
        return list(self._transitions)

    def snapshot(
        self,
        mode_value: str,
        provider_urls: list[str],
        local_urls: list[str] | None = None,
    ) -> NetworkSnapshot:
        mode = self._resolve_mode(mode_value)
        state = self._resolve_state(mode, provider_urls, local_urls or [])
        self._record_transition(mode, state)
        return NetworkSnapshot(
            mode=mode,
            state=state,
            reachable=state == NetworkState.ONLINE,
            local_available=state in {NetworkState.ONLINE, NetworkState.DEGRADED},
            summary=self._summarize(mode, state),
            recent_transitions=list(self._transitions),
        )

    def provider_allowed(self, provider_is_cloud: bool, state: NetworkState) -> bool:
        """Gate a provider against the effective state.

        Cloud providers are only permitted when ``ONLINE``. Local providers are
        always permitted (local-only operation is the offline-first default).
        """
        if provider_is_cloud:
            return state == NetworkState.ONLINE
        return True

    # -- internals ------------------------------------------------------------

    def _resolve_mode(self, mode_value: str) -> NetworkMode:
        try:
            return NetworkMode(str(mode_value).strip().lower() or NetworkMode.AUTO.value)
        except ValueError:
            return NetworkMode.AUTO

    def _resolve_state(
        self,
        mode: NetworkMode,
        provider_urls: list[str],
        local_urls: list[str],
    ) -> NetworkState:
        if mode == NetworkMode.ALWAYS_ONLINE:
            # Forced online: skip probes, treat cloud as reachable (spec §11.5).
            return NetworkState.ONLINE
        if mode == NetworkMode.ALWAYS_OFFLINE:
            # Forced offline: never touch the network. If a local LLM backend is
            # configured we are still locally capable -> DEGRADED, else OFFLINE.
            local_up = self._local_probe(local_urls) if local_urls else False
            return NetworkState.DEGRADED if local_up else NetworkState.OFFLINE

        # AUTO: probe cloud reachability, then local backend.
        cloud_targets = [url for url in provider_urls if url.startswith("http")]
        if not cloud_targets:
            cloud_targets = ["https://huggingface.co", "https://github.com"]
        if self._cloud_probe(cloud_targets):
            return NetworkState.ONLINE
        local_up = self._local_probe(local_urls) if local_urls else False
        return NetworkState.DEGRADED if local_up else NetworkState.OFFLINE

    def _record_transition(self, mode: NetworkMode, state: NetworkState) -> None:
        if self._last_state == state:
            return
        self._transitions.appendleft(
            NetworkTransition(
                mode=mode,
                from_state=self._last_state,
                to_state=state,
                at=datetime.now(timezone.utc),
            )
        )
        self._last_state = state

    def _summarize(self, mode: NetworkMode, state: NetworkState) -> str:
        if state == NetworkState.ONLINE:
            return "External network is reachable; cloud providers are available."
        if state == NetworkState.DEGRADED:
            return "Cloud is unreachable; running local-only with the local LLM backend."
        return "Offline: no external network or local LLM backend; AI features fall back to hand-crafted paths."

    def _probe_targets(self, targets: list[str]) -> bool:
        for url in targets:
            try:
                response = httpx.get(url, timeout=1.0, follow_redirects=True)
            except httpx.HTTPError:
                continue
            if response.is_success or response.status_code in {301, 302, 401, 403, 405}:
                return True
        return False

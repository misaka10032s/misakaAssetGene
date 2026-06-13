"""State-transition matrix tests for offline three-state mode (spec §11.5).

Network probes are mocked: ``cloud_probe`` / ``local_probe`` are injected so the
ONLINE / DEGRADED / OFFLINE resolution and provider gating are tested without
real network access.
"""

from __future__ import annotations

import pytest

from core.config import Settings
from core.llm.router import gate_providers
from core.models.schemas import ProviderMode, ProviderName, ProviderSnapshot, ProviderStatus
from core.network.service import NetworkStateService
from core.network.state import NetworkMode, NetworkState

CLOUD_URLS = ["https://api.anthropic.com", "https://api.openai.com/v1"]
LOCAL_URLS = ["http://127.0.0.1:11434/api/tags"]


def _service(cloud_up: bool, local_up: bool) -> NetworkStateService:
    return NetworkStateService(
        cloud_probe=lambda _urls: cloud_up,
        local_probe=lambda _urls: local_up,
    )


# -- AUTO mode state resolution -------------------------------------------------


def test_auto_cloud_reachable_is_online():
    snap = _service(cloud_up=True, local_up=True).snapshot("auto", CLOUD_URLS, LOCAL_URLS)
    assert snap.state == NetworkState.ONLINE
    assert snap.reachable is True
    assert snap.local_available is True


def test_auto_cloud_down_local_up_is_degraded():
    snap = _service(cloud_up=False, local_up=True).snapshot("auto", CLOUD_URLS, LOCAL_URLS)
    assert snap.state == NetworkState.DEGRADED
    assert snap.reachable is False
    assert snap.local_available is True


def test_auto_cloud_down_local_down_is_offline():
    snap = _service(cloud_up=False, local_up=False).snapshot("auto", CLOUD_URLS, LOCAL_URLS)
    assert snap.state == NetworkState.OFFLINE
    assert snap.reachable is False
    assert snap.local_available is False


# -- forced modes ---------------------------------------------------------------


def test_always_online_skips_probes():
    # Even with both probes down, forced online stays ONLINE and never probes.
    calls = {"cloud": 0, "local": 0}

    def cloud(_urls):
        calls["cloud"] += 1
        return False

    def local(_urls):
        calls["local"] += 1
        return False

    service = NetworkStateService(cloud_probe=cloud, local_probe=local)
    snap = service.snapshot("always_online", CLOUD_URLS, LOCAL_URLS)
    assert snap.state == NetworkState.ONLINE
    assert calls == {"cloud": 0, "local": 0}


def test_always_offline_with_local_is_degraded():
    snap = _service(cloud_up=True, local_up=True).snapshot("always_offline", CLOUD_URLS, LOCAL_URLS)
    # Forced offline never treats cloud as reachable, even if it actually is.
    assert snap.state == NetworkState.DEGRADED


def test_always_offline_without_local_is_offline():
    snap = _service(cloud_up=True, local_up=False).snapshot("always_offline", CLOUD_URLS, LOCAL_URLS)
    assert snap.state == NetworkState.OFFLINE


def test_unknown_mode_falls_back_to_auto():
    snap = _service(cloud_up=True, local_up=False).snapshot("garbage", CLOUD_URLS, LOCAL_URLS)
    assert snap.mode == NetworkMode.AUTO
    assert snap.state == NetworkState.ONLINE


# -- transition logging ---------------------------------------------------------


def test_transitions_are_logged_on_change_only():
    service = NetworkStateService(
        cloud_probe=lambda _u: service_state["cloud"],
        local_probe=lambda _u: service_state["local"],
    )
    service_state = {"cloud": True, "local": True}
    service.snapshot("auto", CLOUD_URLS, LOCAL_URLS)  # ONLINE
    service.snapshot("auto", CLOUD_URLS, LOCAL_URLS)  # still ONLINE -> no new entry
    service_state["cloud"] = False
    service.snapshot("auto", CLOUD_URLS, LOCAL_URLS)  # DEGRADED
    service_state["local"] = False
    service.snapshot("auto", CLOUD_URLS, LOCAL_URLS)  # OFFLINE

    transitions = service.transitions
    assert [t.to_state for t in transitions] == [
        NetworkState.OFFLINE,
        NetworkState.DEGRADED,
        NetworkState.ONLINE,
    ]
    # Newest first; the OFFLINE transition came from DEGRADED.
    assert transitions[0].from_state == NetworkState.DEGRADED


# -- provider gating ------------------------------------------------------------


def _providers() -> list[ProviderSnapshot]:
    return [
        ProviderSnapshot(name=ProviderName.OLLAMA, mode=ProviderMode.LOCAL, status=ProviderStatus.READY, configured=True, base_url="http://127.0.0.1:11434"),
        ProviderSnapshot(name=ProviderName.ANTHROPIC, mode=ProviderMode.CLOUD, status=ProviderStatus.READY, configured=True, base_url="https://api.anthropic.com"),
    ]


@pytest.mark.parametrize(
    "state,cloud_status",
    [
        (NetworkState.ONLINE, ProviderStatus.READY),
        (NetworkState.DEGRADED, ProviderStatus.DISABLED),
        (NetworkState.OFFLINE, ProviderStatus.DISABLED),
    ],
)
def test_cloud_providers_gated_by_state(state, cloud_status):
    gated = gate_providers(_providers(), state)
    by_name = {p.name: p for p in gated}
    assert by_name[ProviderName.ANTHROPIC].status == cloud_status
    # Local provider is never gated.
    assert by_name[ProviderName.OLLAMA].status == ProviderStatus.READY


def test_provider_allowed_helper():
    service = _service(cloud_up=False, local_up=True)
    assert service.provider_allowed(provider_is_cloud=True, state=NetworkState.ONLINE) is True
    assert service.provider_allowed(provider_is_cloud=True, state=NetworkState.DEGRADED) is False
    assert service.provider_allowed(provider_is_cloud=True, state=NetworkState.OFFLINE) is False
    assert service.provider_allowed(provider_is_cloud=False, state=NetworkState.OFFLINE) is True

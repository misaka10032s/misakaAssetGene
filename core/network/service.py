from __future__ import annotations

import httpx

from core.models.schemas import NetworkSnapshot
from core.network.state import NetworkMode


class NetworkStateService:
    def snapshot(self, mode_value: str, provider_urls: list[str]) -> NetworkSnapshot:
        try:
            mode = NetworkMode(str(mode_value).strip().lower() or NetworkMode.AUTO.value)
        except ValueError:
            mode = NetworkMode.AUTO

        if mode == NetworkMode.ALWAYS_OFFLINE:
            return NetworkSnapshot(mode=mode, reachable=False, summary="Offline mode is forced by configuration.")
        if mode == NetworkMode.ALWAYS_ONLINE:
            return NetworkSnapshot(mode=mode, reachable=True, summary="Online mode is forced by configuration.")

        probe_targets = [url for url in provider_urls if url.startswith("http")]
        if not probe_targets:
            probe_targets = ["https://huggingface.co", "https://github.com"]
        reachable = self._probe_targets(probe_targets)
        return NetworkSnapshot(
            mode=mode,
            reachable=reachable,
            summary="External network is reachable." if reachable else "External network is unreachable; local-only execution is recommended.",
        )

    def _probe_targets(self, targets: list[str]) -> bool:
        for url in targets:
            try:
                response = httpx.get(url, timeout=1.0, follow_redirects=True)
            except httpx.HTTPError:
                continue
            if response.is_success or response.status_code in {301, 302, 401, 403, 405}:
                return True
        return False

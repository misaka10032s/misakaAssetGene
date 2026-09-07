"""SSRF-hardening tests for ``LocalLlmManager.download_model`` (待回答 #48).

The old guard was a bare ``"huggingface.co" not in url`` substring check with
``follow_redirects=True`` — trivially bypassed by a URL that merely CONTAINS
the substring (``?u=huggingface.co``, ``huggingface.co.evil.example``), and
an allowed host could still redirect the fetch to a private/loopback address
after the check passed. This file proves both halves of the fix:

* ``core.network.safe_url.validate_download_url`` — exact-label host
  allow-list (never substring) + DNS-resolution check against private/
  loopback/link-local/multicast/reserved/unspecified addresses.
* ``download_model``'s manual redirect loop — every hop's ``Location`` is
  re-validated with the SAME function before it is requested, and the chain
  is capped at :data:`core.network.safe_url.MAX_REDIRECT_HOPS`.

No real network access: DNS is faked via a monkeypatched
``core.network.safe_url.socket.getaddrinfo``, and HTTP responses are faked
via a real ``httpx.MockTransport`` (matching this repo's existing convention
in ``tests/test_gemini_vision.py`` — the fake inspects an ACTUAL serialized
``httpx.Request``, not a hand-built dict) wired in place of the module-level
``httpx.stream`` that ``core.llm.local_manager`` calls directly.
"""

from __future__ import annotations

import contextlib
import socket
from pathlib import Path

import httpx
import pytest

from core.config import Settings
from core.llm import local_manager as local_manager_module
from core.network import safe_url as safe_url_module
from core.network.safe_url import UnsafeUrlError, validate_download_url

_PUBLIC_IP = "1.1.1.1"  # unambiguously global — see safe_url doc for why 203.0.113.0/24 is NOT usable here (Python marks it is_private).


def _settings(tmp_path: Path) -> Settings:
    return Settings(MISAKA_MODEL_DIR=str(tmp_path / "models"))


def _install_dns_mock(monkeypatch: pytest.MonkeyPatch, overrides: dict[str, str] | None = None) -> None:
    """Fake ``socket.getaddrinfo`` — every hostname resolves to ``_PUBLIC_IP``
    unless ``overrides`` names it explicitly."""
    overrides = overrides or {}

    def _fake_getaddrinfo(host, port, *args, **kwargs):
        ip = overrides.get(host, _PUBLIC_IP)
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (ip, 0))]

    monkeypatch.setattr(safe_url_module.socket, "getaddrinfo", _fake_getaddrinfo)


def _install_stream_mock(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    """Replace ``core.llm.local_manager.httpx.stream`` with a fake that routes
    through a real ``httpx.MockTransport`` — same shape/semantics as the real
    ``httpx.stream`` context manager (see httpx._api.stream), just backed by
    ``handler`` instead of a live socket."""
    transport = httpx.MockTransport(handler)

    @contextlib.contextmanager
    def _fake_stream(method, url, *, follow_redirects=False, timeout=None, **kwargs):
        with httpx.Client(transport=transport) as client:
            with client.stream(method, url, follow_redirects=follow_redirects, timeout=timeout) as response:
                yield response

    monkeypatch.setattr(local_manager_module.httpx, "stream", _fake_stream)


# ---------------------------------------------------------------------------
# validate_download_url — pure allow-list + literal-IP checks (no network)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_url",
    [
        "https://evil.example/?u=huggingface.co",
        "https://huggingface.co.evil.example/x",
        "http://huggingface.co/x",  # plain http, not https
        "https://user@huggingface.co/x",  # embedded userinfo
    ],
)
def test_substring_bypass_urls_rejected(bad_url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        validate_download_url(bad_url)


def test_literal_ip_host_rejected() -> None:
    with pytest.raises(UnsafeUrlError):
        validate_download_url("https://127.0.0.1/resolve/main/model.safetensors")


@pytest.mark.parametrize(
    "private_ip",
    [
        "10.0.0.5",
        "127.0.0.1",
        "169.254.169.254",
        "::1",
        "100.64.0.1",  # CGNAT / shared address space (100.64.0.0/10) — F1, opus review 待回答 #47/#48
        "192.0.0.1",  # IETF protocol assignments (192.0.0.0/24) — not_global but not covered by the other terms
    ],
)
def test_resolved_private_address_rejected(monkeypatch: pytest.MonkeyPatch, private_ip: str) -> None:
    _install_dns_mock(monkeypatch, {"huggingface.co": private_ip})
    with pytest.raises(UnsafeUrlError):
        validate_download_url("https://huggingface.co/resolve/main/model.safetensors")


# ---------------------------------------------------------------------------
# download_model — end to end (redirect chain, hop cap, happy path)
# ---------------------------------------------------------------------------


def test_download_model_rejects_substring_bypass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_dns_mock(monkeypatch)
    manager = local_manager_module.LocalLlmManager()
    with pytest.raises(ValueError):
        manager.download_model(_settings(tmp_path), "https://evil.example/?u=huggingface.co")


def test_download_model_redirect_hop2_to_loopback_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """hop1 (huggingface.co -> cdn-lfs.hf.co) is an allowed subdomain and must
    be permitted; hop2's target is a loopback URL and must be rejected BEFORE
    it is ever requested."""
    _install_dns_mock(monkeypatch, {"huggingface.co": _PUBLIC_IP, "cdn-lfs.hf.co": _PUBLIC_IP})
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        if request.url.host == "huggingface.co":
            return httpx.Response(
                302, headers={"Location": "https://cdn-lfs.hf.co/xet/model.bin"}, request=request
            )
        if request.url.host == "cdn-lfs.hf.co":
            # hop2 target is a loopback address — must never actually be requested.
            return httpx.Response(302, headers={"Location": "http://127.0.0.1:8401/steal"}, request=request)
        raise AssertionError(f"unexpected request to {request.url} — hop2 target must not be fetched")

    _install_stream_mock(monkeypatch, handler)
    manager = local_manager_module.LocalLlmManager()
    with pytest.raises(ValueError, match="Redirect target rejected"):
        manager.download_model(_settings(tmp_path), "https://huggingface.co/resolve/main/model.bin")
    # Exactly the first two hops were fetched; the loopback hop2 TARGET itself
    # (127.0.0.1) was validated-and-rejected without ever being requested.
    assert requested_urls == [
        "https://huggingface.co/resolve/main/model.bin",
        "https://cdn-lfs.hf.co/xet/model.bin",
    ]


def test_download_model_six_hop_chain_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_dns_mock(monkeypatch, {"huggingface.co": _PUBLIC_IP})

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        # /resolve/hopN.bin -> redirect to hop(N+1), for N in 0..5 (6 redirects
        # total = one more than MAX_REDIRECT_HOPS=5 permits).
        hop_n = int(path.rsplit("hop", 1)[1].split(".")[0])
        return httpx.Response(
            302,
            headers={"Location": f"https://huggingface.co/resolve/hop{hop_n + 1}.bin"},
            request=request,
        )

    _install_stream_mock(monkeypatch, handler)
    manager = local_manager_module.LocalLlmManager()
    with pytest.raises(ValueError, match="Exceeded the maximum of 5 redirects"):
        manager.download_model(_settings(tmp_path), "https://huggingface.co/resolve/hop0.bin")


def test_download_model_happy_path_writes_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _install_dns_mock(monkeypatch, {"huggingface.co": _PUBLIC_IP})
    body = b"fake-safetensors-bytes"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "huggingface.co"
        return httpx.Response(200, content=body, request=request)

    _install_stream_mock(monkeypatch, handler)
    manager = local_manager_module.LocalLlmManager()
    settings = _settings(tmp_path)
    result = manager.download_model(settings, "https://huggingface.co/resolve/main/model.bin")

    saved_path = Path(result["saved_path"])
    assert saved_path.exists()
    assert saved_path.read_bytes() == body
    assert result["filename"] == "model.bin"

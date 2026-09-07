"""Tests for the Origin/Host guard on state-changing endpoints (待回答 #47).

Covers both the pure unit helpers in ``core.network.origin_guard`` and an
end-to-end pass through the real FastAPI app (``core.main.app``) against a
real write route (``POST /api/v1/projects``), per the owner ruling: "精確比對
host＋解析 IP 擋內網／保留位址＋轉址後每跳重驗" (SSRF half is covered by
``tests/test_local_llm_download_ssrf.py``).

``TestClient`` defaults its base URL to ``http://testserver`` — an unrealistic
Host header no real deployment would ever send — so every fixture in this
suite (and every other test file that POSTs/PUTs/PATCHes/DELETEs through
``main.app``, updated alongside this change) pins
``base_url="http://127.0.0.1:8401"`` to match the server's actual configured
bind address (spec §14.1 / ``core.config.Settings.misaka_api_port``).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.middleware.cors import CORSMiddleware
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import core.main as main
from core.network.origin_guard import (
    STATE_CHANGING_METHODS,
    TAURI_DEFAULT_ORIGINS,
    _host_header_matches,
    resolve_allowed_origins,
)
from core.project.manager import ProjectManager

# ---------------------------------------------------------------------------
# Unit: _host_header_matches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "host_header",
    [
        "127.0.0.1:8401",
        "localhost:8401",
        "[::1]:8401",
    ],
)
def test_host_header_matches_loopback_with_correct_port(host_header: str) -> None:
    assert _host_header_matches(host_header, required_port=8401) is True


@pytest.mark.parametrize(
    "host_header",
    [
        "127.0.0.1:9999",       # wrong port
        "127.0.0.1",            # no port -> compares as scheme default 80, fails vs 8401
        "evil.example:8401",    # not loopback at all
        "evil.example",         # not loopback, no port
        "127.0.0.1.evil.example:8401",  # loopback-looking suffix trick, still not loopback
    ],
)
def test_host_header_rejects_wrong_host_or_port(host_header: str) -> None:
    assert _host_header_matches(host_header, required_port=8401) is False


def test_host_header_no_port_passes_when_server_is_on_80() -> None:
    # The "missing port compares as scheme default 80" rule cuts both ways:
    # a bare Host with no port DOES match when the server itself binds 80.
    assert _host_header_matches("127.0.0.1", required_port=80) is True


# ---------------------------------------------------------------------------
# Unit: resolve_allowed_origins
# ---------------------------------------------------------------------------


def test_resolve_allowed_origins_includes_loopback_and_tauri_defaults() -> None:
    origins = resolve_allowed_origins(main.settings)
    assert "http://127.0.0.1:8400" in origins
    assert "http://localhost:8400" in origins
    assert "http://[::1]:8400" in origins
    assert "http://127.0.0.1:8401" in origins
    for tauri_origin in TAURI_DEFAULT_ORIGINS:
        assert tauri_origin in origins


def test_resolve_allowed_origins_includes_env_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main.settings, "misaka_allowed_origins", "http://example-test.local:9999")
    origins = resolve_allowed_origins(main.settings)
    assert "http://example-test.local:9999" in origins


def test_state_changing_methods_set() -> None:
    assert STATE_CHANGING_METHODS == {"POST", "PUT", "PATCH", "DELETE"}


# ---------------------------------------------------------------------------
# core/config.py: allowed_origins_extra wildcard rejection (F2, opus
# fresh-review 待回答 #47/#48) -- MISAKA_ALLOWED_ORIGINS must never reach
# CORSMiddleware's allow_origins as a literal "*" or a wildcard subdomain.
# ---------------------------------------------------------------------------


def test_allowed_origins_extra_drops_bare_wildcard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main.settings, "misaka_allowed_origins", "*")
    assert main.settings.allowed_origins_extra == []
    assert "*" not in resolve_allowed_origins(main.settings)


def test_allowed_origins_extra_drops_wildcard_subdomain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main.settings, "misaka_allowed_origins", "https://*.example")
    assert main.settings.allowed_origins_extra == []
    assert "https://*.example" not in resolve_allowed_origins(main.settings)


def test_allowed_origins_extra_keeps_concrete_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main.settings, "misaka_allowed_origins", "http://127.0.0.1:9000")
    assert main.settings.allowed_origins_extra == ["http://127.0.0.1:9000"]
    assert "http://127.0.0.1:9000" in resolve_allowed_origins(main.settings)


def test_wildcard_env_origin_never_reaches_cors_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """MISAKA_ALLOWED_ORIGINS=* must never surface as a literal "*" inside the
    origin list handed to CORSMiddleware -- combined with this app's
    allow_credentials=True (core/main.py) a literal "*" there would become a
    credentialed CORS wildcard letting any web page read this local API's GET
    responses. Rebuilds CORSMiddleware in isolation with the SAME allow-list
    resolver main.py uses (resolve_allowed_origins), since core.main.app's own
    CORSMiddleware is constructed once at import time and does not re-read
    settings per request."""
    monkeypatch.setattr(main.settings, "misaka_allowed_origins", "*")
    allowed = resolve_allowed_origins(main.settings)
    assert "*" not in allowed

    async def _ok(request):  # pragma: no cover - trivial handler
        return PlainTextResponse("ok")

    cors_app = Starlette(routes=[Route("/x", _ok, methods=["GET", "OPTIONS"])])
    cors_app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    cors_client = TestClient(cors_app, base_url="http://127.0.0.1:8401")
    resp = cors_client.options(
        "/x",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert resp.headers.get("access-control-allow-origin") is None


# ---------------------------------------------------------------------------
# End-to-end through the real app + a real write route
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    manager = ProjectManager(tmp_path / "projects")
    monkeypatch.setattr(main, "project_manager", manager)
    monkeypatch.setattr(main.generation_service, "project_manager", manager)
    # Real deployment Host — see module docstring.
    return TestClient(main.app, base_url="http://127.0.0.1:8401")


def _create_project_payload() -> dict[str, str]:
    return {"name": "OriginGuardTest", "type": "RPG", "synopsis": "s"}


def test_write_route_no_origin_correct_host_passes(client: TestClient) -> None:
    # No Origin header at all (TestClient does not send one by default) —
    # the intended local-tool path (curl, or a same-machine caller); Host
    # alone (set by base_url above) is the whole guard for this case.
    resp = client.post("/api/v1/projects", json=_create_project_payload())
    assert resp.status_code == 200, resp.text


def test_write_route_allowed_origin_passes(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/projects",
        json=_create_project_payload(),
        headers={"Origin": "http://127.0.0.1:8400"},
    )
    assert resp.status_code == 200, resp.text


def test_write_route_foreign_origin_rejected(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/projects",
        json=_create_project_payload(),
        headers={"Origin": "http://evil.example"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["message"] == "message.fail.403"


def test_write_route_null_origin_rejected(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/projects",
        json=_create_project_payload(),
        headers={"Origin": "null"},
    )
    assert resp.status_code == 403, resp.text


def test_write_route_host_wrong_port_rejected(client: TestClient) -> None:
    resp = client.post(
        "/api/v1/projects",
        json=_create_project_payload(),
        headers={"Host": "127.0.0.1:9999"},
    )
    assert resp.status_code == 403, resp.text


def test_write_route_host_no_port_rejected(client: TestClient) -> None:
    # Server is configured for port 8401 (not 80), so a portless Host must
    # fail — "compare unconditionally, never skip the check when port is
    # absent."
    resp = client.post(
        "/api/v1/projects",
        json=_create_project_payload(),
        headers={"Host": "127.0.0.1"},
    )
    assert resp.status_code == 403, resp.text


def test_write_route_env_added_origin_accepted(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main.settings, "misaka_allowed_origins", "http://extra-tool.local:9999")
    resp = client.post(
        "/api/v1/projects",
        json=_create_project_payload(),
        headers={"Origin": "http://extra-tool.local:9999"},
    )
    assert resp.status_code == 200, resp.text


def test_wildcard_env_origin_write_route_still_403s(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # Independent of the CORS allow-list fix above: OriginGuardMiddleware
    # re-resolves the allow-list on every request (dispatch() calls
    # resolve_allowed_origins fresh), so a foreign Origin on a write route
    # must still be rejected regardless of what MISAKA_ALLOWED_ORIGINS holds.
    monkeypatch.setattr(main.settings, "misaka_allowed_origins", "*")
    resp = client.post(
        "/api/v1/projects",
        json=_create_project_payload(),
        headers={"Origin": "http://evil.example"},
    )
    assert resp.status_code == 403, resp.text


def test_read_route_foreign_origin_still_ok(client: TestClient) -> None:
    # GET (read-only) is exempt from the guard entirely, regardless of Origin.
    resp = client.get("/api/v1/projects", headers={"Origin": "http://evil.example"})
    assert resp.status_code == 200, resp.text


def test_options_preflight_unaffected(client: TestClient) -> None:
    resp = client.options(
        "/api/v1/projects",
        headers={
            "Origin": "http://127.0.0.1:8400",
            "Access-Control-Request-Method": "POST",
        },
    )
    # CORS preflight must still be answered (not 403'd by the guard, which
    # exempts OPTIONS explicitly regardless of middleware ordering).
    assert resp.status_code != 403, resp.text

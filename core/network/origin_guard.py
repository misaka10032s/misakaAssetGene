"""Origin/Host guard for state-changing requests (待回答 #47).

The core API binds to loopback only (spec §14.1) but previously had NO
verification that a state-changing request actually originated from this
machine's own frontend/desktop shell — a malicious web page open in the
user's browser could still POST/PUT/PATCH/DELETE to
``http://127.0.0.1:8401/...`` (classic DNS-rebinding / drive-by-CSRF against
a local service), and the CORS middleware alone does not stop that: CORS is
enforced by the BROWSER reading the response, not by the server refusing the
request, so a same-origin-policy-ignoring client (or a browser that simply
doesn't bother to block a non-credentialed simple request) still reaches the
handler.

This module adds a second, server-side line of defence that does not rely on
the browser cooperating:

* ``Host`` header (if present) must name this machine (loopback) AND the
  exact configured API port — never skipped just because a port is absent
  (an absent port compares against the scheme default, 80, and therefore
  fails unless the server itself is bound to 80).
* ``Origin`` header (if present) must be an exact match against an allow-list
  of this repo's own frontend/desktop-shell origins (never a substring or
  regex-any-port check) — a ``null`` Origin is always rejected.
* Requests with NEITHER header (a local CLI tool, e.g. ``curl`` run from the
  user's own shell) pass on the Host check alone — that is the intended
  local-tool path, not a bypass: nothing about "no Origin" lets a REMOTE
  browser through, because a browser always sends Origin on a
  cross-origin/simple-modifying request.
* GET/HEAD/OPTIONS are exempt (read-only; OPTIONS must keep working for CORS
  preflight, which the CORS middleware already answers before this guard
  would ever see a matching preflight request).
"""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from core.config import Settings

STATE_CHANGING_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Mirrors `core.models.schemas.MessageKey.FAIL_403.value` as a literal string
# rather than importing it — `core.network` and `core.models` already import
# EACH OTHER (core.models.schemas imports core.network.state for
# NetworkMode/NetworkState; that pre-existing cross-package cycle is
# baselined in quality-gates/python/import-cycle-baseline.json), and adding
# a SECOND core.network -> core.models edge here would flip which edge
# import-linter's `acyclic_siblings` nominates to break that cycle — a
# baseline churn with no behavioural benefit. If `MessageKey.FAIL_403`'s
# value ever changes, update this literal to match.
_FAIL_403_MESSAGE_KEY = "message.fail.403"

_LOOPBACK_HOSTNAMES: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1"})

# Tauri v2 default WebView origins. This repo's src-tauri/tauri.conf.json
# (see `build`/`app` blocks) declares no `app.security.csp` or custom
# protocol override, so the framework's built-in default origin applies:
# `tauri://localhost` on macOS/Linux, `http(s)://tauri.localhost` on Windows.
# Only these framework defaults are included — never guessed extras.
TAURI_DEFAULT_ORIGINS: tuple[str, ...] = (
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
)


def resolve_allowed_origins(settings: Settings) -> list[str]:
    """The single allow-list source shared by the Origin guard AND the CORS
    middleware (main.py), so the two can never drift apart.

    Loopback origins for BOTH the frontend port (8400) and the API port
    (8401) — a browser tab actually serving the frontend uses the frontend
    port as its Origin, while a same-machine tool hitting the API directly
    (or a future same-origin console) may use the API port itself; both are
    "this machine's own frontend" for the purposes of this guard.
    """
    ports = sorted({settings.misaka_frontend_port, settings.misaka_api_port})
    origins: list[str] = []
    for hostname in ("127.0.0.1", "localhost", "[::1]"):
        for port in ports:
            origins.append(f"http://{hostname}:{port}")
    origins.extend(TAURI_DEFAULT_ORIGINS)
    origins.extend(settings.allowed_origins_extra)
    # De-dupe while preserving order (env-configured extras may repeat a
    # default).
    seen: set[str] = set()
    deduped: list[str] = []
    for origin in origins:
        if origin not in seen:
            seen.add(origin)
            deduped.append(origin)
    return deduped


def _host_header_matches(host_header: str, required_port: int) -> bool:
    """``host_header`` is the raw ``Host`` value (``"127.0.0.1:8401"``,
    ``"localhost"``, ``"[::1]:8401"``, ...). Parsed via ``urlsplit`` on a
    synthetic ``"//" + host_header`` so IPv6 bracket syntax is handled
    correctly for free. An absent port in the header resolves to ``None``
    from ``urlsplit``, which is normalized to the HTTP scheme default (80)
    here UNCONDITIONALLY — never skipped — so a bare ``Host: 127.0.0.1``
    (no port) fails against a server bound to any port other than 80.
    """
    try:
        split = urlsplit(f"//{host_header}")
    except ValueError:
        return False
    hostname = (split.hostname or "").lower()
    try:
        port = split.port if split.port is not None else 80
    except ValueError:
        return False
    return hostname in _LOOPBACK_HOSTNAMES and port == required_port


def _reject(reason_zh: str) -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={"message": _FAIL_403_MESSAGE_KEY, "detail": reason_zh},
    )


class OriginGuardMiddleware(BaseHTTPMiddleware):
    """Rejects POST/PUT/PATCH/DELETE requests whose Host/Origin do not name
    this machine's own frontend/desktop shell. See module docstring."""

    def __init__(self, app, settings: Settings) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self._settings = settings

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method.upper() not in STATE_CHANGING_METHODS:
            return await call_next(request)

        host_header = request.headers.get("host")
        if host_header is not None and not _host_header_matches(host_header, self._settings.misaka_api_port):
            return _reject("請求的 Host 不是本機服務位址，已拒絕。")

        origin_header = request.headers.get("origin")
        if origin_header is not None:
            allowed = resolve_allowed_origins(self._settings)
            if origin_header == "null" or origin_header not in allowed:
                return _reject("請求的 Origin 不在允許清單內，已拒絕。")
        # else: no Origin header at all — a non-browser local tool (e.g. curl
        # run from the user's own shell) or a same-machine caller that never
        # sets one. A real browser always sends Origin on a cross-origin (or
        # same-origin state-changing) request, so the absence of Origin is
        # not itself a bypass path for a remote/browser attacker — the Host
        # check above is still the full guard for this path, by design.

        return await call_next(request)

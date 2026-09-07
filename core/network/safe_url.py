"""Shared SSRF-safe URL validator (待回答 #48).

Used by :mod:`core.llm.local_manager` (Hugging Face model downloads) and
reusable by anything else in ``core`` that fetches a URL derived from
user/config input rather than a hardcoded loopback constant.

The bug this replaces: a bare ``"huggingface.co" not in url`` substring check
is trivially bypassed (``https://evil.example/?u=huggingface.co``,
``https://huggingface.co.evil.example/``), and ``follow_redirects=True``
lets an otherwise-allowed host bounce the request to a private/loopback
address after the check already passed. This module fixes both: an
exact-label host allow-list (never substring), plus a DNS-resolution check
that rejects any private/loopback/link-local/multicast/reserved/unspecified
resolved address. Callers that need to follow redirects MUST re-run
:func:`validate_download_url` on every ``Location`` header before requesting
it — this module does not follow redirects itself.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import SplitResult, urlsplit

# Apex domains this validator permits, matched by EXACT label (never a bare
# substring — that is exactly the bypass 待回答 #48 reported). Hugging Face's
# main site is huggingface.co; large-file (LFS) blobs commonly redirect to a
# `cdn-lfs*.hf.co` subdomain of the separate `hf.co` apex, so both apexes (and
# their subdomains) must be allowed for the model-download happy path to
# still work through a redirect.
ALLOWED_APEX_DOMAINS: tuple[str, ...] = ("huggingface.co", "hf.co")

# "Up to 5 hops" per 待回答 #48 owner ruling — a caller following redirects
# manually may take at most this many additional requests beyond the first.
MAX_REDIRECT_HOPS: int = 5


class UnsafeUrlError(ValueError):
    """Raised when a URL fails the SSRF-safety validator, naming the reason."""


def _hostname_allowed(hostname: str) -> bool:
    hostname = hostname.lower()
    return any(
        hostname == apex or hostname.endswith(f".{apex}")
        for apex in ALLOWED_APEX_DOMAINS
    )


def _is_unsafe_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # Unwrap an IPv4-mapped IPv6 address (::ffff:127.0.0.1) so the IPv4-side
    # loopback/private checks actually apply to it instead of being
    # evaluated against the (not-obviously-unsafe-looking) IPv6 wrapper.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or not ip.is_global
    )


def _resolve_and_check(hostname: str) -> None:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as error:
        raise UnsafeUrlError(f"DNS resolution failed for host {hostname!r}: {error}") from error
    if not infos:
        raise UnsafeUrlError(f"DNS resolution returned no addresses for host {hostname!r}")
    for info in infos:
        # `info[4]` (the sockaddr tuple) is typed as a union of the IPv4 and
        # IPv6 shapes in typeshed's socket stub; index 0 is the address
        # string in both, but mypy --strict cannot narrow it through the
        # union, so it is coerced explicitly.
        raw_address = str(info[4][0])
        # Strip an IPv6 zone id (e.g. "fe80::1%eth0") before parsing.
        raw_address = raw_address.split("%", 1)[0]
        address = ipaddress.ip_address(raw_address)
        if _is_unsafe_address(address):
            raise UnsafeUrlError(
                f"Host {hostname!r} resolves to a non-public address ({raw_address}); refusing to fetch it."
            )


def validate_download_url(url: str) -> SplitResult:
    """Validate ``url`` is a safe, allow-listed HTTPS download URL.

    Steps (all must pass, in order):
      1. scheme must be exactly ``https``; no embedded userinfo
         (``user:pass@host``).
      2. hostname must be present, must NOT be a literal IP (the allow-list
         is name-based only — a literal IP can never match an apex domain by
         construction, so this is also a belt-and-braces early rejection),
         and must equal (or be a subdomain of, exact-label match) one of
         :data:`ALLOWED_APEX_DOMAINS`.
      3. the hostname is resolved via DNS and EVERY returned address is
         checked against :func:`_is_unsafe_address` — rejecting internal/
         reserved ranges even when an allow-listed name is, for whatever
         reason (poisoned DNS, misconfigured split-horizon, a local hosts
         file entry), currently pointing at one.

    Raises :class:`UnsafeUrlError` (a ``ValueError`` subclass) naming the
    exact reason on failure. Callers that manually follow redirects MUST
    call this again on every hop's ``Location`` target before requesting it.
    """
    parsed = urlsplit(url)
    if parsed.scheme != "https":
        raise UnsafeUrlError(f"Only https URLs are allowed, got scheme={parsed.scheme!r}")
    if parsed.username or parsed.password:
        raise UnsafeUrlError("URLs with embedded userinfo (user:pass@host) are not allowed")

    hostname = parsed.hostname
    if not hostname:
        raise UnsafeUrlError("URL has no hostname")

    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        # A literal IP can never satisfy a name-based allow-list, but reject
        # it explicitly with a clear reason rather than falling through to
        # the generic "not in allow-list" message.
        raise UnsafeUrlError(f"Literal IP hosts are not allowed: {hostname!r}")

    if not _hostname_allowed(hostname):
        raise UnsafeUrlError(f"Host {hostname!r} is not in the allow-list {ALLOWED_APEX_DOMAINS!r}")

    _resolve_and_check(hostname)
    return parsed

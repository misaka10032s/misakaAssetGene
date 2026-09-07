"""Tests for POST /api/v1/projects/{project_id}/assets/{asset_id}/mask (BP-EDITOR-2).

Covers, per the mask-from-regions spec (`docs/blueprint/entries/BP-EDITOR-2.md` /
`@PM/state/runs/misakaAssetGene-refine-loop-260905/C-spec.md` §4.2):
- union / dilate / subtract / feather pixel-level rasterization (`core.editor.mask`).
- polarity: white(255) in the RED channel marks "repaint", black(0) = "keep"
  (ComfyUI `LoadImageMask` channel=red convention — comfyui.py:316-319, BP-EDITOR-1).
- bbox clamping (report, never reject) vs structural bbox-ordering rejection.
- stdlib PNG round-trip: encode (core.editor.mask) -> our own from-scratch decoder here.
- the full API route: create project -> import a source PNG -> build mask -> fetch
  the mask asset's raw bytes via the existing GET .../file route.
- request-size bounds (B-review.md MAJOR finding, 2026-09-05): too-many-regions,
  dilate-out-of-bounds, and an oversize source image (MAX_MASK_PIXELS) all rejected
  with a 4xx, never allowed to allocate/loop over an unbounded buffer.

This repo's venv carries no Pillow, so every PNG here (fixtures AND the decoder used
to verify output) is hand-rolled with only `struct` + `zlib`, deliberately NOT reusing
`core.editor.mask`'s private encoder — this file's decoder is a genuine external check,
not a mirror of the implementation.
"""

from __future__ import annotations

import io
import struct
import zlib
from pathlib import Path

import pytest
from pydantic import ValidationError
from starlette.testclient import TestClient

import core.main as main
from core.editor.mask import ImageHeaderError, MaskRegionError, build_mask_png, read_image_size
from core.models.schemas import MaskFromRegionsRequest, MaskRegion
from core.project.manager import ProjectManager

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


# ---------------------------------------------------------------------------
# Minimal stdlib PNG helpers (test-local, independent of core.editor.mask)
# ---------------------------------------------------------------------------


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)


def _encode_flat_png(width: int, height: int, rgb: tuple[int, int, int] = (128, 64, 200)) -> bytes:
    """A tiny, valid solid-color 8-bit RGB PNG — used only as a source-asset fixture."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = bytes(rgb) * width
    raw = bytearray()
    for _ in range(height):
        raw.append(0)  # filter type 0 (None)
        raw.extend(row)
    idat = zlib.compress(bytes(raw))
    return _PNG_SIGNATURE + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")


def _decode_png_rgb(png_bytes: bytes) -> tuple[int, int, bytes]:
    """Minimal from-scratch PNG decoder: IHDR + concatenated-IDAT inflate +
    filter-type-0 unfiltering. Only handles 8-bit RGB (color type 2), no
    interlace — exactly what `core.editor.mask._write_png_rgb` emits, and
    this assertion on color_type/bit_depth is itself part of the test."""
    assert png_bytes[:8] == _PNG_SIGNATURE, "not a PNG (bad signature)"
    assert png_bytes[12:16] == b"IHDR", "first chunk must be IHDR"
    width, height, bit_depth, color_type = struct.unpack(">IIBB", png_bytes[16:26])
    assert bit_depth == 8, f"expected 8-bit depth, got {bit_depth}"
    assert color_type == 2, f"expected color type 2 (RGB), got {color_type}"

    pos = 8
    n = len(png_bytes)
    idat_parts: list[bytes] = []
    while pos < n:
        length = struct.unpack(">I", png_bytes[pos : pos + 4])[0]
        tag = png_bytes[pos + 4 : pos + 8]
        data = png_bytes[pos + 8 : pos + 8 + length]
        if tag == b"IDAT":
            idat_parts.append(data)
        elif tag == b"IEND":
            break
        pos += 12 + length

    raw = zlib.decompress(b"".join(idat_parts))
    stride = width * 3
    pixels = bytearray(width * height * 3)
    offset = 0
    for y in range(height):
        filter_type = raw[offset]
        assert filter_type == 0, f"unsupported scanline filter type {filter_type}"
        offset += 1
        pixels[y * stride : (y + 1) * stride] = raw[offset : offset + stride]
        offset += stride
    return width, height, bytes(pixels)


# ---------------------------------------------------------------------------
# Unit: read_image_size (stdlib PNG header probe — no Pillow)
# ---------------------------------------------------------------------------


def test_read_image_size_parses_png_ihdr() -> None:
    png = _encode_flat_png(64, 48)
    assert read_image_size(png) == (64, 48)


def test_read_image_size_rejects_unknown_format() -> None:
    with pytest.raises(ImageHeaderError):
        read_image_size(b"not an image at all")


# ---------------------------------------------------------------------------
# Unit: build_mask_png — union / dilate / subtract / feather / clamp / polarity
# ---------------------------------------------------------------------------


def test_union_single_region_polarity_white_inside_black_outside() -> None:
    """Region interior must be pure white (255) in every channel; the RED
    channel specifically is what ComfyUI's LoadImageMask(channel="red") reads."""
    request = MaskFromRegionsRequest(regions=[MaskRegion(bbox=[10, 10, 20, 20])])
    result = build_mask_png(64, 64, request)
    width, height, pixels = _decode_png_rgb(result.png_bytes)
    assert (width, height) == (64, 64)
    assert result.clamped is False

    def px(x: int, y: int) -> tuple[int, int, int]:
        i = (y * width + x) * 3
        return pixels[i], pixels[i + 1], pixels[i + 2]

    assert px(15, 15) == (255, 255, 255)  # inside the region
    assert px(0, 0) == (0, 0, 0)  # far outside
    assert px(20, 20) == (0, 0, 0)  # bbox is half-open: x1/y1=20 excluded
    assert px(9, 9) == (0, 0, 0)  # just below x0/y0=10


def test_union_of_two_regions_covers_both() -> None:
    request = MaskFromRegionsRequest(
        regions=[
            MaskRegion(bbox=[0, 0, 5, 5]),
            MaskRegion(bbox=[40, 40, 45, 45]),
        ]
    )
    result = build_mask_png(64, 64, request)
    width, _, pixels = _decode_png_rgb(result.png_bytes)

    def px(x: int, y: int) -> int:
        return pixels[(y * width + x) * 3]

    assert px(2, 2) == 255
    assert px(42, 42) == 255
    assert px(20, 20) == 0


def test_dilate_expands_region_boundary() -> None:
    plain = build_mask_png(64, 64, MaskFromRegionsRequest(regions=[MaskRegion(bbox=[20, 20, 30, 30])]))
    dilated = build_mask_png(
        64, 64, MaskFromRegionsRequest(regions=[MaskRegion(bbox=[20, 20, 30, 30], dilate=5)])
    )
    plain_w, _, plain_px = _decode_png_rgb(plain.png_bytes)
    dilated_w, _, dilated_px = _decode_png_rgb(dilated.png_bytes)

    def px(pixels: bytes, width: int, x: int, y: int) -> int:
        return pixels[(y * width + x) * 3]

    # (17,25) sits 3px outside the plain bbox on the low-x edge.
    assert px(plain_px, plain_w, 17, 25) == 0
    assert px(dilated_px, dilated_w, 17, 25) == 255
    assert dilated.coverage_ratio > plain.coverage_ratio


def test_subtract_carves_out_overlap() -> None:
    request = MaskFromRegionsRequest(
        regions=[MaskRegion(bbox=[10, 10, 40, 40])],
        subtract=[MaskRegion(bbox=[20, 20, 30, 30])],
    )
    result = build_mask_png(64, 64, request)
    width, _, pixels = _decode_png_rgb(result.png_bytes)

    def px(x: int, y: int) -> int:
        return pixels[(y * width + x) * 3]

    assert px(12, 12) == 255  # inside region, outside subtract
    assert px(25, 25) == 0  # inside subtract -> carved to black
    assert px(50, 50) == 0  # outside everything


def test_feather_ramps_linearly_from_edge_inward() -> None:
    request = MaskFromRegionsRequest(regions=[MaskRegion(bbox=[20, 20, 40, 40], feather=4)])
    result = build_mask_png(64, 64, request)
    width, _, pixels = _decode_png_rgb(result.png_bytes)

    def px(x: int, y: int) -> int:
        return pixels[(y * width + x) * 3]

    # Walking inward from the top edge (y=20) at a fixed x, away from corners
    # (x=25 keeps the x-distance-to-edge large enough to never be the minimum).
    assert px(25, 20) == round(1 / 4 * 255)  # dist_to_edge=0
    assert px(25, 21) == round(2 / 4 * 255)  # dist_to_edge=1
    assert px(25, 22) == round(3 / 4 * 255)  # dist_to_edge=2
    assert px(25, 23) == 255  # dist_to_edge=3 -> ramp reaches full white
    assert px(25, 24) == 255  # dist_to_edge=4 -> fully inside, plateau


def test_bbox_ordering_violation_raises() -> None:
    request = MaskFromRegionsRequest(regions=[MaskRegion(bbox=[20, 20, 10, 10])])
    with pytest.raises(MaskRegionError):
        build_mask_png(64, 64, request)


def test_empty_regions_rejected_at_schema_level() -> None:
    with pytest.raises(ValidationError):  # min_length=1
        MaskFromRegionsRequest(regions=[])


def test_out_of_bounds_bbox_is_clamped_not_rejected() -> None:
    request = MaskFromRegionsRequest(regions=[MaskRegion(bbox=[-10, -10, 20, 20])])
    result = build_mask_png(64, 64, request)
    assert result.clamped is True
    width, _, pixels = _decode_png_rgb(result.png_bytes)
    assert pixels[(0 * width + 0) * 3] == 255  # (0,0) still painted, not dropped


# ---------------------------------------------------------------------------
# Route: full API path
# ---------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    manager = ProjectManager(tmp_path / "projects")
    monkeypatch.setattr(main, "project_manager", manager)
    monkeypatch.setattr(main.generation_service, "project_manager", manager)
    return TestClient(main.app, base_url="http://127.0.0.1:8401")


def _create_project(client: TestClient) -> str:
    resp = client.post("/api/v1/projects", json={"name": "MaskProj", "type": "RPG", "synopsis": "s"})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["project"]["id"]


def _import_source_png(client: TestClient, project_id: str, width: int = 64, height: int = 64) -> str:
    content = _encode_flat_png(width, height)
    resp = client.post(
        f"/api/v1/projects/{project_id}/assets/import",
        files={"file": ("source.png", io.BytesIO(content), "image/png")},
        data={"modality": "image", "asset_type": "image", "title": "Source"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["assets"][-1]["id"]


def test_mask_route_creates_asset_and_serves_correct_bytes(client: TestClient) -> None:
    project_id = _create_project(client)
    asset_id = _import_source_png(client, project_id, 64, 64)

    resp = client.post(
        f"/api/v1/projects/{project_id}/assets/{asset_id}/mask",
        json={
            "regions": [
                {"bbox": [10, 10, 30, 30], "dilate": 2, "feather": 0},
                {"bbox": [40, 40, 60, 60]},
            ],
            "subtract": [{"bbox": [15, 15, 20, 20]}],
            "name": "test-mask",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["width"] == 64
    assert data["height"] == 64
    assert 0.0 < data["coverage_ratio"] < 1.0
    assert data["clamped"] is False
    mask_asset_id = data["mask_asset_id"]
    assert mask_asset_id and mask_asset_id != asset_id

    file_resp = client.get(f"/api/v1/projects/{project_id}/assets/{mask_asset_id}/file")
    assert file_resp.status_code == 200, file_resp.text
    width, height, pixels = _decode_png_rgb(file_resp.content)
    assert (width, height) == (64, 64)

    def px(x: int, y: int) -> int:
        return pixels[(y * width + x) * 3]

    assert px(12, 12) == 255  # inside region A (dilated to [8,32)), outside subtract
    assert px(17, 17) == 0  # inside region A AND inside the subtract hole
    assert px(50, 50) == 255  # inside region B
    assert px(35, 35) == 0  # outside everything


def test_mask_route_rejects_non_image_source_asset(client: TestClient) -> None:
    project_id = _create_project(client)
    resp = client.post(
        f"/api/v1/projects/{project_id}/assets/import",
        files={"file": ("clip.wav", io.BytesIO(b"RIFF...."), "audio/wav")},
        data={"modality": "voice", "asset_type": "voice", "title": "Clip"},
    )
    assert resp.status_code == 200, resp.text
    voice_asset_id = resp.json()["data"]["assets"][-1]["id"]

    resp = client.post(
        f"/api/v1/projects/{project_id}/assets/{voice_asset_id}/mask",
        json={"regions": [{"bbox": [0, 0, 10, 10]}]},
    )
    assert resp.status_code == 400, resp.text


def test_mask_route_unknown_asset_returns_404(client: TestClient) -> None:
    project_id = _create_project(client)
    resp = client.post(
        f"/api/v1/projects/{project_id}/assets/nonexistent-asset/mask",
        json={"regions": [{"bbox": [0, 0, 10, 10]}]},
    )
    assert resp.status_code == 404


def test_mask_route_malformed_source_bytes_returns_400(client: TestClient) -> None:
    """Source asset record exists but its bytes are not a real PNG/JPEG (e.g. a
    corrupted upload) -> read_image_size() raises ImageHeaderError -> 400."""
    project_id = _create_project(client)
    resp = client.post(
        f"/api/v1/projects/{project_id}/assets/import",
        files={"file": ("broken.png", io.BytesIO(b"not really a png"), "image/png")},
        data={"modality": "image", "asset_type": "image", "title": "Broken"},
    )
    assert resp.status_code == 200, resp.text
    broken_asset_id = resp.json()["data"]["assets"][-1]["id"]

    resp = client.post(
        f"/api/v1/projects/{project_id}/assets/{broken_asset_id}/mask",
        json={"regions": [{"bbox": [0, 0, 10, 10]}]},
    )
    assert resp.status_code == 400, resp.text


# ---------------------------------------------------------------------------
# Request-size bounds (B-review.md MAJOR finding, 2026-09-05):
# schema-level max_length on regions/subtract, ge/le on dilate/feather, and
# core.editor.mask.MAX_MASK_PIXELS all guard against one request pinning the
# core API's CPU/memory via an unbounded region list or source image.
# ---------------------------------------------------------------------------


def test_mask_route_too_many_regions_rejected(client: TestClient) -> None:
    """Pydantic ``max_length`` violation on ``regions`` -> ``RequestValidationError``.
    This repo's own global handler (``core/main.py``'s
    ``validation_exception_handler``) maps EVERY ``RequestValidationError`` to
    HTTP 400 (not FastAPI's un-customized default of 422) — asserting 400 here
    matches that deliberate, repo-wide convention, not FastAPI's raw default."""
    project_id = _create_project(client)
    asset_id = _import_source_png(client, project_id, 64, 64)
    resp = client.post(
        f"/api/v1/projects/{project_id}/assets/{asset_id}/mask",
        json={"regions": [{"bbox": [0, 0, 2, 2]} for _ in range(33)]},  # cap is 32
    )
    assert resp.status_code == 400, resp.text


def test_mask_route_dilate_out_of_bounds_rejected(client: TestClient) -> None:
    """Same validation-error-to-400 mapping as above, for ``dilate`` exceeding
    its ``le=256`` bound."""
    project_id = _create_project(client)
    asset_id = _import_source_png(client, project_id, 64, 64)
    resp = client.post(
        f"/api/v1/projects/{project_id}/assets/{asset_id}/mask",
        json={"regions": [{"bbox": [0, 0, 10, 10], "dilate": 257}]},  # cap is 256
    )
    assert resp.status_code == 400, resp.text


def test_mask_route_oversize_source_returns_4xx(client: TestClient) -> None:
    """A source asset whose IHDR claims 5000x5000 (header only, no real pixel
    payload -- constructing it costs O(1) bytes, never a real allocation) must
    be rejected by MAX_MASK_PIXELS before build_mask_png allocates any
    width*height buffer."""
    project_id = _create_project(client)
    fake_ihdr = struct.pack(">IIBBBBB", 5000, 5000, 8, 2, 0, 0, 0)
    fake_png = _PNG_SIGNATURE + _png_chunk(b"IHDR", fake_ihdr)
    resp = client.post(
        f"/api/v1/projects/{project_id}/assets/import",
        files={"file": ("huge.png", io.BytesIO(fake_png), "image/png")},
        data={"modality": "image", "asset_type": "image", "title": "Huge"},
    )
    assert resp.status_code == 200, resp.text
    huge_asset_id = resp.json()["data"]["assets"][-1]["id"]

    resp = client.post(
        f"/api/v1/projects/{project_id}/assets/{huge_asset_id}/mask",
        json={"regions": [{"bbox": [0, 0, 10, 10]}]},
    )
    assert resp.status_code == 400, resp.text

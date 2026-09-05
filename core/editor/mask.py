"""core/editor/mask.py — BP-EDITOR-2: bbox-region mask generation.

Produces a white-on-black PNG mask from a list of pixel-space bbox regions
(``MaskFromRegionsRequest`` in ``core.models.schemas``), suitable for
ComfyUI's ``LoadImageMask`` node with ``channel="red"``: white (255) marks
the region to repaint, black (0) keeps the pixel unchanged — the same
polarity convention the hand-painted editor already uses
(``core/generation/adapters/comfyui.py:316-319``, ``BP-EDITOR-1``).

Coordinates are SOURCE-IMAGE pixel space, half-open like a Python slice
(``bbox = [x0, y0, x1, y1]``, region covers ``x0 <= x < x1`` /
``y0 <= y < y1``). Each region may be:

- ``dilate``  — expanded outward by N pixels on every side before rasterizing.
- ``feather`` — the outer N pixels of the (dilated) rectangle ramp linearly
  from near-0 at the boundary to 1.0 (full white) N pixels inward, instead of
  a hard edge.

The final mask is ``union(regions)`` with ``union(subtract)`` carved out:
``final[p] = union_regions[p] * (1 - union_subtract[p])``.

This repo's Python venv carries no Pillow (confirmed via
``uv sync --extra dev``, see ``@PM/state/runs/misakaAssetGene-refine-loop-260905/B-impl.md``),
so both the source-image dimension probe and the mask PNG encoder are
hand-rolled here with only ``struct`` + ``zlib`` (stdlib).
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.models.schemas import MaskFromRegionsRequest, MaskRegion

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# Hard cap on source-image pixel count a single mask-build request may
# rasterize. Bounds the OTHER half of the request-size risk that
# ``MaskFromRegionsRequest.regions``/``subtract``'s ``max_length=32`` bounds
# on the region-count side: with no cap here, an arbitrarily large source
# image (``assets/import`` itself has no upload size limit) would let one
# request allocate/loop over an unbounded ``width*height`` buffer and pin
# the core API's CPU/memory. 4096x4096 comfortably covers every generation
# resolution this repo currently produces (BP-EDITOR-1/BP-GEN-*) while still
# rejecting a pathological source. Checked in ``build_mask_png`` BEFORE any
# ``width*height``-sized buffer is allocated.
MAX_MASK_PIXELS = 4096 * 4096

# JPEG SOF (start-of-frame) markers that carry frame width/height. Excludes
# 0xC4 (DHT), 0xC8 (JPG, reserved), 0xCC (DAC) which are not SOF markers.
_JPEG_SOF_MARKERS = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)


class ImageHeaderError(ValueError):
    """Raised when a source asset's bytes are not a recognizable PNG/JPEG header."""


class MaskRegionError(ValueError):
    """Raised for a structurally-invalid region list (empty, bad bbox ordering)."""


@dataclass(frozen=True)
class MaskBuildResult:
    png_bytes: bytes
    width: int
    height: int
    coverage_ratio: float
    clamped: bool


# ---------------------------------------------------------------------------
# Source-image size probe (stdlib PNG/JPEG header parse — no Pillow)
# ---------------------------------------------------------------------------


def read_image_size(content: bytes) -> tuple[int, int]:
    """Return ``(width, height)`` parsed from a PNG or JPEG header.

    Raises ``ImageHeaderError`` if ``content`` is not a recognizable PNG or
    JPEG (this repo's mask/source assets are always one of the two).
    """
    if content[:8] == _PNG_SIGNATURE:
        return _read_png_size(content)
    if content[:2] == b"\xff\xd8":
        return _read_jpeg_size(content)
    raise ImageHeaderError("Unrecognized image format (expected PNG or JPEG header)")


def _read_png_size(content: bytes) -> tuple[int, int]:
    # PNG layout: 8-byte signature, then chunks of [4-byte length][4-byte
    # type][data][4-byte CRC]. The very first chunk is always IHDR
    # (length 13): width @ offset 16, height @ offset 20, both big-endian u32.
    if len(content) < 24 or content[12:16] != b"IHDR":
        raise ImageHeaderError("Malformed PNG: missing IHDR chunk")
    width, height = struct.unpack(">II", content[16:24])
    return width, height


def _read_jpeg_size(content: bytes) -> tuple[int, int]:
    i = 2  # skip SOI marker (0xFFD8)
    n = len(content)
    while i + 1 < n:
        if content[i] != 0xFF:
            i += 1
            continue
        marker = content[i + 1]
        if marker in _JPEG_SOF_MARKERS:
            if i + 9 > n:
                break
            height, width = struct.unpack(">HH", content[i + 5 : i + 9])
            return width, height
        # Markers with no payload segment to skip.
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if i + 4 > n:
            break
        seg_len = struct.unpack(">H", content[i + 2 : i + 4])[0]
        i += 2 + seg_len
    raise ImageHeaderError("Malformed JPEG: no SOF (frame size) marker found")


# ---------------------------------------------------------------------------
# Mask rasterization
# ---------------------------------------------------------------------------


def build_mask_png(width: int, height: int, request: MaskFromRegionsRequest) -> MaskBuildResult:
    """Rasterize ``request`` into a white-on-black RGB mask PNG.

    ``width``/``height`` are the SOURCE image's pixel dimensions — the
    output mask is always the exact same size (LoadImageMask requires this).
    """
    if width <= 0 or height <= 0:
        raise MaskRegionError("Source image dimensions must be positive")
    if not request.regions:
        raise MaskRegionError("At least one region is required")

    total_pixels = width * height
    if total_pixels > MAX_MASK_PIXELS:
        raise MaskRegionError(
            f"Source image {width}x{height} ({total_pixels} px) exceeds the "
            f"{MAX_MASK_PIXELS}px mask-build cap"
        )

    clamped = False
    union = [0.0] * total_pixels
    for region in request.regions:
        clamped = _accumulate_region(union, width, height, region) or clamped

    if request.subtract:
        subtract_union = [0.0] * total_pixels
        for region in request.subtract:
            clamped = _accumulate_region(subtract_union, width, height, region) or clamped
        for i in range(total_pixels):
            union[i] = union[i] * (1.0 - subtract_union[i])

    total = 0.0
    pixels = bytearray(width * height * 3)
    for i, value in enumerate(union):
        total += value
        level = max(0, min(255, round(value * 255)))
        base = i * 3
        pixels[base] = level
        pixels[base + 1] = level
        pixels[base + 2] = level

    coverage_ratio = total / (width * height)
    png_bytes = _write_png_rgb(width, height, bytes(pixels))
    return MaskBuildResult(
        png_bytes=png_bytes,
        width=width,
        height=height,
        coverage_ratio=coverage_ratio,
        clamped=clamped,
    )


def _accumulate_region(target: list[float], width: int, height: int, region: MaskRegion) -> bool:
    """Rasterize one region directly into the shared ``target`` grid (row-major,
    ``width*height``, max-merged in place), instead of allocating a separate
    ``width*height`` grid per region and merging afterward — the per-region
    cost this way is bounded by the region's own (dilated) bbox area, not the
    whole image, and only ONE ``width*height`` buffer exists per union
    (``build_mask_png``'s ``union``/``subtract_union``), never one per region.

    Returns whether clamping to the image bounds was needed anywhere along
    the way. Pixel results are identical to the previous per-region-grid
    implementation (union/max semantics are associative and commutative).
    """
    if len(region.bbox) != 4:
        raise MaskRegionError(f"bbox must have exactly 4 values, got {len(region.bbox)}")
    x0, y0, x1, y1 = region.bbox
    if x1 <= x0 or y1 <= y0:
        raise MaskRegionError(f"bbox must satisfy x1 > x0 and y1 > y0, got {region.bbox!r}")

    clamped = False
    cx0, cy0, cx1, cy1 = x0, y0, x1, y1
    if cx0 < 0 or cy0 < 0 or cx1 > width or cy1 > height:
        clamped = True
    cx0 = max(0, min(cx0, width))
    cy0 = max(0, min(cy0, height))
    cx1 = max(cx0, min(cx1, width))
    cy1 = max(cy0, min(cy1, height))

    dilate = max(0, region.dilate)
    feather = max(0, region.feather)

    # Edges used for both the rasterized rectangle and the feather-distance
    # calculation are the DILATED, CLAMPED-bbox extents (clamped bbox first,
    # then dilated, then clamped again to the image) — a region touching the
    # image edge does not fade out just because its geometric bbox extended
    # past the frame.
    ex0, ey0, ex1, ey1 = cx0 - dilate, cy0 - dilate, cx1 + dilate, cy1 + dilate
    if ex0 < 0 or ey0 < 0 or ex1 > width or ey1 > height:
        clamped = True
    fex0 = max(0, ex0)
    fey0 = max(0, ey0)
    fex1 = max(fex0, min(ex1, width))
    fey1 = max(fey0, min(ey1, height))

    for y in range(fey0, fey1):
        row_base = y * width
        for x in range(fex0, fex1):
            dist_to_edge = min(x - ex0, ex1 - 1 - x, y - ey0, ey1 - 1 - y)
            if feather <= 0 or dist_to_edge >= feather:
                value = 1.0
            else:
                # Linear ramp: the boundary pixel (dist=0) gets 1/feather,
                # the pixel `feather` px inward (dist=feather-1) gets 1.0.
                value = (dist_to_edge + 1) / feather
            idx = row_base + x
            if value > target[idx]:
                target[idx] = value
    return clamped


# ---------------------------------------------------------------------------
# Minimal stdlib PNG encoder (8-bit RGB, no filtering, single IDAT)
# ---------------------------------------------------------------------------


def _write_png_rgb(width: int, height: int, rgb_pixels: bytes) -> bytes:
    """Encode raw top-to-bottom row-major 8-bit RGB pixel bytes (``width*height*3``
    bytes, no filter/stride bytes yet) into a minimal, standards-valid PNG.
    """

    def _chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    # bit depth 8, color type 2 = truecolor (RGB), no interlace.
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)

    stride = width * 3
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type 0 (None) for every scanline
        raw.extend(rgb_pixels[y * stride : (y + 1) * stride])
    idat = zlib.compress(bytes(raw), level=6)

    return _PNG_SIGNATURE + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", idat) + _chunk(b"IEND", b"")

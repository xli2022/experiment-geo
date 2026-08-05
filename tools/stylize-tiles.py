#!/usr/bin/env python3
"""
Restyle the textures inside 3D Tiles .b3dm files — photoreal to SimCity-ish.

Why this exists
---------------
Aerial photogrammetry is accurate but visually noisy: desaturated, grainy, full
of high-frequency detail that reads as mush from altitude and as grime up close.
A city-builder look is the opposite — bright, flat, saturated, with large legible
areas of colour. Getting there is an image problem, not a geometry one, so this
rewrites the textures in place and leaves the meshes alone.

"Less detail" here means less *variation*, not less *sharpness*. A city-builder
render is crisper than a photograph, not softer: large flat areas of colour
separated by hard edges. Downscaling and blurring produce the opposite — mush —
so the pipeline deliberately keeps full resolution and sharpens at the end.

  1. bilateral filter  the load-bearing step. Averages colour only across
                       pixels that are already similar, so it collapses roof
                       texture and road speckle into flat areas while leaving
                       the boundary between roof and road perfectly hard. A
                       median or Gaussian blur softens both equally.
  2. shadow lift       aerial shadows carry a heavy blue cast; saturating them
                       directly turns every shaded wall electric blue
  3. saturate/contrast photogrammetry is captured flat; games are not
  4. palette quantize  collapses the remaining near-identical shades into
                       genuinely flat regions
  5. unsharp mask      restores the edge crispness that quantising softens,
                       and pushes past the original for a poster-like look

Quantising after filtering is deliberate: do it first and the filter smears the
palette straight back into gradients.

Usage:
    tools/stylize-tiles.py <tiles-dir> [options]
    tools/stylize-tiles.py <tiles-dir> --preview out.png   # sample, don't write

Tune the look on a preview strip before committing to a full run — a few
hundred tiles takes minutes, a preview takes seconds.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import struct
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass

try:
    import cv2
    import numpy as np
    from PIL import Image, ImageEnhance, ImageFilter
except ImportError as exc:
    sys.exit(f"error: needs Pillow and opencv — pip install Pillow opencv-python-headless ({exc})")

Image.MAX_IMAGE_PIXELS = None

GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942


@dataclass(frozen=True)
class Style:
    """
    Defaults are what preview iteration landed on, not a first guess.

    Note scale defaults to 1.0. Downscaling is tempting for the size win but it
    is exactly the wrong lever here — it costs sharpness, which is the thing
    that makes the style read as illustrated rather than blurred.
    """

    scale: float = 1.0
    bilateral_d: int = 9          # neighbourhood diameter
    bilateral_color: int = 60     # how different a colour can be and still blend
    bilateral_space: int = 60     # how far away a pixel can be and still blend
    bilateral_passes: int = 2     # repeat rather than widen — flatter, still sharp
    saturation: float = 1.5
    contrast: float = 1.18
    brightness: float = 1.04
    colors: int = 24
    shadow_lift: float = 0.16
    sharpen: float = 1.4          # unsharp amount, 0 = off
    quality: int = 78


def stylize_image(data: bytes, style: Style) -> bytes:
    """Apply the look to one encoded image, returning re-encoded JPEG bytes."""
    img = Image.open(io.BytesIO(data)).convert("RGB")

    if style.scale != 1.0:
        w = max(8, int(img.width * style.scale))
        h = max(8, int(img.height * style.scale))
        img = img.resize((w, h), Image.LANCZOS)

    if style.bilateral_passes > 0 and style.bilateral_d > 0:
        # The step that makes this work. Bilateral weights each neighbour by
        # colour distance as well as spatial distance, so a roof averages with
        # roof and a road with road, but neither bleeds across the boundary
        # between them. Repeated narrow passes flatten harder than one wide
        # pass and cost less.
        arr = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
        for _ in range(style.bilateral_passes):
            arr = cv2.bilateralFilter(
                arr, style.bilateral_d, style.bilateral_color, style.bilateral_space
            )
        img = Image.fromarray(cv2.cvtColor(arr, cv2.COLOR_BGR2RGB))

    if style.shadow_lift > 0:
        # Aerial shadows carry a strong blue cast. Saturating them directly
        # turns every shaded wall electric blue, which reads as broken rather
        # than stylised — so lift the toe of the curve first and let the
        # saturation act on midtones instead.
        lift = style.shadow_lift
        lut = [round(255 * ((i / 255) * (1 - lift) + lift)) for i in range(256)]
        img = img.point(lut * 3)

    if style.saturation != 1.0:
        img = ImageEnhance.Color(img).enhance(style.saturation)
    if style.contrast != 1.0:
        img = ImageEnhance.Contrast(img).enhance(style.contrast)
    if style.brightness != 1.0:
        img = ImageEnhance.Brightness(img).enhance(style.brightness)

    if style.colors > 0:
        # Per-tile adaptive palette. A single shared palette across tiles would
        # be more cohesive, but the tiles are independent downloads and banding
        # at their seams looks worse than slight per-tile drift.
        img = img.quantize(colors=style.colors, method=Image.MEDIANCUT, dither=0)
        img = img.convert("RGB")

    if style.sharpen > 0:
        # Quantising softens edges slightly; this restores them and then some.
        # A stylised city should read crisper than the photograph it came from.
        img = img.filter(
            ImageFilter.UnsharpMask(radius=2, percent=int(style.sharpen * 100), threshold=3)
        )

    out = io.BytesIO()
    img.save(out, "JPEG", quality=style.quality, optimize=True, subsampling=1)
    return out.getvalue()


def parse_b3dm(data: bytes) -> tuple[bytes, bytes]:
    """Split a .b3dm into (header+tables, glb payload)."""
    if data[:4] != b"b3dm":
        raise ValueError(f"not a b3dm (magic {data[:4]!r})")
    _, _, _, ft_json, ft_bin, bt_json, bt_bin = struct.unpack("<4sIIIIII", data[:28])
    offset = 28 + ft_json + ft_bin + bt_json + bt_bin
    return data[:offset], data[offset:]


def rebuild_b3dm(prefix: bytes, glb: bytes) -> bytes:
    """Reassemble, fixing the total length in the header."""
    total = len(prefix) + len(glb)
    return struct.pack("<4sII", b"b3dm", 1, total) + prefix[12:] + glb


def restyle_glb(glb: bytes, style: Style) -> tuple[bytes, int, int]:
    """Rewrite every image in a glb. Returns (glb, images_changed, bytes_saved)."""
    magic, _, total = struct.unpack("<III", glb[:12])
    if magic != GLB_MAGIC:
        raise ValueError("not a glb")

    chunks = []
    i = 12
    while i < total:
        length, ctype = struct.unpack("<II", glb[i : i + 8])
        i += 8
        chunks.append([ctype, glb[i : i + length]])
        i += length

    gltf = json.loads(next(c[1] for c in chunks if c[0] == CHUNK_JSON))
    binary = next((c[1] for c in chunks if c[0] == CHUNK_BIN), b"")
    views = gltf.get("bufferViews", [])
    if not views or not gltf.get("images"):
        return glb, 0, 0

    # Restyle images, keyed by the bufferView they live in.
    replacements: dict[int, bytes] = {}
    saved = 0
    for image in gltf["images"]:
        bv = image.get("bufferView")
        if bv is None:
            continue
        view = views[bv]
        start = view.get("byteOffset", 0)
        original = binary[start : start + view["byteLength"]]
        try:
            new = stylize_image(original, style)
        except Exception as exc:  # noqa: BLE001 — a bad image shouldn't kill the tile
            print(f"    skipped an image: {exc}", file=sys.stderr)
            continue
        replacements[bv] = new
        saved += len(original) - len(new)
        image["mimeType"] = "image/jpeg"

    if not replacements:
        return glb, 0, 0

    # Every bufferView after a resized image shifts, so rebuild the whole BIN
    # and reassign offsets in order rather than trying to patch in place.
    out = bytearray()
    for index, view in enumerate(views):
        payload = replacements.get(index)
        if payload is None:
            start = view.get("byteOffset", 0)
            payload = binary[start : start + view["byteLength"]]
        while len(out) % 4:  # glTF requires 4-byte alignment
            out.append(0)
        view["byteOffset"] = len(out)
        view["byteLength"] = len(payload)
        out += payload

    while len(out) % 4:
        out.append(0)
    if gltf.get("buffers"):
        gltf["buffers"][0]["byteLength"] = len(out)

    json_bytes = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * ((4 - len(json_bytes) % 4) % 4)  # pad with spaces, not nulls

    body = (
        struct.pack("<II", len(json_bytes), CHUNK_JSON)
        + json_bytes
        + struct.pack("<II", len(out), CHUNK_BIN)
        + bytes(out)
    )
    return struct.pack("<III", GLB_MAGIC, 2, 12 + len(body)) + body, len(replacements), saved


def process_file(args: tuple[str, Style]) -> tuple[str, int, int, int]:
    path, style = args
    before = os.path.getsize(path)
    try:
        data = open(path, "rb").read()
        prefix, glb = parse_b3dm(data)
        new_glb, changed, _ = restyle_glb(glb, style)
        if not changed:
            return path, 0, before, before
        out = rebuild_b3dm(prefix, new_glb)
        with open(path, "wb") as fh:
            fh.write(out)
        return path, changed, before, len(out)
    except Exception as exc:  # noqa: BLE001
        print(f"  failed {path}: {exc}", file=sys.stderr)
        return path, 0, before, before


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("Usage")[0].strip())
    ap.add_argument("tiles_dir")
    ap.add_argument("--preview", metavar="PNG", help="write a before/after strip and exit")
    ap.add_argument("--scale", type=float, default=Style.scale)
    ap.add_argument("--bilateral-d", type=int, default=Style.bilateral_d)
    ap.add_argument("--bilateral-color", type=int, default=Style.bilateral_color)
    ap.add_argument("--bilateral-space", type=int, default=Style.bilateral_space)
    ap.add_argument("--bilateral-passes", type=int, default=Style.bilateral_passes, help="0 = off")
    ap.add_argument("--sharpen", type=float, default=Style.sharpen, help="unsharp amount, 0 = off")
    ap.add_argument("--saturation", type=float, default=Style.saturation)
    ap.add_argument("--contrast", type=float, default=Style.contrast)
    ap.add_argument("--brightness", type=float, default=Style.brightness)
    ap.add_argument("--colors", type=int, default=Style.colors, help="palette size, 0 = off")
    ap.add_argument(
        "--shadow-lift",
        type=float,
        default=Style.shadow_lift,
        help="raise the black point before saturating, 0 = off",
    )
    ap.add_argument("--quality", type=int, default=Style.quality)
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    args = ap.parse_args()

    style = Style(
        scale=args.scale,
        bilateral_d=args.bilateral_d,
        bilateral_color=args.bilateral_color,
        bilateral_space=args.bilateral_space,
        bilateral_passes=args.bilateral_passes,
        sharpen=args.sharpen,
        saturation=args.saturation,
        contrast=args.contrast,
        brightness=args.brightness,
        colors=args.colors,
        shadow_lift=args.shadow_lift,
        quality=args.quality,
    )

    files = sorted(
        os.path.join(r, f)
        for r, _, fs in os.walk(args.tiles_dir)
        for f in fs
        if f.endswith(".b3dm")
    )
    if not files:
        print(f"error: no .b3dm files under {args.tiles_dir}", file=sys.stderr)
        return 1

    if args.preview:
        return write_preview(files, style, args.preview)

    print(f"Restyling {len(files)} tiles with {args.jobs} workers")
    print(f"  {style}")
    total_before = total_after = changed_tiles = 0
    with ProcessPoolExecutor(max_workers=args.jobs) as pool:
        for i, (_, changed, before, after) in enumerate(
            pool.map(process_file, ((f, style) for f in files), chunksize=4), 1
        ):
            total_before += before
            total_after += after
            changed_tiles += 1 if changed else 0
            if i % 25 == 0 or i == len(files):
                print(f"\r  {i}/{len(files)} tiles", end="", flush=True)

    mb = 1024 * 1024
    print()
    print(f"Restyled {changed_tiles}/{len(files)} tiles")
    print(f"  {total_before / mb:.0f} MB -> {total_after / mb:.0f} MB")
    if total_after:
        print(f"  {total_before / total_after:.2f}x smaller")
    return 0


def write_preview(files: list[str], style: Style, out_path: str) -> int:
    """Find the biggest texture available and show it before and after."""
    best: tuple[int, bytes] | None = None
    for path in files[: min(len(files), 40)]:
        try:
            _, glb = parse_b3dm(open(path, "rb").read())
            _, _, total = struct.unpack("<III", glb[:12])
            i, chunks = 12, []
            while i < total:
                length, ctype = struct.unpack("<II", glb[i : i + 8])
                i += 8
                chunks.append((ctype, glb[i : i + length]))
                i += length
            gltf = json.loads(next(c[1] for c in chunks if c[0] == CHUNK_JSON))
            binary = next((c[1] for c in chunks if c[0] == CHUNK_BIN), b"")
            for image in gltf.get("images", []):
                bv = image.get("bufferView")
                if bv is None:
                    continue
                v = gltf["bufferViews"][bv]
                blob = binary[v.get("byteOffset", 0) :][: v["byteLength"]]
                if best is None or len(blob) > best[0]:
                    best = (len(blob), blob)
        except Exception:  # noqa: BLE001
            continue

    if best is None:
        print("error: found no embedded textures to preview", file=sys.stderr)
        return 1

    before = Image.open(io.BytesIO(best[1])).convert("RGB")
    after = Image.open(io.BytesIO(stylize_image(best[1], style))).convert("RGB")
    after = after.resize(before.size, Image.NEAREST)  # compare like for like

    strip = Image.new("RGB", (before.width * 2 + 8, before.height), (12, 16, 20))
    strip.paste(before, (0, 0))
    strip.paste(after, (before.width + 8, 0))
    strip.save(out_path)
    print(f"Wrote {out_path} — original left, restyled right ({before.width}x{before.height})")
    print(f"  {style}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

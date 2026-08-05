#!/usr/bin/env python3
"""
Downscale OSM2World's texture library in place.

Why this exists
---------------
OSM2World ships ~40 AmbientCG PBR material sets at 4K, each with Colour,
Normal, ORM and Displacement maps — about 400 MB in total. It *embeds* those
images into every generated .glb rather than referencing them externally, so
the library's size is paid once per tile, not once per world.

Measured on Monaco (~7.8 km², LOD 2, 16 tiles at zoom 15):

    4K textures    1.6 GB total   ~100 MB per tile
    512px textures  see README    ~29x smaller texture payload

At 4K a single tile exceeds what any web client should download, and the whole
of Monaco does not fit in GitHub Pages' 1 GB budget. Downscaling is therefore
not an optimisation — it is what makes the pipeline shippable at all.

512px is plenty for surfaces viewed from the air, which is most of this game.
Raise it if you plan to spend time at street level.

Usage
-----
    tools/shrink-textures.py [--size 512] [--quality 85] [textures_dir]

Run once against a fresh OSM2World download, before baking. It rewrites files
in place, so re-running is cheap (already-small images are skipped) but you
cannot undo it — re-extract the zip to get the originals back.
"""

from __future__ import annotations

import argparse
import glob
import os
import sys
import time

try:
    from PIL import Image
except ImportError:
    sys.exit("error: Pillow is required — pip install Pillow")

# These are legitimately huge source images, not decompression bombs.
Image.MAX_IMAGE_PIXELS = None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("Usage")[0].strip())
    parser.add_argument(
        "textures_dir",
        nargs="?",
        default="vendor/osm2world/textures",
        help="OSM2World textures directory (default: vendor/osm2world/textures)",
    )
    parser.add_argument("--size", type=int, default=512, help="max dimension in px")
    parser.add_argument("--quality", type=int, default=85, help="JPEG quality")
    args = parser.parse_args()

    if not os.path.isdir(args.textures_dir):
        return fail(f"not a directory: {args.textures_dir}")

    files = [
        f
        for pattern in ("*.jpg", "*.jpeg", "*.png")
        for f in glob.glob(os.path.join(args.textures_dir, "**", pattern), recursive=True)
    ]
    if not files:
        return fail(f"no images found under {args.textures_dir}")

    before = sum(os.path.getsize(f) for f in files)
    started = time.time()
    resized = skipped = failed = 0

    for path in files:
        try:
            with Image.open(path) as im:
                if max(im.size) <= args.size:
                    skipped += 1
                    continue
                im.thumbnail((args.size, args.size), Image.LANCZOS)
                if path.lower().endswith(".png"):
                    im.save(path, optimize=True)
                else:
                    im.convert("RGB").save(path, "JPEG", quality=args.quality, optimize=True)
            resized += 1
        except Exception as exc:  # noqa: BLE001 — report and continue
            print(f"  skipped {path}: {exc}", file=sys.stderr)
            failed += 1

    after = sum(os.path.getsize(f) for f in files)
    ratio = before / after if after else 0

    print(
        f"Resized {resized} images to {args.size}px "
        f"({skipped} already small, {failed} failed) in {time.time() - started:.0f}s"
    )
    print(f"Texture library: {mb(before)} -> {mb(after)} ({ratio:.0f}x smaller)")
    return 0


def mb(n: int) -> str:
    return f"{n / 1024 / 1024:.0f} MB"


def fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

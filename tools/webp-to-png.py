#!/usr/bin/env python3
"""
Decode embedded WebP textures in .glb tiles back to PNG.

Why this exists
---------------
Only to feed the KTX2 encoder, which cannot read WebP.

PLATEAU publishes its LOD2 building textures as WebP inside the b3dm payload,
and Basis Universal — both `gltf-transform etc1s` and `optimize
--texture-compress ktx2` — skips any image it does not recognise, with a
warning rather than an error:

    warn: ktx:texture(1/1): Skipping, unsupported texture type "image/webp".

The result looks like a successful run that produced no KTX2 at all. So the
textures have to be in a format the encoder accepts before it sees them, and
PNG is the lossless one.

This is a transcode of an already-lossy source, which is worth being honest
about: PLATEAU's WebP *is* the original, so nothing better exists to encode
from. The alternative is not "higher quality KTX2", it is keeping WebP and
paying full uncompressed VRAM for every resident tile.

Run before tools/optimize-tiles.sh, which does the actual KTX2 encoding.

Usage:
    tools/webp-to-png.py public/tiles/tokyo-shinjuku/bldg
"""

from __future__ import annotations

import argparse
import glob
import io
import json
import os
import struct
import sys

try:
    from PIL import Image
except ImportError:
    print("error: needs Pillow (pip install pillow)", file=sys.stderr)
    raise


def read_glb(path: str) -> tuple[dict, bytes]:
    blob = open(path, "rb").read()
    _magic, _version, total = struct.unpack_from("<III", blob, 0)
    off, gltf, binary = 12, None, b""
    while off < total:
        clen, ctype = struct.unpack_from("<II", blob, off)
        body = blob[off + 8 : off + 8 + clen]
        if ctype == 0x4E4F534A:
            gltf = json.loads(body)
        elif ctype == 0x004E4942:
            binary = body
        off += 8 + clen + (-clen % 4)
    if gltf is None:
        raise ValueError(f"{path}: no JSON chunk")
    return gltf, binary


def write_glb(path: str, gltf: dict, binary: bytes) -> None:
    j = json.dumps(gltf, separators=(",", ":")).encode()
    j += b" " * (-len(j) % 4)
    b = binary + b"\x00" * (-len(binary) % 4)
    total = 12 + 8 + len(j) + (8 + len(b) if b else 0)
    out = bytearray(struct.pack("<III", 0x46546C67, 2, total))
    out += struct.pack("<II", len(j), 0x4E4F534A) + j
    if b:
        out += struct.pack("<II", len(b), 0x004E4942) + b
    open(path, "wb").write(bytes(out))


def convert(path: str) -> int:
    gltf, binary = read_glb(path)
    images = gltf.get("images", [])
    targets = {
        i: im
        for i, im in enumerate(images)
        if im.get("mimeType") == "image/webp" and "bufferView" in im
    }
    if not targets:
        return 0

    # Decode first, so a failure leaves the file untouched.
    replacements: dict[int, bytes] = {}
    for _i, im in targets.items():
        bv = gltf["bufferViews"][im["bufferView"]]
        start = bv.get("byteOffset", 0)
        raw = binary[start : start + bv["byteLength"]]
        buf = io.BytesIO()
        Image.open(io.BytesIO(raw)).save(buf, format="PNG", optimize=False)
        replacements[im["bufferView"]] = buf.getvalue()

    # Every bufferView's offset moves once an image changes size, so the buffer
    # is rebuilt wholesale rather than patched. Order is preserved and each
    # view's internal layout is untouched, so accessors — which address views by
    # index and offset *within* the view — stay valid.
    rebuilt = bytearray()
    for idx, bv in enumerate(gltf["bufferViews"]):
        data = replacements.get(idx)
        if data is None:
            start = bv.get("byteOffset", 0)
            data = binary[start : start + bv["byteLength"]]
        # 4-byte alignment satisfies every accessor component type glTF allows.
        pad = -len(rebuilt) % 4
        rebuilt += b"\x00" * pad
        bv["byteOffset"] = len(rebuilt)
        bv["byteLength"] = len(data)
        rebuilt += data

    for _i, im in targets.items():
        im["mimeType"] = "image/png"

    if gltf.get("buffers"):
        gltf["buffers"][0]["byteLength"] = len(rebuilt)
    # EXT_texture_webp described the WebP that is no longer there. Leaving it
    # declared makes the file invalid for any reader that honours it.
    for key in ("extensionsUsed", "extensionsRequired"):
        if key in gltf:
            gltf[key] = [e for e in gltf[key] if e != "EXT_texture_webp"]
            if not gltf[key]:
                del gltf[key]
    for tex in gltf.get("textures", []):
        ext = tex.get("extensions", {})
        webp = ext.pop("EXT_texture_webp", None)
        if webp is not None:
            tex["source"] = webp["source"]
        if not ext:
            tex.pop("extensions", None)

    write_glb(path, gltf, bytes(rebuilt))
    return len(targets)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("Why this")[0].strip())
    ap.add_argument("tiles_dir")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.tiles_dir, "**", "*.glb"), recursive=True))
    before = sum(os.path.getsize(f) for f in files)
    converted = tiles = 0
    for path in files:
        n = convert(path)
        if n:
            tiles += 1
            converted += n
    after = sum(os.path.getsize(f) for f in files)
    print(
        f"{converted} WebP textures -> PNG across {tiles} tiles "
        f"({before / 1e6:.1f} -> {after / 1e6:.1f} MB, temporarily)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

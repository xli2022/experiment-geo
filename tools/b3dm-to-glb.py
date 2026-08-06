#!/usr/bin/env python3
"""
Convert a 3D Tiles 1.0 tileset from .b3dm content to plain .glb.

Why this exists
---------------
b3dm is the legacy container. 3D Tiles 1.1 dropped it in favour of glTF as tile
content directly, and every tileset this project bakes itself already emits
.glb — so a downloaded PLATEAU tileset was the only thing keeping a second
content format alive in the tree.

Unifying is not just tidiness. A b3dm wraps its glTF in a header plus feature
and batch tables, so nothing in the toolchain that speaks glTF can touch it:
tools/optimize-tiles.sh finds files by `-name '*.glb'` and skips a b3dm
entirely, which meant Tokyo shipped uncompressed textures while Berlin shipped
WebP. Converting brings it under the same optimizer.

What is dropped, deliberately
-----------------------------
The batch table. It carries per-feature attributes — building IDs, heights,
usage codes — addressed by the `_BATCHID` vertex attribute. Nothing here reads
them; the renderer draws geometry and the palette comes from materials. 3D
Tiles 1.1 would carry the same data as `EXT_structural_metadata`, so this is
the point to revisit if features ever need to be selectable.

What is preserved
-----------------
The RTC offset. b3dm tiles sit far from the origin and carry their true centre
either as `RTC_CENTER` in the feature table or as the `CESIUM_RTC` glTF
extension. Both are folded into a root node translation, which is exactly
equivalent and leaves a file that needs no extension to read correctly — the
extension is itself a Cesium-specific holdover that 1.1 has no use for. Lose it
and the geometry lands near the centre of the earth.

Usage:
    tools/b3dm-to-glb.py public/tiles/tokyo-shinjuku/bldg
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import struct
import sys

B3DM_HEADER = struct.Struct("<4sIIIIII")


def split_b3dm(blob: bytes) -> tuple[dict, bytes]:
    """Return (feature table JSON, embedded glTF bytes)."""
    magic, _version, _length, ftj, ftb, btj, btb = B3DM_HEADER.unpack_from(blob, 0)
    if magic != b"b3dm":
        raise ValueError(f"not a b3dm (magic {magic!r})")
    feature = json.loads(blob[28 : 28 + ftj] or b"{}")
    start = 28 + ftj + ftb + btj + btb
    if blob[start : start + 4] != b"glTF":
        raise ValueError("no glTF payload at the expected offset")
    # Trust the glTF's own length rather than the rest of the file: a b3dm is
    # padded to 8-byte alignment, and trailing padding makes an invalid .glb.
    (gltf_len,) = struct.unpack_from("<I", blob, start + 8)
    return feature, blob[start : start + gltf_len]


def parse_glb(glb: bytes) -> tuple[dict, bytes, bytes]:
    """Return (json chunk, binary chunk, everything after) for a .glb."""
    _magic, _version, total = struct.unpack_from("<III", glb, 0)
    off = 12
    gltf: dict | None = None
    binary = b""
    while off < total:
        clen, ctype = struct.unpack_from("<II", glb, off)
        body = glb[off + 8 : off + 8 + clen]
        if ctype == 0x4E4F534A:  # 'JSON'
            gltf = json.loads(body)
        elif ctype == 0x004E4942:  # 'BIN'
            binary = body
        off += 8 + clen + (-clen % 4)
    if gltf is None:
        raise ValueError("glb has no JSON chunk")
    return gltf, binary, b""


def write_glb(gltf: dict, binary: bytes) -> bytes:
    j = json.dumps(gltf, separators=(",", ":")).encode()
    j += b" " * (-len(j) % 4)
    b = binary + b"\x00" * (-len(binary) % 4)
    total = 12 + 8 + len(j) + (8 + len(b) if b else 0)
    out = bytearray(struct.pack("<III", 0x46546C67, 2, total))
    out += struct.pack("<II", len(j), 0x4E4F534A) + j
    if b:
        out += struct.pack("<II", len(b), 0x004E4942) + b
    return bytes(out)


def rtc_center(feature: dict, gltf: dict) -> list[float] | None:
    """The tile's relative-to-centre offset, from wherever it was recorded."""
    if "RTC_CENTER" in feature:
        return list(feature["RTC_CENTER"])
    ext = (gltf.get("extensions") or {}).get("CESIUM_RTC")
    if ext and "center" in ext:
        return list(ext["center"])
    return None


def bake_rtc(gltf: dict, center: list[float]) -> None:
    """Fold the offset into a new root node, and drop the extension.

    A new parent rather than editing the existing roots: a root may already
    carry a matrix, and composing a translation into an arbitrary matrix is
    easy to get subtly wrong. Wrapping is always correct.
    """
    nodes = gltf.setdefault("nodes", [])
    for scene in gltf.get("scenes", []):
        roots = scene.get("nodes", [])
        if not roots:
            continue
        nodes.append({"translation": center, "children": list(roots)})
        scene["nodes"] = [len(nodes) - 1]

    if "extensions" in gltf:
        gltf["extensions"].pop("CESIUM_RTC", None)
        if not gltf["extensions"]:
            del gltf["extensions"]
    for key in ("extensionsUsed", "extensionsRequired"):
        if key in gltf:
            gltf[key] = [e for e in gltf[key] if e != "CESIUM_RTC"]
            if not gltf[key]:
                del gltf[key]


def convert_tile(path: str) -> str:
    blob = open(path, "rb").read()
    feature, glb = split_b3dm(blob)
    gltf, binary, _ = parse_glb(glb)

    center = rtc_center(feature, gltf)
    if center:
        bake_rtc(gltf, center)

    out = path[: -len(".b3dm")] + ".glb"
    with open(out, "wb") as fh:
        fh.write(write_glb(gltf, binary))
    os.remove(path)
    return out


def retarget_tilesets(root: str) -> int:
    """Point every content URI at the .glb that replaced its .b3dm."""
    changed = 0

    def fix(node: dict) -> None:
        nonlocal changed
        content = node.get("content")
        if content and content.get("uri", "").endswith(".b3dm"):
            content["uri"] = content["uri"][: -len(".b3dm")] + ".glb"
            changed += 1
        for child in node.get("children", []):
            fix(child)

    for path in glob.glob(os.path.join(root, "**", "*.json"), recursive=True):
        doc = json.load(open(path))
        if "root" not in doc:
            continue
        fix(doc["root"])
        # The container is the only thing that made this 1.0 rather than 1.1.
        doc.setdefault("asset", {})["version"] = "1.1"
        json.dump(doc, open(path, "w"), separators=(",", ":"))
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("Why this")[0].strip())
    ap.add_argument("tiles_dir", help="directory holding tileset.json and its content")
    args = ap.parse_args()

    tiles = sorted(glob.glob(os.path.join(args.tiles_dir, "**", "*.b3dm"), recursive=True))
    if not tiles:
        print(f"no .b3dm under {args.tiles_dir} — nothing to do", file=sys.stderr)
        return 0

    before = sum(os.path.getsize(t) for t in tiles)
    rtc = 0
    for path in tiles:
        blob = open(path, "rb").read()
        feature, glb = split_b3dm(blob)
        gltf, _, _ = parse_glb(glb)
        if rtc_center(feature, gltf):
            rtc += 1
        convert_tile(path)

    refs = retarget_tilesets(args.tiles_dir)
    after = sum(
        os.path.getsize(p)
        for p in glob.glob(os.path.join(args.tiles_dir, "**", "*.glb"), recursive=True)
    )
    print(
        f"{len(tiles)} tiles b3dm -> glb ({before / 1e6:.1f} -> {after / 1e6:.1f} MB), "
        f"{rtc} carried an RTC centre, {refs} content URIs retargeted"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

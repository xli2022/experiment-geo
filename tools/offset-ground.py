#!/usr/bin/env python3
"""
Separate the ground layers vertically so they stop fighting for the same depth.

Why this exists
---------------
OSM2World draws terrain, roads, pavements, kerbs, rail ballast and lane
markings all at ground level, and at ground level means *exactly* y = 0.
Measured in one z15 tile of Berlin Mitte: 19,902 near-horizontal triangles
within 6 cm of y = 0, of which 35.8% are asphalt, 31.3% terrain, 15.4%
pavement and 3.5% road markings — every one of them competing for the same
depth value.

The result is the thin flickering lines that trace kerbs, road edges and area
boundaries. It is not fixable in the renderer: a depth buffer cannot order
coplanar surfaces, and `logarithmicDepthBuffer` (already enabled) improves
precision across distance rather than resolving exact ties. Nor can polygon
offset help, because a tile is a single material — there is nothing to bias
one layer against another.

So the fix is in the data: push each layer to its own height, in the order a
street is actually built. Offsets are centimetres, far below what reads as a
step at any altitude you fly at, but far above the depth buffer's resolution
at these ranges.

Only near-horizontal triangles near the ground are touched, so a wall sharing
a material with a pavement is left alone.

Run on the raw bake, before tools/optimize-tiles.sh — Draco-compressed
accessors have no readable buffer view.

Usage:
    tools/offset-ground.py public/tiles/berlin
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import struct
import sys

import numpy as np

# Palette colour -> metres of vertical offset, ordered the way a street is
# actually built: ground below, made surfaces above it, paint on top of those,
# and the pavement raised over the carriageway as a real kerb would be.
#
# The 3 cm step is set by the compressor, not by taste. Draco quantizes
# positions onto a uniform grid sized by the mesh's largest extent, so a 750 m
# tile lands every axis on ~11.4 mm at the 16 bits tools/optimize-tiles.sh now
# requests. An earlier version of this table used 5–20 mm steps against Draco's
# *default* 14 bits, which is a 45.8 mm grid — every separation below half a
# step collapsed back to a shared height during compression, so the offsets
# were applied correctly and then silently undone. Keep the smallest gap here
# at least twice the grid.
LAYERS = {
    "#a9bd8d": -0.12,  # TERRAIN_DEFAULT — the sheet everything else sits on
    "#8fb573": -0.09,  # GRASS
    "#7fa365": -0.09,  # SCRUB
    "#6f9457": -0.09,  # HEDGE
    "#a68f6d": -0.09,  # EARTH
    "#dcc9a0": -0.09,  # SAND
    "#ddd3c0": -0.09,  # SHELLS
    "#9c968c": -0.06,  # RAIL_BALLAST
    "#8a8580": -0.06,  # RAILWAY
    "#b2aca2": -0.06,  # GRAVEL
    "#aaa49a": -0.06,  # SCREE
    "#b7b1a7": -0.06,  # PEBBLESTONE
    # ASPHALT stays at 0 — it is the reference the rest are placed around.
    "#f2f2ee": 0.03,  # ROAD_MARKING and friends, painted onto the carriageway
    "#d98a7a": 0.03,  # RED_ROAD_MARKING
    "#b4aea4": 0.09,  # SETT — pavements, genuinely above the road
    "#aca69c": 0.09,  # UNHEWN_COBBLESTONE
    "#c0bab0": 0.09,  # PAVING_STONE
    "#c7c2b8": 0.09,  # CONCRETE
    "#c8c3ba": 0.12,  # KERB — the top of the stack, as on a real street
}

# A triangle counts as ground if it is near-horizontal and near y = 0. Both
# tests matter: the first keeps walls that share a material (concrete, brick)
# out of it, the second keeps roofs and bridge decks out.
NORMAL_UP = 0.9
GROUND_BAND = 1.5  # metres either side of y = 0

MATCH_EPS = 2.0e-3


def srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_to_linear(value: str) -> tuple[float, float, float]:
    v = value.lstrip("#")
    return tuple(srgb_to_linear(int(v[i : i + 2], 16) / 255.0) for i in (0, 2, 4))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("Usage")[0].strip())
    ap.add_argument("tiles_dir")
    args = ap.parse_args()

    paths = sorted(glob.glob(os.path.join(args.tiles_dir, "**", "*.glb"), recursive=True))
    if not paths:
        print(f"error: no .glb under {args.tiles_dir}", file=sys.stderr)
        return 1

    layers = [(np.array(hex_to_linear(h)), dy) for h, dy in LAYERS.items()]

    total = 0
    tiles = 0
    for path in paths:
        moved = process(path, layers)
        if moved is None:
            continue
        total += moved
        tiles += 1
        if moved:
            print(f"  {os.path.relpath(path, args.tiles_dir)}: {moved:,} vertices")

    print(f"Offset {total:,} ground vertices across {tiles} tiles")
    return 0


def process(path: str, layers) -> int | None:
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:4] != b"glTF":
        return None

    json_len = struct.unpack("<I", data[12:16])[0]
    gltf = json.loads(data[20 : 20 + json_len])
    binary = bytearray(data[20 + json_len + 8 :])

    moved = 0
    for mesh in gltf.get("meshes", []):
        for prim in mesh.get("primitives", []):
            attrs = prim.get("attributes", {})
            if not {"POSITION", "COLOR_0", "NORMAL"} <= set(attrs):
                continue
            positions, p_off, count = read_vec3(gltf, binary, attrs["POSITION"])
            colors, _, _ = read_vec3(gltf, binary, attrs["COLOR_0"])
            normals, _, _ = read_vec3(gltf, binary, attrs["NORMAL"])
            if positions is None or colors is None or normals is None:
                continue

            tris = read_indices(gltf, binary, prim, count)
            if tris is None:
                continue

            # Per-triangle so a shared material on a wall is never dragged down
            # with the pavement it also surfaces.
            flat = np.abs(normals[tris[:, 0]][:, 1]) > NORMAL_UP
            low = np.abs(positions[tris][:, :, 1]).max(axis=1) < GROUND_BAND
            candidates = tris[flat & low]
            if not len(candidates):
                continue

            for base, dy in layers:
                matched = np.all(np.abs(colors - base) < MATCH_EPS, axis=1)
                sel = candidates[np.all(matched[candidates], axis=1)]
                if not len(sel):
                    continue
                verts = np.unique(sel)
                positions[verts, 1] += dy
                moved += len(verts)

            write_vec3(binary, p_off, positions)
            update_bounds(gltf, attrs["POSITION"], positions)

    if not moved:
        return 0

    rebuild(path, gltf, binary)
    return moved


def update_bounds(gltf: dict, index: int, positions: np.ndarray) -> None:
    """Accessor min/max are required by the spec and are what the tileset's
    bounding volumes are derived from, so they have to follow the geometry."""
    acc = gltf["accessors"][index]
    acc["min"] = [float(v) for v in positions.min(axis=0)]
    acc["max"] = [float(v) for v in positions.max(axis=0)]


def read_vec3(gltf: dict, binary: bytearray, index: int):
    acc = gltf["accessors"][index]
    if acc.get("type") != "VEC3" or acc.get("componentType") != 5126 or "bufferView" not in acc:
        return None, None, 0
    view = gltf["bufferViews"][acc["bufferView"]]
    off = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    count = acc["count"]
    if (view.get("byteStride") or 12) != 12:
        return None, None, 0
    arr = np.frombuffer(binary, dtype="<f4", count=count * 3, offset=off).reshape(count, 3)
    return arr.astype(np.float64), off, count


def write_vec3(binary: bytearray, off: int, values: np.ndarray) -> None:
    binary[off : off + values.size * 4] = values.astype("<f4").tobytes()


def read_indices(gltf: dict, binary: bytearray, prim: dict, count: int):
    if "indices" not in prim:
        return np.arange(count, dtype=np.int64).reshape(-1, 3)
    acc = gltf["accessors"][prim["indices"]]
    if "bufferView" not in acc:
        return None
    view = gltf["bufferViews"][acc["bufferView"]]
    off = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    dtype = {5121: "<u1", 5123: "<u2", 5125: "<u4"}.get(acc["componentType"])
    if dtype is None:
        return None
    return np.frombuffer(binary, dtype=dtype, count=acc["count"], offset=off).astype(
        np.int64
    ).reshape(-1, 3)


def rebuild(path: str, gltf: dict, binary: bytearray) -> None:
    """
    Reassemble the GLB from scratch.

    The JSON chunk changes length here — accessor min/max gain or lose digits —
    so the chunk headers and the total file length cannot be reused from the
    original. Both chunks are padded to a 4-byte boundary as the spec requires,
    JSON with spaces and BIN with zeros; getting that wrong yields a file that
    parses in some loaders and not others.
    """
    json_bytes = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * (-len(json_bytes) % 4)
    bin_bytes = bytes(binary)
    bin_bytes += b"\x00" * (-len(bin_bytes) % 4)

    total = 12 + 8 + len(json_bytes) + 8 + len(bin_bytes)
    out = bytearray()
    out += b"glTF" + struct.pack("<II", 2, total)
    out += struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
    out += struct.pack("<II", len(bin_bytes), 0x004E4942) + bin_bytes

    with open(path, "wb") as fh:
        fh.write(out)


if __name__ == "__main__":
    sys.exit(main())

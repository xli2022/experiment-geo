#!/usr/bin/env python3
"""
Give each roof its own colour, so a low-poly skyline is not one flat sheet.

Why this exists
---------------
OSM2World assigns a material per *feature type*, not per building, so every
roof without an explicit OSM tag lands on ROOF_DEFAULT. Since `building:colour`
and `roof:colour` cover well under 1% of buildings, that means practically the
whole city is one shade of terracotta — accurate to the data and visually
monotonous. Reference low-poly cities get much of their character from roofs
varying building to building.

The variation has to be synthetic, and it has to be *per roof*: colouring by
triangle gives a patchwork within one roof, and colouring by a spatial grid
splits any roof that straddles a cell boundary. So this finds connected
components among roof triangles and colours each component as a unit.

Components are built only from roof-coloured triangles, never through walls.
Two adjacent buildings in a terrace share a wall but rarely a roof plane at the
same height, so they stay separate — which is what keeps a Berlin block from
collapsing into one colour.

The choice per roof is a hash of the component's centroid, so it is stable
across re-bakes: the same building keeps its colour as long as its geometry
does, and a re-bake does not reshuffle the whole city.

Run this on the raw bake, before tools/optimize-tiles.sh — Draco-compressed
accessors have no readable buffer view.

Usage:
    tools/vary-roofs.py public/tiles/berlin
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import struct
import sys

import numpy as np

# Roofs to spread buildings across. The first entry must be the colour
# OSM2World actually emits for ROOF_DEFAULT, since that is what is matched;
# the rest are the variants. Keep them close in value — varying hue with a
# similar lightness reads as "different buildings", whereas varying lightness
# reads as "inconsistent lighting".
ROOF_BASE = "#c4694a"
ROOF_VARIANTS = (
    "#c4694a",  # terracotta, the base
    "#b25c43",  # deeper brick
    "#cd7a55",  # lighter clay
    "#a85f4e",  # brown-red
    "#b9705c",  # muted rose
    "#8f6157",  # weathered brown
    "#a5745f",  # sandy brown
    "#c98661",  # pale terracotta
)

# How close a vertex colour must be to ROOF_BASE (in linear space, per channel)
# to count as roof. OSM2World writes the configured colour exactly, so this only
# needs to absorb float round-trip.
MATCH_EPS = 2.0e-3


def srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_to_linear(value: str) -> tuple[float, float, float]:
    v = value.lstrip("#")
    return tuple(srgb_to_linear(int(v[i : i + 2], 16) / 255.0) for i in (0, 2, 4))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("Usage")[0].strip())
    ap.add_argument("tiles_dir")
    ap.add_argument(
        "--weld",
        type=float,
        default=0.01,
        help="metres; positions closer than this are one vertex when joining roof triangles",
    )
    args = ap.parse_args()

    paths = sorted(glob.glob(os.path.join(args.tiles_dir, "**", "*.glb"), recursive=True))
    if not paths:
        return fail(f"no .glb under {args.tiles_dir}")

    base = np.array(hex_to_linear(ROOF_BASE))
    variants = np.array([hex_to_linear(v) for v in ROOF_VARIANTS])

    total_roofs = 0
    total_tiles = 0
    for path in paths:
        n = process(path, base, variants, args.weld)
        if n is None:
            continue
        total_roofs += n
        total_tiles += 1
        print(f"  {os.path.relpath(path, args.tiles_dir)}: {n} roofs")

    print(f"Varied {total_roofs} roofs across {total_tiles} tiles")
    return 0


def process(path: str, base: np.ndarray, variants: np.ndarray, weld: float) -> int | None:
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:4] != b"glTF":
        return None

    json_len = struct.unpack("<I", data[12:16])[0]
    gltf = json.loads(data[20 : 20 + json_len])
    bin_off = 20 + json_len + 8
    binary = bytearray(data[bin_off:])

    roofs = 0
    for mesh in gltf.get("meshes", []):
        for prim in mesh.get("primitives", []):
            attrs = prim.get("attributes", {})
            if "COLOR_0" not in attrs or "POSITION" not in attrs:
                continue
            colors, c_off, c_count = read_vec3(gltf, binary, attrs["COLOR_0"])
            positions, _, _ = read_vec3(gltf, binary, attrs["POSITION"])
            if colors is None or positions is None or len(colors) != len(positions):
                continue

            tris = read_indices(gltf, binary, prim, c_count)
            if tris is None:
                continue

            is_roof_vertex = np.all(np.abs(colors - base) < MATCH_EPS, axis=1)
            roof_tris = tris[np.all(is_roof_vertex[tris], axis=1)]
            if not len(roof_tris):
                continue

            labels = components(positions, roof_tris, weld)
            if labels is None:
                continue

            # Hash each component's centroid to a variant. Quantised to 10 cm so
            # float noise between runs cannot flip a building to another colour.
            for label in np.unique(labels):
                verts = np.unique(roof_tris[labels == label])
                centroid = positions[verts].mean(axis=0)
                key = tuple(int(round(v * 10)) for v in centroid)
                idx = stable_hash(key) % len(variants)
                colors[verts] = variants[idx]
                roofs += 1

            write_vec3(binary, c_off, colors)

    if not roofs:
        return 0

    rebuild(path, data, json_len, binary)
    return roofs


def stable_hash(key: tuple[int, int, int]) -> int:
    """
    FNV-1a. Python's hash() is salted per process, which would reshuffle every
    roof on each run and make re-bakes non-reproducible.
    """
    h = 0xCBF29CE484222325
    for value in key:
        for byte in int(value & 0xFFFFFFFF).to_bytes(4, "little"):
            h ^= byte
            h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def components(positions: np.ndarray, tris: np.ndarray, weld: float) -> np.ndarray | None:
    """
    Label each roof triangle with the connected component it belongs to.

    Vertices are welded by rounded position first: OSM2World emits unindexed
    geometry, so triangles of one roof share coordinates but not indices, and
    without welding every triangle is its own component.
    """
    verts = np.unique(tris)
    if not len(verts):
        return None

    quant = np.round(positions[verts] / weld).astype(np.int64)
    _, welded = np.unique(quant, axis=0, return_inverse=True)
    remap = np.full(positions.shape[0], -1, dtype=np.int64)
    remap[verts] = welded

    parent = np.arange(welded.max() + 1, dtype=np.int64)

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for tri in tris:
        a, b, c = remap[tri]
        for other in (b, c):
            ra, rb = find(a), find(other)
            if ra != rb:
                parent[rb] = ra

    return np.array([find(remap[tri[0]]) for tri in tris], dtype=np.int64)


def read_vec3(gltf: dict, binary: bytearray, index: int):
    acc = gltf["accessors"][index]
    if acc.get("type") != "VEC3" or acc.get("componentType") != 5126 or "bufferView" not in acc:
        return None, None, 0
    view = gltf["bufferViews"][acc["bufferView"]]
    off = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    count = acc["count"]
    stride = view.get("byteStride") or 12
    if stride != 12:
        return None, None, 0
    arr = np.frombuffer(binary, dtype="<f4", count=count * 3, offset=off).reshape(count, 3)
    return arr.astype(np.float64), off, count


def write_vec3(binary: bytearray, off: int, values: np.ndarray) -> None:
    binary[off : off + values.size * 4] = values.astype("<f4").tobytes()


def read_indices(gltf: dict, binary: bytearray, prim: dict, count: int):
    if "indices" not in prim:
        # Unindexed: triangles are consecutive vertex triples.
        return np.arange(count, dtype=np.int64).reshape(-1, 3)
    acc = gltf["accessors"][prim["indices"]]
    if "bufferView" not in acc:
        return None
    view = gltf["bufferViews"][acc["bufferView"]]
    off = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    dtype = {5121: "<u1", 5123: "<u2", 5125: "<u4"}.get(acc["componentType"])
    if dtype is None:
        return None
    arr = np.frombuffer(binary, dtype=dtype, count=acc["count"], offset=off)
    return arr.astype(np.int64).reshape(-1, 3)


def rebuild(path: str, original: bytes, json_len: int, binary: bytearray) -> None:
    """Write the GLB back with its JSON chunk untouched and the new BIN chunk."""
    head = original[: 20 + json_len]
    bin_header = original[20 + json_len : 20 + json_len + 8]
    out = bytearray(head + bin_header + bytes(binary))
    struct.pack_into("<I", out, 8, len(out))
    with open(path, "wb") as fh:
        fh.write(out)


def fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Give each building its own roof and wall colour, so a low-poly city is not one
flat sheet.

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
    tools/vary-buildings.py public/tiles/berlin-mitte/all
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

# Walls need a much tighter spread than roofs, and ship at half of even this
# (see --wall-strength, which defaults to 0.5).
#
# The reason is that the variation is a *hash* — nothing about a building
# decides its colour, so at full strength the differences read as arbitrary
# rather than characterful. Rendering the same view at strength 0, 0.5 and 1
# showed how little walls contribute from the air in any case: roofs carry
# nearly all the visible variety, and walls only assert themselves at street
# level, which is precisely where randomness is most obvious. Half keeps
# adjacent buildings from merging without inviting the question of why one is
# ochre and its neighbour is grey.
WALL_BASE = "#e6dfd1"
WALL_VARIANTS = (
    "#e6dfd1",  # the base cream
    "#d6cdb8",  # sand
    "#e4e1dd",  # cool white
    "#cdc2ad",  # taupe
    "#dbd1bc",  # warm stone
    "#c7c1b6",  # grey
    "#eee4cd",  # pale ochre
    "#d0ccc4",  # pale slate
)

# Metres each component is pushed out along its own normals, chosen from the
# same hash that picks its colour.
#
# Colouring buildings individually exposed z-fighting that uniform colour had
# been hiding: where two building surfaces are coincident — an OSM `building`
# outline against one of its `building:part` children, or two neighbours whose
# footprints share an edge — they used to be the same colour, so whichever won
# a pixel looked identical. Give them different colours and the tie becomes a
# visible flicker across whole roofs and facades.
#
# Nudging by component rather than by material is what separates them, since
# they are the same material by definition.
#
# The step count is what decides how well this works: two coincident components
# stay tied whenever their hashes pick the same step, so the collision rate is
# 1/NUDGE_STEPS and nothing else changes it.
#
# The step *size* used to be set by the Draco grid in tools/optimize-tiles.sh —
# 0.72 mm at 20 bits — on the reasoning that anything coarser survives
# compression and is therefore separated. It survives compression and still
# tiles on screen. The grid is a floor, not the requirement: the depth buffer,
# float32 in the vertex shader and the angle a surface is seen at all eat into
# the gap after compression is done with it. Measured with test/_sepsweep.mjs,
# nothing below 8 mm changed the number of fighting pixels at all — 2 mm was
# indistinguishable from applying no nudge whatsoever.
#
# So 8 mm, and four levels rather than sixteen. Two components colliding is now
# 1 in 4 instead of 1 in 16, which is worse; but the fifteen non-colliding cases
# in sixteen were all tiling anyway, so a quarter fighting beats all of them.
#
# The range stops at 32 mm so it stays clear of the offsets stacked above it in
# offset-ground.py — windows at 40 mm, doors at 56, the unlisted-colour fallback
# from 80. They have to be disjoint rather than merely different: all of them
# push along a surface's own normal, so a component whose nudge equals a
# neighbour's level is exactly coplanar with it. It also has to stop below the
# windows and doors for a second reason — they carry their own colours and so
# never receive this nudge, and a wall that overtook them would swallow them.
NUDGE_STEP = 0.008
NUDGE_STEPS = 4

# (label, base colour, variants, nudge direction).
#
# The direction matters more than the distance. A roof's faces point at the sky,
# so nudging one outward lifts it clear of the walls it sits on and leaves a slot
# you can see straight through along the whole eaves line — a far worse artifact
# than the z-fighting the nudge exists to end. Inward sinks it into the building
# instead, where the walls in front of it hide the offset completely.
#
# Walls are the opposite: outward buries a shared party wall in the neighbour it
# abuts, which is invisible, while inward would pull the two apart and open the
# gap between them. So each goes the way that moves it into solid geometry.
TARGETS = (
    ("roof", ROOF_BASE, ROOF_VARIANTS, -1.0),
    ("wall", WALL_BASE, WALL_VARIANTS, +1.0),
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
        "--wall-strength",
        type=float,
        default=0.5,
        help="0 leaves walls uniform, 1 uses the full palette spread, values between blend",
    )
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

    targets = []
    for label, base, variants, direction in TARGETS:
        base_lin = np.array(hex_to_linear(base))
        spread = np.array([hex_to_linear(v) for v in variants])
        if label == "wall":
            # Blend each variant toward the base colour. At 0 every wall keeps
            # the base and the pass is a no-op; at 1 the palette is used as
            # written. Interpolating in linear space keeps the midpoints from
            # drifting dark the way an sRGB blend would.
            spread = base_lin + (spread - base_lin) * args.wall_strength
        targets.append((label, base_lin, spread, direction))

    totals: dict[str, int] = {label: 0 for label, *_ in TARGETS}
    total_tiles = 0
    for path in paths:
        counts = process(path, targets, args.weld)
        if counts is None:
            continue
        for label, n in counts.items():
            totals[label] += n
        total_tiles += 1
        summary = ", ".join(f"{n} {label}s" for label, n in counts.items())
        print(f"  {os.path.relpath(path, args.tiles_dir)}: {summary}")

    parts = ", ".join(f"{n} {label}s" for label, n in totals.items())
    print(f"Varied {parts} across {total_tiles} tiles")
    return 0


def process(path: str, targets: list, weld: float) -> dict[str, int] | None:
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:4] != b"glTF":
        return None

    json_len = struct.unpack("<I", data[12:16])[0]
    gltf = json.loads(data[20 : 20 + json_len])
    bin_off = 20 + json_len + 8
    binary = bytearray(data[bin_off:])

    counts = {label: 0 for label, *_ in targets}
    changed = False
    for mesh in gltf.get("meshes", []):
        for prim in mesh.get("primitives", []):
            attrs = prim.get("attributes", {})
            if "COLOR_0" not in attrs or "POSITION" not in attrs:
                continue
            colors, c_off, c_count = read_vec3(gltf, binary, attrs["COLOR_0"])
            positions, p_off, _ = read_vec3(gltf, binary, attrs["POSITION"])
            normals, _, _ = (
                read_vec3(gltf, binary, attrs["NORMAL"])
                if "NORMAL" in attrs
                else (None, None, 0)
            )
            if colors is None or positions is None or len(colors) != len(positions):
                continue

            tris = read_indices(gltf, binary, prim, c_count)
            if tris is None:
                continue

            for name, base, variants, direction in targets:
                matched = np.all(np.abs(colors - base) < MATCH_EPS, axis=1)
                target_tris = tris[np.all(matched[tris], axis=1)]
                if not len(target_tris):
                    continue

                labels = components(positions, target_tris, weld)
                if labels is None:
                    continue

                # Hash each component's centroid to a variant. Quantised to 10 cm
                # so float noise between runs cannot flip a building to another
                # colour.
                for label in np.unique(labels):
                    verts = np.unique(target_tris[labels == label])
                    centroid = positions[verts].mean(axis=0)
                    key = tuple(int(round(v * 10)) for v in centroid)
                    # Offset the hash per target so a building's wall and roof
                    # do not land on the same index in their palettes, which
                    # would correlate them into visible stripes across the city.
                    h = stable_hash(key)
                    idx = (h + len(name)) % len(variants)
                    colors[verts] = variants[idx]
                    if normals is not None:
                        # Same hash, so a component's nudge is as stable across
                        # re-bakes as its colour.
                        # +1 so the nudge is never zero. A component that does
                        # not move stays tied to every neighbour this tool never
                        # touched — an OSM-coloured roof, a concrete deck — and
                        # one step of the hash landing on zero was what left
                        # those pairs fighting.
                        positions[verts] += normals[verts] * (
                            direction * ((h % NUDGE_STEPS) + 1) * NUDGE_STEP
                        )
                    counts[name] += 1
                    changed = True

            write_vec3(binary, c_off, colors)
            if normals is not None:
                write_vec3(binary, p_off, positions)
                update_bounds(gltf, attrs["POSITION"], positions)

    if not changed:
        return counts

    rebuild(path, gltf, binary)
    return counts


def update_bounds(gltf: dict, index: int, positions: np.ndarray) -> None:
    """Accessor min/max are required by the spec and feed the tileset's bounding
    volumes, so they have to follow the nudged geometry."""
    acc = gltf["accessors"][index]
    acc["min"] = [float(v) for v in positions.min(axis=0)]
    acc["max"] = [float(v) for v in positions.max(axis=0)]


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


def rebuild(path: str, gltf: dict, binary: bytearray) -> None:
    """
    Reassemble the GLB from scratch.

    The JSON chunk cannot be reused verbatim any more: nudging components moves
    geometry, so accessor min/max change and the chunk's length changes with
    them. Both chunks are padded to a 4-byte boundary as the spec requires,
    JSON with spaces and BIN with zeros.
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


def fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

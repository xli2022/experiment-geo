#!/usr/bin/env python3
"""
Generate a root tileset.json for an OSM2World tile bake.

Why this exists
---------------
`osm2world tileset` writes one self-contained `<y>.tileset.json` per tile and
no root that ties them together. A 3D Tiles client needs a single entry point,
so without this step there is nothing for `3d-tiles-renderer` to load.

This walks the bake, reads each per-tile tileset, and emits a root whose
children reference them as external tilesets.

It also repairs the child bounding volumes. OSM2World writes the *same*
`boundingVolume.region` into every tile — the bounds of the whole bake rather
than of that tile. A 3D Tiles `region` is geographic and is explicitly not
affected by the tile transform, so every tile ends up claiming to cover the
entire area. The client then cannot cull or prioritise anything, the root's
bounding sphere comes out an order of magnitude too large, and screen-space
error never drops far enough to refine — you get a loaded root tileset and
zero rendered triangles.

Since the tiles are laid out on a standard XYZ grid, the correct bounds are
recoverable from each tile's own z/x/y path. Height range is kept from the
file, which is per-tile and correct.

Usage:
    tools/make-root-tileset.py public/tiles/monaco
"""

from __future__ import annotations

import argparse
import json
import math
import os
import struct
import sys

WGS84_A = 6378137.0
WGS84_B = 6356752.314245
WGS84_E2 = 6.69437999014e-3
WGS84_EP2 = (WGS84_A**2 - WGS84_B**2) / WGS84_B**2

# glTF is Y-up, 3D Tiles is Z-up, and the client applies this between the glTF
# content and the tile transform (asset.gltfUpAxis, default Y).
YUP_TO_ZUP = (1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("Usage")[0].strip())
    parser.add_argument("tiles_dir", help="directory containing the bake (e.g. public/tiles/monaco)")
    parser.add_argument(
        "--output", default=None, help="output path (default: <tiles_dir>/tileset.json)"
    )
    args = parser.parse_args()

    root_dir = os.path.abspath(args.tiles_dir)
    if not os.path.isdir(root_dir):
        return fail(f"not a directory: {args.tiles_dir}")

    out_path = args.output or os.path.join(root_dir, "tileset.json")

    children = []
    repaired = 0
    for dirpath, _dirnames, filenames in os.walk(root_dir):
        for name in sorted(filenames):
            if not name.endswith(".tileset.json"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root_dir).replace(os.sep, "/")
            try:
                with open(path, encoding="utf-8") as fh:
                    tileset = json.load(fh)
                node = tileset["root"]
            except (OSError, ValueError, KeyError) as exc:
                print(f"  skipping {rel}: {exc}", file=sys.stderr)
                continue

            region = node.get("boundingVolume", {}).get("region")
            # Prefer the tile's real geometry extent. Path-derived bounds are a
            # fallback because OSM2World lets geometry overflow the tile it is
            # filed under — measured at ±779 m in a z15 tile whose true
            # half-width is 611 m, so clipping to the grid would cull a quarter
            # of the content at the edges.
            tile_region = region_from_geometry(path, node) or region_from_path(rel, region)
            if tile_region is None:
                print(f"  skipping {rel}: no usable bounding region", file=sys.stderr)
                continue

            # Repair the child's *own* region too, not just the reference to it
            # here. When a tile's content is an external tileset the client
            # adopts that tileset's root bounding volume, so leaving the
            # whole-bake region in place there defeats culling just as
            # thoroughly — every tile claims the entire area and all of them
            # stay resident at every altitude.
            if node.get("boundingVolume", {}).get("region") != tile_region:
                node.setdefault("boundingVolume", {})["region"] = tile_region
                try:
                    with open(path, "w", encoding="utf-8") as fh:
                        json.dump(tileset, fh, separators=(",", ":"))
                    repaired += 1
                except OSError as exc:
                    print(f"  could not rewrite {rel}: {exc}", file=sys.stderr)

            # The child tileset carries its own transform; repeating it here
            # would apply it twice.
            children.append(
                {
                    "geometricError": node.get("geometricError", 0),
                    "boundingVolume": {"region": tile_region},
                    "content": {"uri": rel},
                }
            )

    if not children:
        return fail(f"no *.tileset.json found under {args.tiles_dir}")

    region = union_regions(
        [c["boundingVolume"]["region"] for c in children if "region" in c["boundingVolume"]]
    )
    if region is None:
        return fail("children have no region bounding volumes — cannot build a root bound")

    # The root must be at least as coarse as any child, or clients may refine
    # past it and load everything at once.
    max_child_error = max(c["geometricError"] for c in children)

    root = {
        "asset": {"version": "1.1"},
        "geometricError": max_child_error * 4,
        "root": {
            "refine": "ADD",
            "geometricError": max_child_error * 2,
            "boundingVolume": {"region": region},
            "children": children,
        },
    }

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(root, fh, separators=(",", ":"))

    west, south, east, north, min_h, max_h = region
    print(f"Wrote {out_path}")
    print(f"  {len(children)} child tilesets ({repaired} bounding volumes repaired)")
    print(
        f"  bounds: {math.degrees(south):.5f},{math.degrees(west):.5f} .. "
        f"{math.degrees(north):.5f},{math.degrees(east):.5f}"
    )
    print(f"  height: {min_h:.1f} .. {max_h:.1f} m")
    return 0


def region_from_geometry(tileset_path: str, node: dict) -> list[float] | None:
    """
    Derive a tile's true bounds from the extent of its own geometry.

    Reads the POSITION accessors' min/max out of the sibling .glb — those are
    required by the glTF spec and survive Draco compression, so this works on
    compressed tiles too — walks the local AABB's eight corners out through the
    Y-up→Z-up conversion and the tile transform to ECEF, and converts back to
    geodetic.
    """
    glb_path = tileset_path.replace(".tileset.json", ".glb")
    if not os.path.isfile(glb_path):
        return None

    try:
        gltf = read_gltf_json(glb_path)
    except (OSError, ValueError, struct.error):
        return None

    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    for mesh in gltf.get("meshes", []):
        for prim in mesh.get("primitives", []):
            idx = prim.get("attributes", {}).get("POSITION")
            if idx is None:
                continue
            acc = gltf["accessors"][idx]
            if "min" not in acc or "max" not in acc:
                continue
            for i in range(3):
                lo[i] = min(lo[i], acc["min"][i])
                hi[i] = max(hi[i], acc["max"][i])

    if any(math.isinf(v) for v in lo + hi):
        return None

    transform = node.get("transform")
    full = multiply(transform, YUP_TO_ZUP) if transform else YUP_TO_ZUP

    west = south = math.inf
    east = north = -math.inf
    min_h, max_h = math.inf, -math.inf
    for cx in (lo[0], hi[0]):
        for cy in (lo[1], hi[1]):
            for cz in (lo[2], hi[2]):
                ex, ey, ez = apply_point(full, (cx, cy, cz))
                lat, lon, h = ecef_to_geodetic(ex, ey, ez)
                west = min(west, lon)
                east = max(east, lon)
                south = min(south, lat)
                north = max(north, lat)
                min_h = min(min_h, h)
                max_h = max(max_h, h)

    return [west, south, east, north, min_h, max_h]


def read_gltf_json(path: str) -> dict:
    """Parse the JSON chunk of a .glb, or the whole file if it is plain glTF."""
    with open(path, "rb") as fh:
        head = fh.read(20)
        if head[:4] != b"glTF":
            fh.seek(0)
            return json.load(fh)
        length = struct.unpack("<I", head[12:16])[0]
        return json.loads(fh.read(length))


def multiply(a, b):
    """Column-major 4x4 multiply, matching the glTF/3D Tiles convention."""
    out = [0.0] * 16
    for col in range(4):
        for row in range(4):
            out[col * 4 + row] = sum(a[k * 4 + row] * b[col * 4 + k] for k in range(4))
    return out


def apply_point(m, p):
    x, y, z = p
    return (
        m[0] * x + m[4] * y + m[8] * z + m[12],
        m[1] * x + m[5] * y + m[9] * z + m[13],
        m[2] * x + m[6] * y + m[10] * z + m[14],
    )


def ecef_to_geodetic(x: float, y: float, z: float) -> tuple[float, float, float]:
    """ECEF metres to (latitude, longitude, height) in radians/metres, via Bowring."""
    lon = math.atan2(y, x)
    p = math.hypot(x, y)
    if p == 0.0:
        return (math.copysign(math.pi / 2, z), lon, abs(z) - WGS84_B)
    theta = math.atan2(z * WGS84_A, p * WGS84_B)
    lat = math.atan2(
        z + WGS84_EP2 * WGS84_B * math.sin(theta) ** 3,
        p - WGS84_E2 * WGS84_A * math.cos(theta) ** 3,
    )
    n = WGS84_A / math.sqrt(1.0 - WGS84_E2 * math.sin(lat) ** 2)
    height = p / math.cos(lat) - n
    return (lat, lon, height)


def region_from_path(rel: str, fallback: list[float] | None) -> list[float] | None:
    """
    Derive a tile's true geographic bounds from its `.../<z>/<x>/<y>.tileset.json`
    path, keeping the height range from the file's own (correct) region.

    Falls back to the file's region if the path doesn't carry z/x/y — better a
    too-large bound than none.
    """
    parts = rel.split("/")
    try:
        z = int(parts[-3])
        x = int(parts[-2])
        y = int(parts[-1].split(".")[0])
    except (IndexError, ValueError):
        return fallback

    min_h, max_h = (fallback[4], fallback[5]) if fallback else (0.0, 200.0)
    n = 2.0**z
    return [
        math.radians(x / n * 360.0 - 180.0),  # west
        lat_at(y + 1, n),  # south
        math.radians((x + 1) / n * 360.0 - 180.0),  # east
        lat_at(y, n),  # north
        min_h,
        max_h,
    ]


def lat_at(y: float, n: float) -> float:
    """Latitude (radians) of an XYZ tile row edge in Web Mercator."""
    return math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / n)))


def union_regions(regions: list[list[float]]) -> list[float] | None:
    """Union of 3D Tiles `region` bounds: [west, south, east, north, minH, maxH] in radians."""
    if not regions:
        return None
    return [
        min(r[0] for r in regions),
        min(r[1] for r in regions),
        max(r[2] for r in regions),
        max(r[3] for r in regions),
        min(r[4] for r in regions),
        max(r[5] for r in regions),
    ]


def fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())

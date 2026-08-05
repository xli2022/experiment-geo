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
import sys


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
            tile_region = region_from_path(rel, region)
            if tile_region is None:
                print(f"  skipping {rel}: no usable bounding region", file=sys.stderr)
                continue

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
    print(f"  {len(children)} child tilesets")
    print(
        f"  bounds: {math.degrees(south):.5f},{math.degrees(west):.5f} .. "
        f"{math.degrees(north):.5f},{math.degrees(east):.5f}"
    )
    print(f"  height: {min_h:.1f} .. {max_h:.1f} m")
    return 0


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

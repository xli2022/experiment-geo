#!/usr/bin/env python3
"""
Download a terrain height field from Japan's GSI elevation tiles.

Why this exists
---------------
PLATEAU publishes buildings at their true elevation but ships no relief for
Shinjuku — there is no `dem` package in its catalogue at all. Under a flat
sheet the city cannot sit right: measured, 43% of building meshes floated more
than 10 m above it, and a 25 m float displaces its shadow by 32 m, which is
what makes shadows read as detached from the buildings casting them.

Deriving the surface from the buildings themselves was tried twice and does not
work. The premise — the lowest geometry in a patch of city is the ground there
— holds at tile scale and fails at cell scale: most 45 m cells never contain a
building's base, so their lowest vertex is a roof, and no rejection threshold
separates the two because the real ground ends up looking like the outlier.
Making cells large enough to reliably catch a base makes them too coarse to
follow terrain, which is the entire point.

So this fetches actual survey data. GSI serves elevation as XYZ tiles of plain
CSV — 256x256 values per tile, metres above sea level, `e` where there is no
data — which needs no raster library and no reprojection.

    dem5a  5 m mesh, z15, photogrammetric. Best where it exists, urban Japan.
    dem    10 m mesh, z14, national coverage. The fallback.

Licensing: GSI tiles are open under the 国土地理院コンテンツ利用規約, which
follows the Government of Japan Standard Terms (CC BY 4.0 compatible) and
requires the source to be credited — "出典：国土地理院". Anything shipped from
this has to carry that, alongside PLATEAU's own credit.

Usage:
    tools/fetch-dem.py public/tiles/tokyo-shinjuku/terrain.json \\
        --lat 35.6898 --lon 139.6960 --radius 2400
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.error
import urllib.request

TILE = 256
UA = {"User-Agent": "experiment-geo/0.1 (terrain fetch)"}


def tile_xy(lat: float, lon: float, z: int) -> tuple[int, int]:
    n = 2**z
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n)
    return x, y


def tile_bounds(x: int, y: int, z: int) -> tuple[float, float, float, float]:
    """(west, south, east, north) in degrees."""
    n = 2**z

    def lat_at(row: float) -> float:
        return math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * row / n))))

    return (x / n * 360.0 - 180.0, lat_at(y + 1), (x + 1) / n * 360.0 - 180.0, lat_at(y))


def fetch_tile(source: str, z: int, x: int, y: int) -> list[list[float]] | None:
    url = f"https://cyberjapandata.gsi.go.jp/xyz/{source}/{z}/{x}/{y}.txt"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as fh:
            text = fh.read().decode()
    except urllib.error.HTTPError as exc:
        # 404 is normal and expected: GSI's finer meshes are not national, so a
        # tile simply not existing is how coverage ends.
        if exc.code == 404:
            return None
        raise
    rows = []
    for line in text.strip().splitlines():
        # "e" marks no data — sea, or outside the survey.
        rows.append([math.nan if v == "e" else float(v) for v in line.split(",")])
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("Why this")[0].strip())
    ap.add_argument("out", help="path to write the height field JSON")
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--radius", type=float, default=2400.0, help="metres")
    ap.add_argument("--zoom", type=int, default=15)
    ap.add_argument("--source", default="dem5a", help="dem5a (5 m) or dem (10 m)")
    ap.add_argument(
        "--resolution",
        type=int,
        default=192,
        help="samples per side in the output. The surface follows terrain, not "
        "buildings, so this is far below the source resolution on purpose.",
    )
    args = ap.parse_args()

    dlat = args.radius / 111_320.0
    dlon = args.radius / (111_320.0 * math.cos(math.radians(args.lat)))
    south, north = args.lat - dlat, args.lat + dlat
    west, east = args.lon - dlon, args.lon + dlon

    x0, y1 = tile_xy(south, west, args.zoom)
    x1, y0 = tile_xy(north, east, args.zoom)
    tiles: dict[tuple[int, int], list[list[float]]] = {}
    missing = 0
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            grid = fetch_tile(args.source, args.zoom, x, y)
            if grid is None:
                missing += 1
            else:
                tiles[(x, y)] = grid
    print(f"{len(tiles)} tiles fetched, {missing} absent ({args.source} z{args.zoom})")
    if not tiles:
        print("error: no elevation tiles covered this area", file=sys.stderr)
        return 1

    def sample(lat: float, lon: float) -> float:
        x, y = tile_xy(lat, lon, args.zoom)
        grid = tiles.get((x, y))
        if grid is None:
            return math.nan
        w, s, e, n = tile_bounds(x, y, args.zoom)
        col = min(TILE - 1, max(0, int((lon - w) / (e - w) * TILE)))
        # Rows run north to south, opposite to latitude.
        row = min(TILE - 1, max(0, int((n - lat) / (n - s) * TILE)))
        return grid[row][col]

    res = args.resolution
    heights: list[float] = []
    known = 0
    for j in range(res):
        lat = north - (north - south) * (j / (res - 1))
        for i in range(res):
            lon = west + (east - west) * (i / (res - 1))
            h = sample(lat, lon)
            if not math.isnan(h):
                known += 1
            heights.append(h)

    # Fill no-data from neighbours so the consumer never has to. Sea shows up
    # as `e`, and a hole in the surface is worse than a flat patch of it.
    valid = [h for h in heights if not math.isnan(h)]
    if not valid:
        print("error: every sample was no-data", file=sys.stderr)
        return 1
    fallback = sorted(valid)[len(valid) // 2]
    for _pass in range(res):
        holes = [i for i, h in enumerate(heights) if math.isnan(h)]
        if not holes:
            break
        for i in holes:
            r, c = divmod(i, res)
            near = [
                heights[(r + dr) * res + (c + dc)]
                for dr in (-1, 0, 1)
                for dc in (-1, 0, 1)
                if 0 <= r + dr < res and 0 <= c + dc < res
                and not math.isnan(heights[(r + dr) * res + (c + dc)])
            ]
            if near:
                heights[i] = sum(near) / len(near)
    heights = [fallback if math.isnan(h) else round(h, 2) for h in heights]

    doc = {
        "attribution": "国土地理院 (GSI Japan)",
        "source": f"{args.source} z{args.zoom}",
        # Geographic bounds of the grid. Row 0 is the north edge.
        "north": north,
        "south": south,
        "west": west,
        "east": east,
        "resolution": res,
        # Metres above sea level, row-major from the north-west corner.
        "heights": heights,
    }
    with open(args.out, "w") as fh:
        json.dump(doc, fh, separators=(",", ":"))
    print(
        f"{res}x{res} samples ({known} from survey, {res * res - known} filled) "
        f"-> {args.out}, elevation {min(heights):.1f}..{max(heights):.1f} m"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

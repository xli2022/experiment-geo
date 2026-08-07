#!/usr/bin/env python3
"""
Download a terrain height field from PLATEAU's own DEM.

Why this rather than tools/fetch-dem.py
---------------------------------------
Both produce the same height field, and GSI's is easier to fetch. This one is
the authoritative surface for a PLATEAU city, for one reason: it is the terrain
the buildings were placed against. Same programme, same aerial survey, same
epoch, same vertical datum. Any disagreement left between a building's base and
this ground is a real feature of the city — a basement, a sunken plaza, a
railway cutting — rather than a difference between two surveys.

It is not published as 3D Tiles. Nothing in PLATEAU is: the catalogue has no
`dem` dataset for any prefecture, which is why an earlier version of this repo
concluded there was no relief for Shinjuku at all. It ships inside the city's
CityGML package, whose `feature_types` do list `dem`.

That package is 772 MB for Shinjuku and the DEM is one 43 MB member of it, so
this reads the zip over HTTP range requests: the end-of-central-directory
record, then the central directory, then just the bytes of `udx/dem/*.gml`,
inflated as it streams. Nothing else is downloaded.

Resolution is the other reason to prefer it. The TIN's triangles are about
1.8 m across. GSI's 5 m mesh sampled onto a 25 m output grid — nearest
neighbour, one point per cell — cannot represent a railway cutting or an
embankment, and Shinjuku is full of both.

Vertical datum: the filename says it. `533945_dem_6697_op.gml` is EPSG:6697,
JGD2011 with heights above the geoid, so this needs the same geoid correction
GSI's data does. PLATEAU's building tilesets are already ellipsoidal because
their converter applies it; the CityGML underneath them is not.

Licensing: PDL 1.0, same as the rest of PLATEAU, and it requires the source
named and derived data marked as modified.

Usage:
    tools/fetch-plateau-dem.py public/tiles/tokyo-shinjuku/terrain.json \\
        --city-code 13104 --lat 35.6898 --lon 139.6960 --radius 2400
"""

from __future__ import annotations

import argparse
import json
import math
import re
import struct
import sys
import urllib.error
import urllib.request
import zlib

from importlib.machinery import SourceFileLoader
from pathlib import Path

# Reuse the geoid lookup rather than restating it — the correction has to be
# identical whichever DEM a city ends up using, or switching source would
# silently move the ground.
_fetch_dem = SourceFileLoader("_fetch_dem", str(Path(__file__).with_name("fetch-dem.py"))).load_module()
geoid_height = _fetch_dem.geoid_height

CATALOGUE = "https://api.plateauview.mlit.go.jp/datacatalog/plateau-datasets"
UA = {"User-Agent": "experiment-geo/0.1 (plateau dem)"}
POSLIST = re.compile(r"<gml:posList>([^<]+)</gml:posList>")
FEATURE_NAME = re.compile(r"<gml:name>(\d{8})</gml:name>")


def get(url: str, byte_range: tuple[int, int] | None = None) -> bytes:
    headers = dict(UA)
    if byte_range:
        headers["Range"] = f"bytes={byte_range[0]}-{byte_range[1]}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=300) as fh:
        return fh.read()


def mesh_bounds(code: str) -> tuple[float, float, float, float]:
    """(south, west, north, east) of a JIS X 0410 third-level mesh code.

    Each ReliefFeature carries one, which is what makes it cheap to skip the
    ~90% of a 10 km sheet that a city-sized field does not cover: decode the
    code, and only parse the features whose square overlaps.
    """
    lat = int(code[0:2]) * 2 / 3
    lon = int(code[2:4]) + 100.0
    lat += int(code[4]) / 12.0
    lon += int(code[5]) / 8.0
    lat += int(code[6]) / 120.0
    lon += int(code[7]) / 80.0
    return lat, lon, lat + 1 / 120.0, lon + 1 / 80.0


def find_dem_member(zip_url: str) -> tuple[int, int, str]:
    """(data offset, compressed size, name) of the DEM inside the CityGML zip."""
    size = _content_length(zip_url)
    tail = get(zip_url, (size - 65536, size - 1))
    i = tail.rfind(b"PK\x05\x06")
    if i < 0:
        raise SystemExit("error: no end-of-central-directory record; zip64?")
    _, cd_size, cd_off = struct.unpack("<HII", tail[i + 10 : i + 20])

    cd = get(zip_url, (cd_off, cd_off + cd_size - 1))
    p = 0
    while p < len(cd) and cd[p : p + 4] == b"PK\x01\x02":
        method = struct.unpack("<H", cd[p + 10 : p + 12])[0]
        csize, _usize, nlen, elen, clen = struct.unpack("<IIHHH", cd[p + 20 : p + 34])
        lho = struct.unpack("<I", cd[p + 42 : p + 46])[0]
        name = cd[p + 46 : p + 46 + nlen].decode("utf-8", "replace")
        if "/dem/" in name and name.endswith(".gml"):
            if method != 8:
                raise SystemExit(f"error: {name} uses compression method {method}, expected deflate")
            # The central directory's extra field and the local header's differ,
            # so the data offset has to come from the local header itself.
            hdr = get(zip_url, (lho, lho + 64))
            n2, e2 = struct.unpack("<HH", hdr[26:30])
            return lho + 30 + n2 + e2, csize, name
        p += 46 + nlen + elen + clen
    raise SystemExit("error: no udx/dem/*.gml member in the CityGML package")


def _content_length(url: str) -> int:
    req = urllib.request.Request(url, headers=UA, method="HEAD")
    with urllib.request.urlopen(req, timeout=120) as fh:
        return int(fh.headers["Content-Length"])


def zip_url_for(city_code: str) -> str:
    cat = json.loads(get(CATALOGUE))
    for row in cat.get("citygml", []):
        if row.get("city_code") == city_code:
            print(f"{row['city']} {row['year']} CityGML, spec {row.get('spec')}, "
                  f"{row['file_size'] / 1e6:.0f} MB")
            if "dem" not in row.get("feature_types", []):
                raise SystemExit(f"error: {city_code} publishes no dem package")
            return row["url"]
    raise SystemExit(f"error: no CityGML package for city code {city_code}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("Why this")[0].strip())
    ap.add_argument("out", help="path to write the height field JSON")
    ap.add_argument("--city-code", required=True, help="e.g. 13104 for Shinjuku-ku")
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--radius", type=float, default=2400.0, help="metres")
    ap.add_argument(
        "--resolution",
        type=int,
        default=384,
        help="samples per side. The source TIN is ~1.8 m, so this is the real "
        "limit on how much relief survives — 384 over a 4.8 km field is 12.5 m.",
    )
    ap.add_argument("--zip-url", help="skip catalogue lookup and use this package")
    ap.add_argument("--no-geoid", action="store_true", help="leave heights orthometric")
    args = ap.parse_args()

    dlat = args.radius / 111_320.0
    dlon = args.radius / (111_320.0 * math.cos(math.radians(args.lat)))
    south, north = args.lat - dlat, args.lat + dlat
    west, east = args.lon - dlon, args.lon + dlon

    zip_url = args.zip_url or zip_url_for(args.city_code)
    start, csize, name = find_dem_member(zip_url)
    print(f"{name}: {csize / 1e6:.1f} MB compressed at offset {start}")

    res = args.resolution
    # Sum and count per cell rather than nearest-neighbour sampling. The TIN is
    # finer than the grid, so a cell holds many vertices, and averaging them is
    # what keeps a 12.5 m cell from landing arbitrarily on the floor or the rim
    # of a cutting the way one sampled point does.
    total = [0.0] * (res * res)
    count = [0] * (res * res)

    req = urllib.request.Request(zip_url, headers={**UA, "Range": f"bytes={start}-{start + csize - 1}"})
    dec = zlib.decompressobj(-zlib.MAX_WBITS)
    tail = ""
    read = 0
    kept_features = skipped_features = 0
    vertices = 0

    with urllib.request.urlopen(req, timeout=600) as fh:
        while True:
            raw = fh.read(4 << 20)
            if not raw:
                break
            read += len(raw)
            text = tail + dec.decompress(raw).decode("utf-8", "replace")
            # Work whole features at a time so a posList is never split across
            # chunks, and so a feature's mesh code is always in hand before its
            # geometry is parsed.
            parts = text.split("<dem:ReliefFeature")
            tail = parts.pop()
            for part in parts:
                m = FEATURE_NAME.search(part)
                if m:
                    s, w, n, e = mesh_bounds(m.group(1))
                    if n < south or s > north or e < west or w > east:
                        skipped_features += 1
                        continue
                    kept_features += 1
                for hit in POSLIST.finditer(part):
                    v = hit.group(1).split()
                    for k in range(0, len(v) - 2, 3):
                        lat, lon, h = float(v[k]), float(v[k + 1]), float(v[k + 2])
                        if not (south <= lat <= north and west <= lon <= east):
                            continue
                        col = min(res - 1, int((lon - west) / (east - west) * res))
                        row = min(res - 1, int((north - lat) / (north - south) * res))
                        total[row * res + col] += h
                        count[row * res + col] += 1
                        vertices += 1
            print(f"\r  {read / 1e6:5.1f}/{csize / 1e6:.1f} MB  "
                  f"{kept_features} features kept, {vertices} vertices", end="", file=sys.stderr)
    print(file=sys.stderr)
    print(f"{kept_features} features in range, {skipped_features} skipped, {vertices} vertices binned")

    heights = [total[i] / count[i] if count[i] else math.nan for i in range(res * res)]
    known = sum(1 for h in heights if not math.isnan(h))
    if not known:
        print("error: no DEM vertices fell inside the requested area", file=sys.stderr)
        return 1

    # Fill from neighbours. A TIN leaves gaps wherever a triangle is larger than
    # a cell, which over flat ground is common and harmless.
    valid = [h for h in heights if not math.isnan(h)]
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
    heights = [fallback if math.isnan(h) else h for h in heights]

    geoid = None if args.no_geoid else geoid_height(args.lat, args.lon)
    if geoid is not None:
        heights = [h + geoid for h in heights]
        print(f"geoid undulation {geoid:.2f} m applied — heights are ellipsoidal")
    heights = [round(h, 2) for h in heights]

    # Report the bounds of the sample *points*, not of the binned area.
    #
    # Each height is the mean of the vertices in its bin, so it describes the
    # bin's centre, while the consumer lays heights[0] on the north-west corner
    # of whatever bounds it is given. Handing it the outer edge would shift the
    # whole surface half a cell north-west — 6.25 m at this resolution, which on
    # Shinjuku's grades is a couple of decimetres of height error for nothing.
    half_lat = (north - south) / res / 2
    half_lon = (east - west) / res / 2

    doc = {
        "attribution": "Project PLATEAU, MLIT Japan",
        "source": f"{name.rsplit('/', 1)[-1]} (CityGML dem, LOD1 TIN)",
        "datum": "ellipsoidal" if geoid is not None else "orthometric",
        "geoidUndulation": geoid,
        "north": north - half_lat,
        "south": south + half_lat,
        "west": west + half_lon,
        "east": east - half_lon,
        "resolution": res,
        "heights": heights,
    }
    with open(args.out, "w") as fh:
        json.dump(doc, fh, separators=(",", ":"))
    print(
        f"{res}x{res} samples ({known} from the TIN, {res * res - known} filled) "
        f"-> {args.out}, {doc['datum']} height {min(heights):.1f}..{max(heights):.1f} m"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

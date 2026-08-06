#!/usr/bin/env python3
"""Score a city's OSM data for how well it will bake.

OSM2World can only render what is mapped, and the tags that decide whether a
bake looks like Berlin's are few and countable. This asks Overpass for them over
a fixed-area box at each city's centre, so the numbers compare directly rather
than reflecting how large a box someone happened to pick.

What matters, in order:

  height / building:levels  Without one, OSM2World gives the building a default
                            box height and the skyline goes flat. This is the
                            single biggest lever and the usual reason a city
                            bakes badly.
  roof:shape                What stops every roof being a flat lid. Berlin's
                            coverage is what gives its bake its silhouette.
  building:part             Detail on large or stepped buildings — towers with
                            setbacks, wings at different heights.

Usage:
    tools/probe-city.py                 # the built-in candidate list
    tools/probe-city.py "Lyon:45.76,4.84"
"""
from __future__ import annotations

import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

# Roughly the area of the shipped Berlin bake: 4.45 x 4.47 km.
HALF_SIDE_KM = 2.23
ENDPOINT = "https://overpass-api.de/api/interpreter"

# name -> (lat, lon) of a central point worth flying over.
CANDIDATES = {
    "Berlin (baseline)": (52.5170, 13.3890),
    "Munich": (48.1372, 11.5755),
    "Hamburg": (53.5503, 9.9920),
    "Vienna": (48.2082, 16.3719),
    "Prague": (50.0875, 14.4213),
    "Zurich": (47.3769, 8.5417),
    "Amsterdam": (52.3730, 4.8920),
    "Copenhagen": (55.6761, 12.5683),
    "Helsinki": (60.1699, 24.9384),
    "Manhattan": (40.7580, -73.9855),
    "San Francisco": (37.7880, -122.4070),
    "London": (51.5100, -0.1300),
    "Paris": (48.8600, 2.3400),
    "Barcelona": (41.3870, 2.1700),
}

QUERY = """
[out:json][timeout:300];
(way["building"]({bbox}); relation["building"]({bbox}););
out count;
(way["building"]["height"]({bbox}); relation["building"]["height"]({bbox});
 way["building"]["building:levels"]({bbox}); relation["building"]["building:levels"]({bbox}););
out count;
(way["building"]["roof:shape"]({bbox}); relation["building"]["roof:shape"]({bbox}););
out count;
(way["building:part"]({bbox}); relation["building:part"]({bbox}););
out count;
"""


def bbox_for(lat: float, lon: float) -> str:
    dlat = HALF_SIDE_KM / 111.32
    dlon = HALF_SIDE_KM / (111.32 * math.cos(math.radians(lat)))
    return f"{lat - dlat:.5f},{lon - dlon:.5f},{lat + dlat:.5f},{lon + dlon:.5f}"


def probe(lat: float, lon: float) -> list[int] | None:
    query = QUERY.format(bbox=bbox_for(lat, lon))
    data = urllib.parse.urlencode({"data": query}).encode()
    # Overpass answers 406 to urllib's default User-Agent. It is a shared free
    # service and asks callers to identify themselves, which is fair.
    request = urllib.request.Request(
        ENDPOINT, data=data, headers={"User-Agent": "experiment-geo/0.1 (city bake survey)"}
    )
    # Overpass sheds load with 429 and 504 under normal conditions, so a single
    # attempt reports "no data" for cities that have plenty. Back off and retry.
    payload = None
    for attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=320) as fh:
                payload = json.load(fh)
            break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            code = getattr(exc, "code", None)
            if attempt == 4 or code not in (429, 504, None):
                print(f"    query failed: {type(exc).__name__}: {exc}", file=sys.stderr)
                return None
            time.sleep(15 * 2**attempt)
    if payload is None:
        return None
    counts = []
    for element in payload.get("elements", []):
        if element.get("type") == "count":
            tags = element.get("tags", {})
            counts.append(int(tags.get("total", 0)))
    return counts if len(counts) == 4 else None


def main() -> int:
    if len(sys.argv) > 1:
        cities = {}
        for arg in sys.argv[1:]:
            name, coords = arg.split(":")
            lat, lon = coords.split(",")
            cities[name] = (float(lat), float(lon))
    else:
        cities = CANDIDATES

    area = (2 * HALF_SIDE_KM) ** 2
    print(f"{2 * HALF_SIDE_KM:.1f} x {2 * HALF_SIDE_KM:.1f} km box ({area:.0f} km²) at each centre\n")
    print(f"{'city':<20} {'buildings':>10} {'with height':>13} {'roof:shape':>13} {'parts':>8}")
    print("-" * 68)

    rows = []
    for name, (lat, lon) in cities.items():
        counts = probe(lat, lon)
        if counts is None:
            print(f"{name:<20} {'query failed':>10}")
            continue
        total, heighted, roofed, parts = counts
        pct = lambda n: f"{100 * n / total:.1f}%" if total else "-"
        print(
            f"{name:<20} {total:>10,} {heighted:>7,} {pct(heighted):>5} "
            f"{roofed:>7,} {pct(roofed):>5} {parts:>8,}"
        )
        rows.append((name, total, heighted, roofed, parts))
        # Overpass is a shared free service; do not hammer it.
        time.sleep(4)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

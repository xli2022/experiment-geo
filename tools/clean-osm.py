#!/usr/bin/env python3
"""
Strip OSM tag values that OSM2World cannot map, so a bake doesn't abort.

Why this exists
---------------
OSM2World resolves `surface=*` through `DefaultMaterials.getSurfaceMaterial()`.
For a value it doesn't recognise that returns null, and the caller dereferences
it immediately:

    NullPointerException: Cannot invoke "Material.get(O2WConfig)" because the
    return value of "DefaultMaterials.getSurfaceMaterial(String, O2WConfig)"
    is null

The failure is not scoped to the offending way — it propagates out of the
ForkJoin pool and takes down *every* tile in the run. One mistagged footpath
anywhere in the extract yields zero output.

Berlin Mitte trips this reliably. Measured in a 13.38-13.40E / 52.51-52.53N
extract: 2038 `asphalt` and 1738 `paving_stones` alongside `concrete:plates`,
`tactile_paving` (a mistag — that is normally its own key), `artificial_turf`,
`metal`, and the multi-value `sett;paving_stones`, which is invalid OSM.

Rather than enumerate what breaks — the tail is long and varies by city — this
keeps a conservative allowlist of values known to bake and drops the rest. A
dropped `surface` falls back to OSM2World's default for the feature, which is
the correct-looking surface for almost every road and path.

Usage:
    tools/clean-osm.py in.osm.pbf -o out.osm.pbf
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile

# Values verified to bake. Deliberately conservative: a surface that renders as
# the feature default is invisible next to a bake that produces nothing.
SURFACE_ALLOWLIST = {
    "asphalt",
    "cobblestone",
    "compacted",
    "concrete",
    "dirt",
    "earth",
    "fine_gravel",
    "grass",
    "gravel",
    "ground",
    "paved",
    "paving_stones",
    "pebblestone",
    "sand",
    "sett",
    "unhewn_cobblestone",
    "unpaved",
    "wood",
}

# Colour tags OSM2World honours via `useBuildingColors`. Keeping them is what
# gives the handful of well-mapped buildings their real colour, but a value at
# either extreme reads as a defect rather than a building: with no texture and
# no specular response, a `building:colour=#000000` is a black hole in the
# skyline and pure white is a blown-out silhouette. Measured in Berlin Mitte:
# 4 buildings tagged #000000, against ~50 tagged plausible tones.
#
# Dropping the tag falls back to the palette, which is the desired look anyway.
COLOUR_KEYS = ("building:colour", "building:color", "roof:colour", "roof:color")
MIN_LUMINANCE = 0.10
MAX_LUMINANCE = 0.97

NAMED_COLOURS = {
    "black": 0.0,
    "white": 1.0,
    "grey": 0.5,
    "gray": 0.5,
    "silver": 0.75,
    "red": 0.30,
    "green": 0.35,
    "blue": 0.15,
    "yellow": 0.90,
    "brown": 0.30,
    "orange": 0.55,
    "beige": 0.90,
    "cream": 0.93,
}

TAG_RE = re.compile(r'<tag k="surface" v="([^"]*)"\s*/>')
COLOUR_TAG_RE = re.compile(r'<tag k="([^"]*(?:colour|color))" v="([^"]*)"\s*/>')


def luminance(value: str) -> float | None:
    """Rough perceptual luminance of an OSM colour value, or None if unparsable."""
    v = value.strip().lower()
    if v in NAMED_COLOURS:
        return NAMED_COLOURS[v]
    if not v.startswith("#"):
        return None
    h = v[1:]
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        r, g, b = (int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
    except ValueError:
        return None
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("Usage")[0].strip())
    ap.add_argument("input", help="input .osm.pbf")
    ap.add_argument("-o", "--output", required=True, help="output .osm.pbf")
    args = ap.parse_args()

    if not os.path.isfile(args.input):
        print(f"error: no such file: {args.input}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        raw = os.path.join(tmp, "raw.osm")
        clean = os.path.join(tmp, "clean.osm")

        run(["osmium", "cat", args.input, "-f", "osm", "-o", raw, "--overwrite"])

        dropped: dict[str, int] = {}
        dropped_colours: dict[str, int] = {}
        kept = 0
        kept_colours = 0
        with open(raw, encoding="utf-8") as src, open(clean, "w", encoding="utf-8") as dst:
            for line in src:
                match = TAG_RE.search(line)
                if match:
                    value = match.group(1)
                    if value not in SURFACE_ALLOWLIST:
                        dropped[value] = dropped.get(value, 0) + 1
                        # Drop the whole line — it is a standalone <tag/> element.
                        continue
                    kept += 1
                    dst.write(line)
                    continue

                colour = COLOUR_TAG_RE.search(line)
                if colour and colour.group(1) in COLOUR_KEYS:
                    value = colour.group(2)
                    lum = luminance(value)
                    if lum is not None and not (MIN_LUMINANCE <= lum <= MAX_LUMINANCE):
                        key = f"{colour.group(1)}={value}"
                        dropped_colours[key] = dropped_colours.get(key, 0) + 1
                        continue
                    kept_colours += 1

                dst.write(line)

        run(["osmium", "cat", clean, "-f", "pbf", "-o", args.output, "--overwrite"])

    print(f"Wrote {args.output}")
    print(f"  surface tags: {kept} kept, {sum(dropped.values())} dropped")
    for value, count in sorted(dropped.items(), key=lambda kv: -kv[1]):
        print(f"    {count:5d}  {value}")
    print(f"  colour tags:  {kept_colours} kept, {sum(dropped_colours.values())} dropped")
    for value, count in sorted(dropped_colours.items(), key=lambda kv: -kv[1]):
        print(f"    {count:5d}  {value}")
    return 0


def run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"error: {' '.join(cmd[:2])} failed:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    sys.exit(main())

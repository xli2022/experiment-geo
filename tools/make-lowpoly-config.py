#!/usr/bin/env python3
"""
Generate an OSM2World config that bakes a flat low-poly palette.

Why this exists
---------------
OSM2World assigns every surface one of ~83 *named* materials (ASPHALT, BRICK,
ROOF_DEFAULT, GRASS, WATER, ...) by feature type, independently of OSM tags.
That naming is the only reliable surface classifier available to us:

  - OSM's own `building:material` / `building:colour` tags cover ~0.4% of
    buildings, so they classify nothing.
  - Sampling Berlin's photogrammetric orthophoto classifies nothing either —
    measured median saturation 0.077, with 24% of pixels blue *shadow* rather
    than blue material.

So the palette is keyed on OSM2World's material names. This script reads the
stock `standard.properties` for the material list and writes a self-contained
config carrying one flat colour per material and no texture references at all.

Doing it in config rather than post-processing the output matters: OSM2World
embeds its 4K PBR texture sets into *every* tile, so a stock bake is ~98 MB per
tile that we would then throw away. Leaving the textures out up front skips
that entirely — measured 47 MB per tile instead, before compression.

Usage:
    tools/make-lowpoly-config.py vendor/osm2world/standard.properties \
        -o vendor/osm2world/lowpoly.properties
"""

from __future__ import annotations

import argparse
import os
import re
import sys

# --- the palette -----------------------------------------------------------
#
# Muted and slightly desaturated rather than primary: a city is mostly roof and
# road, and saturated defaults (OSM2World ships ROOF_DEFAULT as #cc0000, pillar
# box red) turn a skyline into a fairground at any distance.
#
# Anything not listed falls back to NEUTRAL, so an OSM2World update that adds
# materials degrades to a grey surface rather than an untextured black one.

NEUTRAL = "#b8b3a8"

PALETTE = {
    # Buildings — walls light and warm so the darker roofs read as the accent.
    "BUILDING_DEFAULT": "#e6dfd1",
    "BUILDING_WINDOWS": "#cfd6da",
    "SINGLE_WINDOW": "#cfd6da",
    "GLASS": "#aac6d6",
    "GLASS_ROOF": "#aac6d6",
    "GLASS_ROOF_TRANSPARENT": "#aac6d6",
    "GLASS_TRANSPARENT": "#aac6d6",
    "GLASS_WALL": "#b3ccda",
    "GLASS_WALL_TRANSPARENT": "#b3ccda",
    "ENTRANCE_DEFAULT": "#8d7a63",
    "GARAGE_DOOR": "#c2bcb2",
    "PLASTIC": "#e4e4e2",
    # Roofs — the main source of colour variety in a low-poly skyline.
    "ROOF_DEFAULT": "#c4694a",
    "TILES": "#bd6f52",
    "THATCH_ROOF": "#c0a068",
    "COPPER_ROOF": "#79ad97",
    "SLATE": "#6d7278",
    "SOLAR_PANEL": "#3f4a5c",
    "CORRUGATED_STEEL": "#a6abb0",
    "STEEL": "#a2a8ae",
    # Masonry.
    "BRICK": "#b06f5a",
    "ADOBE": "#c9a482",
    "CONCRETE": "#c7c2b8",
    "MARBLE": "#e8e6e0",
    "SANDSTONE": "#d8c9a4",
    "STONE": "#b5b0a6",
    "ROCK": "#a49e94",
    "WALL_GABION": "#a8a49b",
    "WOOD": "#9b7c55",
    "WOOD_WALL": "#a08560",
    "WOODCHIPS": "#a98d63",
    # Ground surfaces. Roads sit darker than pavement so the street grid reads
    # from altitude — that legibility is most of the low-poly look.
    "ASPHALT": "#94948f",
    "PAVING_STONE": "#c0bab0",
    "SETT": "#b4aea4",
    "UNHEWN_COBBLESTONE": "#aca69c",
    "PEBBLESTONE": "#b7b1a7",
    "GRAVEL": "#b2aca2",
    "SCREE": "#aaa49a",
    "KERB": "#c8c3ba",
    "EARTH": "#a68f6d",
    "SAND": "#dcc9a0",
    "SHELLS": "#ddd3c0",
    "GRASS_PAVER": "#a8b58c",
    "CARPET": "#9a8f86",
    "TARTAN": "#b5705c",
    "MANHOLE_COVER": "#8a8a86",
    # Vegetation and terrain.
    "GRASS": "#8fb573",
    "SCRUB": "#7fa365",
    "HEDGE": "#6f9457",
    "TERRAIN_DEFAULT": "#a9bd8d",
    "TREE_BILLBOARD_BROAD_LEAVED": "#6f9a57",
    "TREE_BILLBOARD_BROAD_LEAVED_FRUIT": "#77a25c",
    "TREE_BILLBOARD_CONIFEROUS": "#5c8a52",
    # Water, snow, ice.
    "WATER": "#7fa8c4",
    "ICE": "#dbe8ef",
    "SNOW": "#f0f4f7",
    # Rail.
    "RAILWAY": "#8a8580",
    "RAIL_BALLAST": "#9c968c",
    # Markings — kept bright, they are thin and read as detail rather than area.
    "ROAD_MARKING": "#f2f2ee",
    "ROAD_MARKING_ARROW_RIGHT": "#f2f2ee",
    "ROAD_MARKING_ARROW_RIGHT_LEFT": "#f2f2ee",
    "ROAD_MARKING_ARROW_THROUGH": "#f2f2ee",
    "ROAD_MARKING_ARROW_THROUGH_RIGHT": "#f2f2ee",
    "ROAD_MARKING_CROSSING": "#f2f2ee",
    "ROAD_MARKING_DASHED": "#f2f2ee",
    "ROAD_MARKING_ZEBRA": "#f2f2ee",
    "RED_ROAD_MARKING": "#d98a7a",
    "RUNWAY_CENTER_MARKING": "#f2f2ee",
    "HELIPAD_MARKING": "#f2f2ee",
    # Sport pitches.
    "PITCH_BEACHVOLLEYBALL": "#dcc9a0",
    "PITCH_SOCCER": "#7fa860",
    "PITCH_TENNIS_ASPHALT": "#7f95a8",
    "PITCH_TENNIS_CLAY": "#c07a5c",
    "PITCH_TENNIS_GRASS": "#7fa860",
    "PITCH_TENNIS_SINGLES_ASPHALT": "#7f95a8",
    "PITCH_TENNIS_SINGLES_CLAY": "#c07a5c",
    "PITCH_TENNIS_SINGLES_GRASS": "#7fa860",
    "TENNIS_NET": "#d8d5cf",
    # Street furniture and other small stuff.
    "CHAIN_LINK_FENCE": "#a8adb2",
    "FLAGCLOTH": "#d8d5cf",
    "ADVERTISING_POSTER": "#c9c4bb",
    "POWER_TOWER_HORIZONTAL": "#9aa0a6",
    "POWER_TOWER_VERTICAL": "#9aa0a6",
}

# Global settings, written verbatim. This config is deliberately *self-contained*
# rather than `include`-ing standard.properties.
#
# Blanking the inherited texture keys does not work: an empty value still
# registers the texture layer, and OSM2World then dies with
# `ArrayIndexOutOfBoundsException: Index 0 out of bounds for length 0` building
# a layer that has no images. The keys have to be absent, not empty — which
# means not inheriting them in the first place.
#
# Dropping the include also drops `./locales/<locale>-defaults.properties`,
# whose only content is textured traffic signs. No loss: signs are
# sub-metre detail that a low-poly city has no use for.
GLOBALS = {
    # Billboard trees are a transparent PNG on a quad. With textures blanked
    # they collapse to opaque coloured rectangles standing in the street, which
    # is the single worst artifact of this whole approach. The 3D models are
    # low-poly to begin with and are what the style wants anyway.
    "useBillboards": "false",
    # Keep OSM's own building colours where they exist. Coverage is tiny
    # (~0.4%) so this changes little, but where a mapper bothered the building
    # is usually a landmark and worth honouring.
    "useBuildingColors": "true",
    # Terrain fills the gaps between mapped features; without it the ground is
    # a void and buildings appear to float.
    "createTerrain": "true",
    "locale": "DE",
    "backgroundColor": "#000000",
    "exportAlpha": "false",
    "treesPerSquareMeter": "0.02",
    "renderUnderground": "false",
}

# Per-material flags worth carrying over from standard.properties. Everything
# else there is texture plumbing.
#
# `transparency` is deliberately *not* carried: it is alpha-tested against a
# texture that no longer exists, so BINARY on a chain-link fence yields either
# an error or an invisible surface. Without it fences render as solid panels,
# which is what a low-poly city does with them anyway.
KEEP_FLAGS = ("doubleSided",)

MATERIAL_NAME = re.compile(r"^material_([A-Z_0-9]+)_", re.MULTILINE)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("Usage")[0].strip())
    ap.add_argument("standard", help="path to OSM2World's standard.properties")
    ap.add_argument("-o", "--output", required=True, help="where to write the generated config")
    args = ap.parse_args()

    try:
        with open(args.standard, encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    materials = sorted(set(MATERIAL_NAME.findall(text)))
    missing = [m for m in materials if m not in PALETTE]

    # Carried-over per-material flags, e.g. material_FLAGCLOTH_doubleSided.
    flags = []
    for line in text.splitlines():
        key = line.split("=")[0].strip()
        if key.startswith("material_") and key.rsplit("_", 1)[-1] in KEEP_FLAGS:
            flags.append(line.strip())

    lines = [
        "# Generated by tools/make-lowpoly-config.py — do not edit by hand.",
        "#",
        "# Flat low-poly palette: no textures at all, one colour per material.",
        "# Self-contained on purpose — see the generator for why it does not",
        "# include standard.properties.",
        "",
    ]

    lines.append("# --- global ---")
    for key, value in GLOBALS.items():
        lines.append(f"{key} = {value}")
    lines.append("")

    if flags:
        lines.append("# --- carried-over material flags ---")
        lines.extend(sorted(set(flags)))
        lines.append("")

    lines.append("# --- palette ---")
    for name in materials:
        colour = PALETTE.get(name, NEUTRAL)
        suffix = "" if name in PALETTE else "   # fallback"
        lines.append(f"material_{name}_color = {colour}{suffix}")
    lines.append("")

    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))

    print(f"Wrote {args.output}")
    print(f"  {len(materials)} materials, {len(flags)} flags carried, no textures")
    if missing:
        print(f"  {len(missing)} without a palette entry, using {NEUTRAL}: {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

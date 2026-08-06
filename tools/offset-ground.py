#!/usr/bin/env python3
"""
Separate coincident surfaces vertically so they stop fighting for the same depth.

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

Only near-horizontal triangles near the ground are touched by the layer table,
so a wall sharing a material with a pavement is left alone. A second table
lifts whole objects — currently tree crowns, whose undersides land exactly on
the trunk cap on every tree in the city.

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
# Every material gets its own level; sharing one only moves the fight rather
# than ending it.
#
# The step floor is set by the compressor, not by taste. Draco quantizes
# positions onto a uniform grid sized by the mesh's largest extent, so on a
# 750 m tile every axis — including height — shares one step: 45.8 mm at Draco's
# default 14 bits, 11.4 mm at 16. Both are coarser than the 4 mm sub-steps here,
# so the offsets were applied correctly and then silently undone at compression
# time, and the surfaces came back out exactly coplanar. tools/optimize-tiles.sh
# asks for 20 bits, which measures 0.72 mm — the sub-steps clear it fivefold.
# Anything finer than about 2 mm does not survive, wherever it is written.
LAYERS = {
    "#a9bd8d": -0.120,  # TERRAIN_DEFAULT — the sheet everything else sits on
    "#8fb573": -0.104,  # GRASS
    "#7fa365": -0.096,  # SCRUB
    "#6f9457": -0.088,  # HEDGE
    "#a68f6d": -0.080,  # EARTH
    "#dcc9a0": -0.072,  # SAND
    "#ddd3c0": -0.064,  # SHELLS
    "#9c968c": -0.056,  # RAIL_BALLAST
    "#8a8580": -0.048,  # RAILWAY
    "#b2aca2": -0.040,  # GRAVEL
    "#aaa49a": -0.032,  # SCREE
    "#b7b1a7": -0.024,  # PEBBLESTONE
    # WATER, just under the carriageway.
    #
    # OSM2World draws a water body's top face at exactly y = 0, the same height
    # as the roads and plazas along its banks — 68,176 m² of water against
    # 31,100 m² of asphalt in one z15 tile, sharing a plane wherever a quay or a
    # bridge approach overlaps the riverbank polygon. That is the largest single
    # source of coincident surfaces left in the world once compression stops
    # erasing the rest: 4,579 conflicting pairs in that one tile.
    #
    # Down rather than up, because a quay stands above the river it runs beside,
    # so asphalt winning the overlap is also the correct picture. Water still
    # sits 110 mm clear of the terrain sheet, so a river reads as water and not
    # as ground.
    "#7fa8c4": -0.010,
    # ASPHALT is the reference the rest are placed around, so its offset is
    # zero — but it has to be listed rather than left implicit, or the unlisted
    # -material fallback below claims it and lifts the carriageway above its own
    # road markings.
    "#94948f": 0.0,  # ASPHALT
    "#f2f2ee": 0.036,  # ROAD_MARKING and friends, painted onto the carriageway
    "#d98a7a": 0.052,  # RED_ROAD_MARKING — cycle lanes, well clear of white paint
    "#b4aea4": 0.076,  # SETT — pavements, genuinely above the road
    "#aca69c": 0.086,  # UNHEWN_COBBLESTONE
    "#c0bab0": 0.096,  # PAVING_STONE
    "#c7c2b8": 0.106,  # CONCRETE
    "#c8c3ba": 0.120,  # KERB — the top of the stack, as on a real street
}

# Whole objects nudged rigidly, at whatever height they sit, rather than layers
# of ground. Every vertex of the matching colour moves, so the shape is
# unchanged — it is only lifted clear of whatever it was tied with.
#
# Trees are the reason this exists, and they were the single largest source of
# coplanar surfaces left in the world once the ground was sorted out: measured
# 3,032 conflicting pairs of TREE_CROWN against TREE_TRUNK in one tile, more
# than half of everything remaining. OSM2World builds a tree as a trunk with a
# crown on top, and the crown's underside lands exactly on the trunk's cap, so
# every tree in the city carries a coincident pair. There are thousands of them.
RIGID = {
    "#6f9a57": 0.04,  # TREE_CROWN — lifted clear of the trunk it sits on
    # Posts and masts sit on top of the thing they are mounted to, so their
    # caps land in its surface. Steep-face displacement cannot reach those —
    # a cap is horizontal — so the whole object lifts instead.
    "#9aa0a6": 0.025,  # METAL_FENCE_POST / POWER_TOWER_*
}

# Horizontal surfaces *above* the ground, i.e. roof coverings sitting on the
# roof structure they clad.
#
# This is the case the other three tables all miss: LAYERS only looks near
# y = 0, NORMAL_OFFSETS only moves steep faces, and the per-component nudge in
# vary-buildings.py only knows about ROOF_DEFAULT and BUILDING_DEFAULT. A
# copper dome, a glazed courtyard roof or a run of solar panels is none of
# those, so nothing separated it from the roof underneath and the two tiled
# against each other across the whole surface — the dense stipple over a large
# area, rather than the thin lines that coplanar *edges* produce.
#
# Displacement is along each face's own normal rather than straight up, so a
# curved or pitched covering is handled the same as a flat one. A dome pushed
# out along its normals is simply a slightly larger dome; pushed straight up it
# would separate at the top and stay welded at the sides, which is where a
# horizontal-only rule failed.
#
# Values are distinct per material for the usual reason: sharing a level moves
# the fight rather than ending it.
RAISED = {
    "#a2a8ae": 0.030,  # STEEL
    "#6d7278": 0.040,  # SLATE
    "#bd6f52": 0.050,  # TILES
    "#79ad97": 0.060,  # COPPER_ROOF — the Bode's dome, among others
    "#c0a068": 0.070,  # THATCH_ROOF
    "#e8e6e0": 0.080,  # MARBLE
    "#d8c9a4": 0.090,  # SANDSTONE
    "#b5b0a6": 0.100,  # STONE
    "#b06f5a": 0.110,  # BRICK
    "#aac6d6": 0.120,  # GLASS_ROOF / GLASS — glazing over a courtyard
    "#b3ccda": 0.130,  # GLASS_WALL, where it caps something
    "#3f4a5c": 0.145,  # SOLAR_PANEL — genuinely mounted above the roof
}

# Surfaces pushed out along their own normal rather than upward, because they
# are set flush into whatever they belong to and a vertical face cannot be
# separated by a change in height.
#
# This is what is left once the ground and the trees are sorted out: measured
# on a shipped tile, 490 genuine same-facing coplanar pairs, of which garage
# doors against concrete (177), fences against gravel (118), and entrances and
# wooden doors against walls (28) are the bulk. OSM2World models a door as a
# panel exactly in the plane of the wall it opens through, so the two fight for
# every pixel of the door.
#
# Pushing out along the normal also reads correctly: a door or a fence panel
# standing a couple of centimetres proud of its wall is what the real thing
# does. Values stay small enough that the corner gaps opened by moving faces
# independently are far below a pixel at any altitude you fly at.
# Every value here also has to clear the *wall* nudge in vary-buildings.py, not
# merely the neighbouring entries. A door carries its own colour, so it never
# receives the nudge, while the wall it opens through is BUILDING_DEFAULT and
# does — outward, by up to the nudge ceiling. Any door offset below that ceiling
# is overtaken by its own wall and disappears into it, which is why these all
# start above where the nudge stops rather than at the couple of centimetres a
# real door stands proud.
NORMAL_OFFSETS = {
    "#cfd6da": 0.040,  # BUILDING_WINDOWS / SINGLE_WINDOW
    "#aac6d6": 0.040,  # GLASS
    "#b3ccda": 0.040,  # GLASS_WALL
    "#9b7c55": 0.048,  # WOOD
    "#a08560": 0.048,  # WOOD_WALL
    "#c2bcb2": 0.056,  # GARAGE_DOOR — set into a concrete wall
    "#8d7a63": 0.056,  # ENTRANCE_DEFAULT — doors, same story
    "#a8adb2": 0.064,  # CHAIN_LINK_FENCE / METAL_FENCE / HANDRAIL_DEFAULT
    "#c9c4bb": 0.064,  # ADVERTISING_POSTER / BUS_STOP_SIGN — mounted on things
    "#d8d5cf": 0.064,  # FLAGCLOTH / TENNIS_NET
    "#9aa0a6": 0.072,  # METAL_FENCE_POST / POWER_TOWER_* — posts on their panel
}

# A triangle counts as ground if it is near-horizontal and near y = 0. Both
# tests matter: the first keeps walls that share a material (concrete, brick)
# out of it, the second keeps roofs and bridge decks out.
NORMAL_UP = 0.9
GROUND_BAND = 1.5  # metres either side of y = 0

MATCH_EPS = 2.0e-3

# Every table above is an enumeration, and OSM2World does not draw from a closed
# set. With `useBuildingColors` on it writes whatever `building:colour` and
# `roof:colour` say, so a roof arrives in whatever colour a mapper typed. Those
# roofs match no table here, and they also match nothing in vary-buildings,
# which keys on the default roof colour — so they were the only surfaces in the
# world still receiving no offset at all. Measured on one z15 tile, they were
# 195 of the 212 conflicts left once compression stopped erasing the rest: an
# OSM-coloured roof at exactly 7.5 m against a varied neighbour whose nudge
# happened to be zero.
#
# Hashing the colour gives every one of them a level without anyone enumerating
# it. The step is never zero: an unlisted surface must end up clear of anything
# that was not moved at all.
#
# It displaces *into* the surface, against its own normal, and that direction is
# the whole design. Pushed the other way, a roof's horizontal face lifts off the
# walls it sits on and leaves a slot you can see the sky through — the first
# version did exactly that, at +44 mm on the default terracotta before the
# per-component nudge in vary-buildings.py added up to 48 mm more, and a 9 cm
# gap along every eaves line is a far worse artifact than the z-fighting it was
# there to fix. Displacing inward sinks the face into the solid it bounds
# instead, where the wall in front of it occludes the offset entirely.
#
# Like STEEP_FALLBACK below, this range has to sit *clear of* the per-component
# nudge in vary-buildings.py rather than merely differ from it. Both push a roof
# face the same way — down, into the building — so sharing one 2 mm lattice over
# an overlapping range means an OSM-coloured roof whose level equals a varied
# neighbour's nudge is exactly coplanar with it. That is not a rare collision:
# it was 5,700 of the 16,676 conflicts left across the world, and the largest
# families in the scan were all a roof variant against an OSM colour. Starting
# above where the nudge ends makes it arithmetically impossible.
#
# The step is 8 mm, and the reason it is not 1 mm is the whole point of this
# block. Every lattice here was originally sized against the Draco grid — 1 mm
# "clears 0.72 mm", so the surfaces are not coplanar and the job looked done.
# They are not coplanar in the file and they still tile on screen, because the
# grid is not what decides it: the depth buffer, float32 in the vertex shader
# and the grazing angle a roof is seen at all erode the gap further.
#
# Measured, rather than reasoned about, with test/_sepsweep.mjs — it lifts one
# colour's vertices in the live scene and counts pixels that flip under a 1 cm
# camera jitter, which is what fighting actually is. Over the Humboldt Forum at
# 150 m: 0.5 mm, 1 mm, 2 mm and 4 mm were all indistinguishable from no offset
# at all (~2,140 flipping pixels); 8 mm was the first that moved the number
# (1,834), 16 mm halved it (852), 32 mm reached the floor set by other pairs
# still fighting (337). Anything under about 8 mm is not a smaller fix, it is
# no fix.
#
# Fewer, wider levels is the trade that buys this. Two colours landing on the
# same level are exactly coplanar and always fight, and that is now 1 in 8
# rather than 1 in 16 — but every pair that does *not* collide is now genuinely
# separated, where before all of them tiled anyway.
#
# The nudge takes 8-32 mm, so this takes the 40-96 mm above it. Unlike the steep
# case there is no corner wedge to pay for — a recess in a roof is hidden by the
# wall in front of it however deep it goes.
FALLBACK_BASE = 0.032
FALLBACK_STEPS = 8
FALLBACK_STEP = 0.008

# The same idea for unlisted materials *inside* the ground band, which LAYERS
# would own if it knew about them — a wooden boardwalk over stone paving, and
# anything else surfaced with a material nobody thought to enumerate.
#
# These sit just above the carriageway, in the gap LAYERS leaves between ASPHALT
# at 0 and the road markings at 30 mm — a surface that is not a road, resting on
# the road surface. Every level clears its nearest LAYERS neighbour by at least
# 6 mm, so borrowing the gap cannot create the ties this is here to remove.
#
# The first version used the much wider gap higher up, between the markings and
# the pavements, and so lifted these surfaces 50-82 mm — chosen purely to avoid
# colliding with other levels, without asking how large the lift needed to be,
# and 8 cm at the edge of a large plaza is a visible lip.
#
# The gap this borrows is only 36 mm tall, so it buys three levels at the 8 mm
# the screen needs rather than nine at a 2 mm nobody can see. Three is few, but
# the alternative is nine levels that all tile against each other.
GROUND_FALLBACK_BASE = 0.000
GROUND_FALLBACK_STEP = 0.008
GROUND_FALLBACK_STEPS = 3

# And once more for vertical faces, which is where the rest of it turned out to
# be — measured across the world after the two passes above, 96% of every
# conflict left was steep, and nearly all of it facades carrying a
# `building:colour` a mapper typed.
#
# This range has to sit *clear of* the per-component nudge in vary-buildings.py,
# not merely differ from it. The first version used the same 2 mm lattice over an
# overlapping range, and a varied wall whose component nudge happened to equal an
# OSM-coloured neighbour's level came out exactly coplanar anyway — 650 of the
# 688 conflicts left on a test tile were one such pair. Starting above where the
# nudge ends makes that arithmetically impossible rather than unlikely.
#
# The ceiling is the corner wedge. A wall pushed along its own normal separates
# from the wall it meets at an outside corner, since the two faces travel in
# different directions, and the notch that opens is as wide as the push.
#
# That budget and the 8 mm the screen needs cannot both be had at sixteen
# levels, so this takes six: 80-128 mm, above the fence posts at 72 mm. A 128 mm
# notch is about five pixels at the twenty metres you would have to be at to
# look into a building's corner, against a facade that tiled over its whole area
# at any distance before. The notch is worth it; it is also static, and a
# flicker is what the eye actually catches.
STEEP_FALLBACK_BASE = 0.072
STEEP_FALLBACK_STEPS = 6
STEEP_FALLBACK_STEP = 0.008

# The default roof and wall colours, which vary-buildings.py owns.
#
# Excluding them here costs nothing and keeps the corner wedge inside today's
# budget. Every surface carrying one of these shares a single colour, so a hash
# would give them all the *same* level — which cannot separate them from each
# other, only add to how far they travel. What does separate them is the
# per-component nudge, and an unlisted neighbour still moves relative to them
# whether they move or not.
VARIED_BASES = ("#c4694a", "#e6dfd1")

# Why this is done in the geometry at all, rather than at render time.
#
# Moving surfaces in metres has an obvious flaw: the displacement has to be big
# enough to survive depth precision at viewing range and small enough not to
# reorder surfaces that genuinely sit centimetres apart, and on this scene those
# two wants do not overlap. A depth bias in the fragment shader looks like the
# way out — it is expressed in depth-buffer units, so it is worth the same
# number of bits at any distance, and it moves nothing, so it cannot reorder
# anything.
#
# It was tried, and it is worse. Hashing the vertex colour to a depth offset
# took flips under a 1 cm camera jitter from 1,145 to 2,888 at 2e-5 and 4,819 at
# 1e-4 — monotonically worse the harder it was applied.
#
# The reason is that a shader cannot tell which surfaces are coplanar. It biases
# every one of them, including all the pairs the depth buffer already gets right
# — a wall against the roof it carries, a building against the ground it stands
# on — so it buys separation where surfaces coincide and spends it everywhere
# else. What makes the tables below work is not the size of the numbers, it is
# that something measured which triangles actually share a plane before moving
# any of them. That selectivity is the whole value, and it is not available at
# render time.
#
# The floor every table above is built on, measured rather than assumed.
#
# test/_sepsweep.mjs lifts one colour's vertices in the running scene and counts
# the pixels that flip when the camera moves a centimetre, which is what
# z-fighting is. Below 8 mm the count does not move: 1 mm and 4 mm separations
# tile exactly as much as no separation at all. The tables were previously
# spaced 1-5 mm, on the reasoning that clearing the 0.72 mm Draco grid was
# enough — it is a necessary floor, not a sufficient one, because the depth
# buffer and the vertex shader's float32 erode the gap after compression is done
# with it.
MIN_SEPARATION = 0.008

# vary-buildings.py nudges default-coloured components along their own normal,
# and stops at NUDGE_STEP * NUDGE_STEPS. Everything here that moves the same way
# has to clear that ceiling, so it is duplicated rather than imported — the two
# tools do not share a module, and a silent drift between them is exactly the
# collision this whole scheme exists to prevent.
NUDGE_CEILING = 0.008 * 4


def _check_separations() -> None:
    """Fail loudly if any two levels that could coincide are too close.

    Every offset here is hand-placed against physical intent — a kerb stands
    above a pavement, a door proud of its wall — so the ordering cannot be
    generated. What can be checked is that no two of them landed within the
    distance the screen can actually resolve, which is how the 4 mm ground
    clusters and the 1 mm lattices went unnoticed for so long.
    """
    groups = {
        # Ground layers plus the fallback levels that borrow the gap above
        # asphalt; all are horizontal and near y = 0, so any pair can coincide.
        "ground": sorted(
            set(LAYERS.values())
            | {GROUND_FALLBACK_BASE + (i + 1) * GROUND_FALLBACK_STEP for i in range(GROUND_FALLBACK_STEPS)}
        ),
        # Steep faces: the wall nudge, the detail set into walls, and the
        # unlisted-colour fallback all push outward along the same normal.
        "steep": sorted(
            {(i + 1) * (NUDGE_CEILING / 4) for i in range(4)}
            | set(NORMAL_OFFSETS.values())
            | {STEEP_FALLBACK_BASE + (i + 1) * STEEP_FALLBACK_STEP for i in range(STEEP_FALLBACK_STEPS)}
        ),
        # Roof coverings, lifted clear of the structure they clad.
        "raised": sorted(set(RAISED.values())),
        # Roofs recessed into the building: the nudge, then the fallback above
        # it. Both travel inward, so they share one axis.
        "roof": sorted(
            {(i + 1) * (NUDGE_CEILING / 4) for i in range(4)}
            | {FALLBACK_BASE + (i + 1) * FALLBACK_STEP for i in range(FALLBACK_STEPS)}
        ),
    }
    for name, levels in groups.items():
        for lo, hi in zip(levels, levels[1:]):
            if hi - lo < MIN_SEPARATION - 1e-9:
                raise SystemExit(
                    f"offset-ground: {name} levels {lo * 1000:.0f} mm and {hi * 1000:.0f} mm are "
                    f"{(hi - lo) * 1000:.1f} mm apart, below the {MIN_SEPARATION * 1000:.0f} mm "
                    "the renderer resolves — they will tile against each other"
                )


_check_separations()


def srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_to_linear(value: str) -> tuple[float, float, float]:
    v = value.lstrip("#")
    return tuple(srgb_to_linear(int(v[i : i + 2], 16) / 255.0) for i in (0, 2, 4))


def stable_hash(value: int) -> int:
    """FNV-1a. Python's `hash` is salted per process, so a roof would move
    between bakes; this keeps a colour on the same level forever."""
    h = 0x811C9DC5
    for _ in range(8):
        h = ((h ^ (value & 0xFF)) * 0x01000193) & 0xFFFFFFFF
        value >>= 8
    return h


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("Usage")[0].strip())
    ap.add_argument("tiles_dir")
    args = ap.parse_args()

    paths = sorted(glob.glob(os.path.join(args.tiles_dir, "**", "*.glb"), recursive=True))
    if not paths:
        print(f"error: no .glb under {args.tiles_dir}", file=sys.stderr)
        return 1

    layers = [(np.array(hex_to_linear(h)), dy) for h, dy in LAYERS.items()]
    rigid = [(np.array(hex_to_linear(h)), dy) for h, dy in RIGID.items()]
    along = [(np.array(hex_to_linear(h)), d) for h, d in NORMAL_OFFSETS.items()]
    raised = [(np.array(hex_to_linear(h)), d) for h, d in RAISED.items()]
    varied = [np.array(hex_to_linear(h)) for h in VARIED_BASES]

    total = 0
    tiles = 0
    for path in paths:
        moved = process(path, layers, rigid, along, raised, varied)
        if moved is None:
            continue
        total += moved
        tiles += 1
        if moved:
            print(f"  {os.path.relpath(path, args.tiles_dir)}: {moved:,} vertices")

    print(f"Offset {total:,} vertices across {tiles} tiles")
    return 0


def process(path: str, layers, rigid, along, raised, varied) -> int | None:
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
            # Both height gates are decided here, before anything moves. Read
            # later they would see positions the earlier passes had already
            # shifted, so a surface lifted across the 1.5 m line would be picked
            # up a second time by the above-ground passes and moved twice.
            high = positions[:, 1] > GROUND_BAND

            for base, dy in layers:
                matched = np.all(np.abs(colors - base) < MATCH_EPS, axis=1)
                sel = candidates[np.all(matched[candidates], axis=1)]
                if not len(sel):
                    continue
                verts = np.unique(sel)
                positions[verts, 1] += dy
                moved += len(verts)

            # Ground-level materials no LAYERS entry covers, on their own level.
            if len(candidates):
                ground_spare = np.ones(len(colors), dtype=bool)
                for base, _ in layers:
                    ground_spare &= ~np.all(np.abs(colors - base) < MATCH_EPS, axis=1)
                for base, _ in rigid:
                    ground_spare &= ~np.all(np.abs(colors - base) < MATCH_EPS, axis=1)
                sel = candidates[np.all(ground_spare[candidates], axis=1)]
                if len(sel):
                    where = np.unique(sel)
                    q = np.round(colors[where] * 4095).astype(np.int64)
                    codes = (q[:, 0] << 24) | (q[:, 1] << 12) | q[:, 2]
                    for code in np.unique(codes):
                        verts = where[codes == code]
                        step = (stable_hash(int(code)) % GROUND_FALLBACK_STEPS) + 1
                        positions[verts, 1] += GROUND_FALLBACK_BASE + step * GROUND_FALLBACK_STEP
                        moved += len(verts)

            # Rigid objects ignore the ground gate entirely — a tree crown is
            # neither horizontal nor near y = 0, and it has to move as a whole
            # or it would be deformed rather than lifted.
            for base, dy in rigid:
                matched = np.all(np.abs(colors - base) < MATCH_EPS, axis=1)
                if not matched.any():
                    continue
                positions[matched, 1] += dy
                moved += int(matched.sum())

            # Push out along the surface's own normal. A door set into a wall
            # is vertical, so no amount of raising or lowering separates it
            # from the wall — it has to come forward instead.
            #
            # Restricted to steep faces on purpose. Applied to a horizontal
            # surface this becomes a vertical move that lands wherever the
            # arithmetic puts it, and the first version did exactly that: a
            # fence surface sitting at +0.064 plus a 0.030 push arrived at
            # +0.094, which is precisely PAVING_STONE's level, turning 118
            # conflicts into 293. Horizontal surfaces belong to LAYERS, which
            # assigns levels deliberately rather than by accident.
            # Roof coverings, at any orientation, as long as they are clear of
            # the ground band that LAYERS owns. Gating on height rather than on
            # facing is what lets this reach a dome: its faces point in every
            # direction, so a horizontal-only test misses most of it and a
            # steep-only test misses the rest.
            for base, dist in raised:
                matched = np.all(np.abs(colors - base) < MATCH_EPS, axis=1) & high
                if not matched.any():
                    continue
                positions[matched] += normals[matched] * dist
                moved += int(matched.sum())

            steep = np.abs(normals[:, 1]) < 0.7
            for base, dist in along:
                matched = np.all(np.abs(colors - base) < MATCH_EPS, axis=1) & steep
                if not matched.any():
                    continue
                positions[matched] += normals[matched] * dist
                moved += int(matched.sum())

            # Everything else horizontal and above the ground band.
            #
            # Horizontal only, deliberately: an unlisted *vertical* surface is a
            # wall, and pushing walls out here would stack with the per-component
            # nudge in vary-buildings and start opening gaps between terraced
            # neighbours. The conflicts this is for are decks and roofs.
            spare = (np.abs(normals[:, 1]) > NORMAL_UP) & high
            for base, _ in raised:
                spare &= ~np.all(np.abs(colors - base) < MATCH_EPS, axis=1)
            for base, _ in rigid:
                spare &= ~np.all(np.abs(colors - base) < MATCH_EPS, axis=1)
            for base in varied:
                spare &= ~np.all(np.abs(colors - base) < MATCH_EPS, axis=1)
            if spare.any():
                where = np.nonzero(spare)[0]
                # Quantise before hashing, so float noise between bakes cannot
                # move a colour to a different level and shift a roof.
                q = np.round(colors[where] * 4095).astype(np.int64)
                codes = (q[:, 0] << 24) | (q[:, 1] << 12) | q[:, 2]
                for code in np.unique(codes):
                    verts = where[codes == code]
                    step = (stable_hash(int(code)) % FALLBACK_STEPS) + 1
                    dist = FALLBACK_BASE + step * FALLBACK_STEP
                    # Minus: into the solid the face bounds, never off it.
                    positions[verts] -= normals[verts] * dist
                    moved += len(verts)

            # Steep faces no table covers, at any height. NORMAL_OFFSETS already
            # gives doors, fences and glass their own push, and RIGID objects
            # have moved as a whole, so both are left alone here.
            wall_spare = steep.copy()
            for base, _ in along:
                wall_spare &= ~np.all(np.abs(colors - base) < MATCH_EPS, axis=1)
            for base, _ in rigid:
                wall_spare &= ~np.all(np.abs(colors - base) < MATCH_EPS, axis=1)
            for base in varied:
                wall_spare &= ~np.all(np.abs(colors - base) < MATCH_EPS, axis=1)
            if wall_spare.any():
                where = np.nonzero(wall_spare)[0]
                q = np.round(colors[where] * 4095).astype(np.int64)
                codes = (q[:, 0] << 24) | (q[:, 1] << 12) | q[:, 2]
                for code in np.unique(codes):
                    verts = where[codes == code]
                    step = (stable_hash(int(code)) % STEEP_FALLBACK_STEPS) + 1
                    dist = STEEP_FALLBACK_BASE + step * STEEP_FALLBACK_STEP
                    positions[verts] += normals[verts] * dist
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

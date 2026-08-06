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
    "#8fb573": -0.090,  # GRASS
    "#7fa365": -0.086,  # SCRUB
    "#6f9457": -0.082,  # HEDGE
    "#a68f6d": -0.078,  # EARTH
    "#dcc9a0": -0.074,  # SAND
    "#ddd3c0": -0.070,  # SHELLS
    "#9c968c": -0.060,  # RAIL_BALLAST
    "#8a8580": -0.056,  # RAILWAY
    "#b2aca2": -0.052,  # GRAVEL
    "#aaa49a": -0.048,  # SCREE
    "#b7b1a7": -0.044,  # PEBBLESTONE
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
    "#f2f2ee": 0.030,  # ROAD_MARKING and friends, painted onto the carriageway
    "#d98a7a": 0.045,  # RED_ROAD_MARKING — cycle lanes, well clear of white paint
    "#b4aea4": 0.086,  # SETT — pavements, genuinely above the road
    "#aca69c": 0.090,  # UNHEWN_COBBLESTONE
    "#c0bab0": 0.094,  # PAVING_STONE
    "#c7c2b8": 0.098,  # CONCRETE
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
    "#6d7278": 0.034,  # SLATE
    "#bd6f52": 0.038,  # TILES
    "#79ad97": 0.042,  # COPPER_ROOF — the Bode's dome, among others
    "#c0a068": 0.046,  # THATCH_ROOF
    "#e8e6e0": 0.050,  # MARBLE
    "#d8c9a4": 0.054,  # SANDSTONE
    "#b5b0a6": 0.058,  # STONE
    "#b06f5a": 0.062,  # BRICK
    "#aac6d6": 0.066,  # GLASS_ROOF / GLASS — glazing over a courtyard
    "#b3ccda": 0.070,  # GLASS_WALL, where it caps something
    "#3f4a5c": 0.078,  # SOLAR_PANEL — genuinely mounted above the roof
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
NORMAL_OFFSETS = {
    "#c2bcb2": 0.025,  # GARAGE_DOOR — set into a concrete wall
    "#8d7a63": 0.025,  # ENTRANCE_DEFAULT — doors, same story
    "#9b7c55": 0.020,  # WOOD
    "#a08560": 0.020,  # WOOD_WALL
    "#a8adb2": 0.030,  # CHAIN_LINK_FENCE / METAL_FENCE / HANDRAIL_DEFAULT
    "#9aa0a6": 0.035,  # METAL_FENCE_POST / POWER_TOWER_* — posts on their panel
    "#cfd6da": 0.015,  # BUILDING_WINDOWS / SINGLE_WINDOW
    "#aac6d6": 0.015,  # GLASS
    "#b3ccda": 0.015,  # GLASS_WALL
    "#c9c4bb": 0.030,  # ADVERTISING_POSTER / BUS_STOP_SIGN — mounted on things
    "#d8d5cf": 0.030,  # FLAGCLOTH / TENNIS_NET
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
# Twelve steps of 2 mm, so the deepest recess is 24 mm. 2 mm still clears the
# 0.72 mm Draco grid nearly threefold, which is all the separation a depth
# buffer needs; the old 4 mm bought nothing and cost twice the visible depth.
FALLBACK_STEPS = 12
FALLBACK_STEP = 0.002

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
# the pavements, and so lifted these surfaces 50-82 mm. That was chosen purely
# to avoid colliding with other levels, without asking how large the lift needed
# to be: it only has to clear the 0.72 mm Draco grid, and 8 cm at the edge of a
# large plaza is a visible lip.
GROUND_FALLBACK_BASE = 0.004
GROUND_FALLBACK_STEP = 0.002
GROUND_FALLBACK_STEPS = 9

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
# different directions; the nudge already does this at up to 48 mm and is
# invisible at street level, so 48 mm total is the budget. vary-buildings takes
# 2-32 mm of it and this takes the 33-48 mm above.
STEEP_FALLBACK_BASE = 0.032
STEEP_FALLBACK_STEPS = 16
STEEP_FALLBACK_STEP = 0.001

# The default roof and wall colours, which vary-buildings.py owns.
#
# Excluding them here costs nothing and keeps the corner wedge inside today's
# budget. Every surface carrying one of these shares a single colour, so a hash
# would give them all the *same* level — which cannot separate them from each
# other, only add to how far they travel. What does separate them is the
# per-component nudge, and an unlisted neighbour still moves relative to them
# whether they move or not.
VARIED_BASES = ("#c4694a", "#e6dfd1")


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
                    dist = ((stable_hash(int(code)) % FALLBACK_STEPS) + 1) * FALLBACK_STEP
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

#!/usr/bin/env python3
"""
Project an orthophoto onto OSM2World geometry — clean shapes, real colours.

Why this exists
---------------
The two available sources each have half of what a stylised city needs:

  OSM2World      clean readable geometry — boxes with crisp edges and defined
                 roof planes — but no idea what colour anything is
  photogrammetry real colour, on melted blobby geometry that no amount of
                 texture work makes read as game art

This takes the colour from one and puts it on the other. Every vertex gets a UV
derived from its horizontal world position, so looking down at the result shows
the real Berlin colours — orange roofs orange, parks green, the Spree blue —
draped over geometry that still has hard edges.

Walls are the known compromise. A top-down projection has nothing to say about
a vertical surface, so it smears whatever is directly above down the facade.
`--wall-shade` darkens steep faces to read as shadowed sides rather than
stretched roof, which at flying altitude is convincing and up close is not.

The ortho is referenced as an *external* image rather than embedded, so all
tiles share one download instead of carrying a copy each.

Usage:
    tools/project-ortho.py <tiles-dir> <ortho.png> <ortho.json> [--wall-shade 0.72]
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import struct
import sys

WGS84_A = 6378137.0
WGS84_E2 = 6.69437999014e-3
GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942

COMPONENT = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2), 5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
NUM_COMPONENTS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("Usage")[0].strip())
    ap.add_argument("tiles_dir")
    ap.add_argument("ortho_png")
    ap.add_argument("ortho_json")
    ap.add_argument(
        "--wall-shade",
        type=float,
        default=0.72,
        help="multiplier applied to near-vertical faces, 1 = off",
    )
    args = ap.parse_args()

    with open(args.ortho_json, encoding="utf-8") as fh:
        meta = json.load(fh)
    bounds = meta["bounds"]

    # The ortho was captured in the renderer's local frame, centred on the
    # anchor. The geometry is georeferenced in ECEF, so it has to come back into
    # that same frame before the UVs mean anything.
    anchor = meta.get("anchor")
    if anchor is None:
        print(
            "error: ortho.json has no anchor — rerun capture-ortho.mjs, which records it",
            file=sys.stderr,
        )
        return 1
    to_local = enu_inverse(anchor["lat"], anchor["lon"], anchor.get("height", 0.0))

    ortho_name = os.path.basename(args.ortho_png)
    shutil.copy(args.ortho_png, os.path.join(args.tiles_dir, ortho_name))

    transforms = collect_transforms(args.tiles_dir)
    files = sorted(
        os.path.join(r, f)
        for r, _, fs in os.walk(args.tiles_dir)
        for f in fs
        if f.endswith((".b3dm", ".glb"))
    )
    if not files:
        print(f"error: no tiles under {args.tiles_dir}", file=sys.stderr)
        return 1

    done = skipped = 0
    for path in files:
        rel = os.path.relpath(path, args.tiles_dir).replace(os.sep, "/")
        xform = transforms.get(rel)
        depth = rel.count("/")
        uri = "../" * depth + ortho_name
        try:
            if project_file(path, bounds, to_local, xform, uri, args.wall_shade):
                done += 1
            else:
                skipped += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  failed {rel}: {exc}", file=sys.stderr)
            skipped += 1
        if (done + skipped) % 10 == 0:
            print(f"\r  {done + skipped}/{len(files)}", end="", flush=True)

    print(f"\nProjected {done} tiles ({skipped} skipped) using {ortho_name}")
    return 0 if done else 1


def collect_transforms(tiles_dir: str) -> dict[str, list[float]]:
    """Map each content URI to the transform accumulated down the tileset tree."""
    out: dict[str, list[float]] = {}

    def walk(node: dict, base_dir: str, xform: list[float] | None) -> None:
        if "transform" in node:
            xform = multiply(xform, node["transform"])
        content = node.get("content") or {}
        uri = content.get("uri") or content.get("url")
        if uri:
            target = os.path.normpath(os.path.join(base_dir, uri.split("?")[0]))
            rel = os.path.relpath(target, tiles_dir).replace(os.sep, "/")
            if uri.endswith(".json"):
                load(target, xform)
            elif xform is not None:
                out[rel] = xform
        for child in node.get("children", []):
            walk(child, base_dir, xform)

    def load(path: str, xform: list[float] | None) -> None:
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            return
        if "root" in doc:
            walk(doc["root"], os.path.dirname(path), xform)

    root = os.path.join(tiles_dir, "tileset.json")
    if os.path.exists(root):
        load(root, None)
    return out


def project_file(
    path: str,
    bounds: dict,
    to_local: list[float],
    xform: list[float] | None,
    ortho_uri: str,
    wall_shade: float,
) -> bool:
    data = open(path, "rb").read()
    if data[:4] == b"b3dm":
        _, _, _, ftj, ftb, btj, btb = struct.unpack("<4sIIIIII", data[:28])
        head = 28 + ftj + ftb + btj + btb
        prefix, glb = data[:head], data[head:]
    else:
        prefix, glb = b"", data

    new_glb = project_glb(glb, bounds, to_local, xform, ortho_uri, wall_shade)
    if new_glb is None:
        return False

    if prefix:
        out = struct.pack("<4sII", b"b3dm", 1, len(prefix) + len(new_glb)) + prefix[12:] + new_glb
    else:
        out = new_glb
    with open(path, "wb") as fh:
        fh.write(out)
    return True


def project_glb(
    glb: bytes,
    bounds: dict,
    to_local: list[float],
    xform: list[float] | None,
    ortho_uri: str,
    wall_shade: float,
) -> bytes | None:
    magic, _, total = struct.unpack("<III", glb[:12])
    if magic != GLB_MAGIC:
        return None

    chunks = []
    i = 12
    while i < total:
        length, ctype = struct.unpack("<II", glb[i : i + 8])
        i += 8
        chunks.append([ctype, glb[i : i + length]])
        i += length

    gltf = json.loads(next(c[1] for c in chunks if c[0] == CHUNK_JSON))
    binary = bytearray(next((c[1] for c in chunks if c[0] == CHUNK_BIN), b""))
    if not gltf.get("meshes"):
        return None

    node_matrices = node_transforms(gltf)
    span_x = bounds["maxX"] - bounds["minX"]
    span_z = bounds["maxZ"] - bounds["minZ"]

    extra_views: list[bytes] = []
    for mesh_index, mesh in enumerate(gltf["meshes"]):
        model = node_matrices.get(mesh_index)
        full = multiply(xform, model) if xform is not None else model

        for prim in mesh.get("primitives", []):
            pos_index = prim["attributes"].get("POSITION")
            if pos_index is None:
                continue
            positions = read_accessor(gltf, binary, pos_index)

            uvs = bytearray()
            colors = bytearray()
            for px, py, pz in positions:
                wx, wy, wz = apply_point(full, (px, py, pz)) if full else (px, py, pz)
                lx, ly, lz = apply_point(to_local, (wx, wy, wz))
                # Local ENU is Z-up; the renderer and the ortho are Y-up with
                # north at -z, so east->x and north->-z.
                u = (lx - bounds["minX"]) / span_x
                v = (-ly - bounds["minZ"]) / span_z
                uvs += struct.pack("<ff", clamp01(u), clamp01(v))
                del ly, lz
            # Shade steep faces; a top-down projection has nothing true to say
            # about a wall, so make it read as a shadowed side instead.
            shade = wall_shade
            normals = prim["attributes"].get("NORMAL")
            if normals is not None and shade < 1.0:
                for nx, ny, nz in read_accessor(gltf, binary, normals):
                    up = abs(ny)
                    f = shade + (1.0 - shade) * up
                    colors += struct.pack("<fff", f, f, f)
                    del nx, nz

            prim["attributes"]["TEXCOORD_0"] = add_accessor(
                gltf, extra_views, bytes(uvs), len(positions), "VEC2"
            )
            if colors:
                prim["attributes"]["COLOR_0"] = add_accessor(
                    gltf, extra_views, bytes(colors), len(positions), "VEC3"
                )
            prim["material"] = 0

    # One material for everything, pointing at the shared external ortho.
    gltf["images"] = [{"uri": ortho_uri}]
    gltf["samplers"] = [{"magFilter": 9729, "minFilter": 9987, "wrapS": 33071, "wrapT": 33071}]
    gltf["textures"] = [{"source": 0, "sampler": 0}]
    gltf["materials"] = [
        {
            "name": "ortho",
            "pbrMetallicRoughness": {
                "baseColorTexture": {"index": 0},
                "metallicFactor": 0.0,
                "roughnessFactor": 1.0,
            },
            "doubleSided": True,
        }
    ]

    # Append the new buffer views to the end of the existing binary chunk.
    for payload in extra_views:
        while len(binary) % 4:
            binary.append(0)
        binary += payload
    while len(binary) % 4:
        binary.append(0)
    if gltf.get("buffers"):
        gltf["buffers"][0]["byteLength"] = len(binary)
    else:
        gltf["buffers"] = [{"byteLength": len(binary)}]

    json_bytes = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * ((4 - len(json_bytes) % 4) % 4)
    body = (
        struct.pack("<II", len(json_bytes), CHUNK_JSON)
        + json_bytes
        + struct.pack("<II", len(binary), CHUNK_BIN)
        + bytes(binary)
    )
    return struct.pack("<III", GLB_MAGIC, 2, 12 + len(body)) + body


def add_accessor(gltf, extra_views, payload: bytes, count: int, kind: str) -> int:
    """Register a new bufferView + accessor whose data is appended later."""
    offset = gltf.setdefault("_pending", 0)
    del offset
    views = gltf.setdefault("bufferViews", [])
    # Offsets are patched once all payloads are known; record the running total.
    running = sum(align4(len(p)) for p in extra_views)
    base = gltf.setdefault("_binLen", None)
    if base is None:
        base = max(
            (v.get("byteOffset", 0) + v["byteLength"] for v in views),
            default=0,
        )
        gltf["_binLen"] = base
    views.append({"buffer": 0, "byteOffset": align4(base) + running, "byteLength": len(payload)})
    extra_views.append(payload)
    gltf.setdefault("accessors", []).append(
        {"bufferView": len(views) - 1, "componentType": 5126, "count": count, "type": kind}
    )
    return len(gltf["accessors"]) - 1


def node_transforms(gltf: dict) -> dict[int, list[float] | None]:
    """Accumulated matrix for each mesh, walking the scene graph."""
    out: dict[int, list[float] | None] = {}
    nodes = gltf.get("nodes", [])

    def walk(index: int, parent: list[float] | None) -> None:
        node = nodes[index]
        local = node.get("matrix")
        current = multiply(parent, local) if local else parent
        if "mesh" in node:
            out[node["mesh"]] = current
        for child in node.get("children", []):
            walk(child, current)

    for scene in gltf.get("scenes", []):
        for root in scene.get("nodes", []):
            walk(root, None)
    if not out:
        for i, node in enumerate(nodes):
            if "mesh" in node:
                out[node["mesh"]] = node.get("matrix")
            del i
    return out


def read_accessor(gltf: dict, binary: bytes, index: int):
    acc = gltf["accessors"][index]
    view = gltf["bufferViews"][acc["bufferView"]]
    fmt, size = COMPONENT[acc["componentType"]]
    n = NUM_COMPONENTS[acc["type"]]
    stride = view.get("byteStride") or size * n
    start = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
    for i in range(acc["count"]):
        off = start + i * stride
        yield struct.unpack_from("<" + fmt * n, binary, off)


def enu_inverse(lat_deg: float, lon_deg: float, height: float) -> list[float]:
    """Column-major matrix mapping ECEF into the east-north-up frame at a point."""
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    sl, cl, sp, cp = math.sin(lat), math.cos(lat), math.sin(lon), math.cos(lon)
    n = WGS84_A / math.sqrt(1 - WGS84_E2 * sl * sl)
    ox = (n + height) * cl * cp
    oy = (n + height) * cl * sp
    oz = (n * (1 - WGS84_E2) + height) * sl
    east = (-sp, cp, 0.0)
    north = (-sl * cp, -sl * sp, cl)
    up = (cl * cp, cl * sp, sl)
    # Rows are the basis vectors; translation is -R·origin.
    return [
        east[0], north[0], up[0], 0.0,
        east[1], north[1], up[1], 0.0,
        east[2], north[2], up[2], 0.0,
        -(east[0] * ox + east[1] * oy + east[2] * oz),
        -(north[0] * ox + north[1] * oy + north[2] * oz),
        -(up[0] * ox + up[1] * oy + up[2] * oz),
        1.0,
    ]


def multiply(a, b):
    if a is None:
        return list(b) if b else None
    if b is None:
        return list(a)
    out = [0.0] * 16
    for c in range(4):
        for r in range(4):
            out[c * 4 + r] = sum(a[k * 4 + r] * b[c * 4 + k] for k in range(4))
    return out


def apply_point(m, p):
    if m is None:
        return p
    x, y, z = p
    return (
        m[0] * x + m[4] * y + m[8] * z + m[12],
        m[1] * x + m[5] * y + m[9] * z + m[13],
        m[2] * x + m[6] * y + m[10] * z + m[14],
    )


def clamp01(v: float) -> float:
    return 0.0 if v < 0 else 1.0 if v > 1 else v


def align4(n: int) -> int:
    return n + ((4 - n % 4) % 4)


if __name__ == "__main__":
    sys.exit(main())

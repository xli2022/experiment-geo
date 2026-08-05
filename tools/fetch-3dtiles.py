#!/usr/bin/env python3
"""
Download a spatial subtree of a remote OGC 3D Tiles tileset for self-hosting.

Why this exists
---------------
Several cities publish photogrammetric 3D Tiles as open data (Berlin, Japan's
PLATEAU, Helsinki). Those tilesets cover a whole city or country and are far too
large to mirror wholesale, but a game only needs the area it renders — and
hotlinking someone else's server from a deployed build is rude and fragile.

This walks the tileset from its root, descends only into tiles whose bounding
volume contains the target point, and stops at a depth or byte budget. The
result is a self-contained directory you can serve as a static asset, with the
same relative structure the tileset already uses.

Usage:
    tools/fetch-3dtiles.py <tileset-url> <out-dir> --lat 52.517 --lon 13.389 \
        [--max-bytes 300000000] [--max-depth 12]

Example (Berlin's open photogrammetric mesh, DL-DE Zero 2.0):
    tools/fetch-3dtiles.py \
      https://download-berlin3d.virtualcitymap.de/datasource-data/HOSTING-Berlin-DLPortal/Mesh_2025/tileset.json \
      public/tiles/berlin3d --lat 52.517 --lon 13.389
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from urllib.parse import urljoin

WGS84_A = 6378137.0
WGS84_E2 = 6.69437999014e-3


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("Usage")[0].strip())
    ap.add_argument("url", help="URL of the remote tileset.json")
    ap.add_argument("out_dir", help="local directory to write into")
    ap.add_argument("--lat", type=float, required=True, help="target latitude")
    ap.add_argument("--lon", type=float, required=True, help="target longitude")
    ap.add_argument("--height", type=float, default=40.0, help="target height (m)")
    ap.add_argument("--max-bytes", type=int, default=300_000_000)
    ap.add_argument("--max-depth", type=int, default=12)
    args = ap.parse_args()

    target = geodetic_to_ecef(args.lat, args.lon, args.height)
    os.makedirs(args.out_dir, exist_ok=True)

    state = {"bytes": 0, "tiles": 0, "json": 0, "skipped": 0}
    visit(args.url, args.url, args.out_dir, target, 0, args, state, None)

    print()
    print(f"Fetched {state['tiles']} content tiles + {state['json']} sub-tilesets")
    print(f"  {state['bytes'] / 1024 / 1024:.1f} MB into {args.out_dir}")
    print(f"  {state['skipped']} branches skipped (outside target)")
    if state["tiles"] == 0:
        print("  WARNING: nothing downloaded — is the target inside the tileset?", file=sys.stderr)
        return 1
    return 0


def visit(url: str, root_url: str, out_dir: str, target, depth: int, args, state, xform=None) -> None:
    """Fetch one tileset JSON and recurse into the children that contain the target."""
    if depth > args.max_depth or state["bytes"] >= args.max_bytes:
        return

    data = fetch(url)
    if data is None:
        return
    save(url, root_url, out_dir, data)
    state["json"] += 1
    state["bytes"] += len(data)

    try:
        node = json.loads(data)["root"]
    except (ValueError, KeyError) as exc:
        print(f"  bad tileset at {url}: {exc}", file=sys.stderr)
        return

    walk_node(node, url, root_url, out_dir, target, depth, args, state, xform)


def walk_node(
    node: dict, base_url: str, root_url: str, out_dir: str, target, depth, args, state, xform=None
):
    if state["bytes"] >= args.max_bytes:
        return

    # Bounding volumes live in the tile's local frame. 3D Tiles composes a
    # transform down the tree, and ignoring it makes every containment test
    # nonsense — Berlin's tiles sit under a local-frame transform, so an ECEF
    # point tests as thousands of half-widths outside a box it is actually in.
    if "transform" in node:
        xform = multiply(xform, node["transform"])

    if not contains(node.get("boundingVolume"), target, xform):
        state["skipped"] += 1
        return

    content = node.get("content") or {}
    uri = content.get("uri") or content.get("url")
    if uri:
        child_url = urljoin(base_url, uri)
        if uri.endswith(".json"):
            visit(child_url, root_url, out_dir, target, depth + 1, args, state, xform)
        else:
            blob = fetch(child_url)
            if blob is not None:
                save(child_url, root_url, out_dir, blob)
                state["tiles"] += 1
                state["bytes"] += len(blob)
                print(
                    f"\r  {state['tiles']} tiles, {state['bytes'] / 1024 / 1024:.0f} MB",
                    end="",
                    flush=True,
                )

    for child in node.get("children", []):
        if state["bytes"] >= args.max_bytes:
            return
        walk_node(child, base_url, root_url, out_dir, target, depth + 1, args, state, xform)


def multiply(a, b):
    """Compose two column-major 4x4 matrices (a then b applied outermost)."""
    if a is None:
        return list(b)
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


def apply_vector(m, v):
    if m is None:
        return v
    x, y, z = v
    return (
        m[0] * x + m[4] * y + m[8] * z,
        m[1] * x + m[5] * y + m[9] * z,
        m[2] * x + m[6] * y + m[10] * z,
    )


def contains(bv: dict | None, point, xform=None) -> bool:
    """Is the point inside this bounding volume? Unknown volumes are descended into."""
    if not bv:
        return True
    if "box" in bv:
        b = bv["box"]
        # Move the box into world space rather than inverting the transform.
        cx, cy, cz = apply_point(xform, (b[0], b[1], b[2]))
        d = (point[0] - cx, point[1] - cy, point[2] - cz)
        # Three half-axis vectors; the point is inside if its projection onto
        # each axis is within that axis' own length.
        for i in (3, 6, 9):
            ax = apply_vector(xform, (b[i], b[i + 1], b[i + 2]))
            length2 = ax[0] ** 2 + ax[1] ** 2 + ax[2] ** 2
            if length2 == 0:
                continue
            proj = d[0] * ax[0] + d[1] * ax[1] + d[2] * ax[2]
            if abs(proj) > length2:
                return False
        return True
    if "region" in bv:
        w, s, e, n = bv["region"][:4]
        lat, lon = ecef_to_latlon(point)
        return w <= math.radians(lon) <= e and s <= math.radians(lat) <= n
    if "sphere" in bv:
        cx, cy, cz = apply_point(xform, bv["sphere"][:3])
        r = bv["sphere"][3]
        return (point[0] - cx) ** 2 + (point[1] - cy) ** 2 + (point[2] - cz) ** 2 <= r * r
    return True


def fetch(url: str) -> bytes | None:
    """Fetch via curl — it honours the environment's proxy and CA config."""
    r = subprocess.run(
        ["curl", "-sSL", "--compressed", "--max-time", "60", url],
        capture_output=True,
    )
    if r.returncode != 0 or not r.stdout:
        print(f"\n  fetch failed: {url}", file=sys.stderr)
        return None
    return r.stdout


def save(url: str, root_url: str, out_dir: str, blob: bytes) -> None:
    """Mirror the remote path layout so relative URIs keep resolving."""
    root_base = root_url.rsplit("/", 1)[0] + "/"
    rel = url[len(root_base):] if url.startswith(root_base) else url.rsplit("/", 1)[-1]
    rel = rel.split("?")[0]
    path = os.path.normpath(os.path.join(out_dir, rel))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(blob)


def geodetic_to_ecef(lat_deg: float, lon_deg: float, h: float):
    lat, lon = math.radians(lat_deg), math.radians(lon_deg)
    n = WGS84_A / math.sqrt(1 - WGS84_E2 * math.sin(lat) ** 2)
    return (
        (n + h) * math.cos(lat) * math.cos(lon),
        (n + h) * math.cos(lat) * math.sin(lon),
        (n * (1 - WGS84_E2) + h) * math.sin(lat),
    )


def ecef_to_latlon(p):
    x, y, z = p
    lon = math.atan2(y, x)
    lat = math.atan2(z, math.sqrt(x * x + y * y) * (1 - WGS84_E2))
    return math.degrees(lat), math.degrees(lon)


if __name__ == "__main__":
    sys.exit(main())

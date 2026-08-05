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
    ap.add_argument(
        "--radius",
        type=float,
        default=700.0,
        help="metres around the target to fetch. A 3D Tiles client refines across "
        "siblings, so descending a single path from the root never reaches the "
        "detailed levels — the subtree has to be a neighbourhood, not a line.",
    )
    ap.add_argument("--max-bytes", type=int, default=300_000_000)
    ap.add_argument("--max-depth", type=int, default=12)
    args = ap.parse_args()

    target = geodetic_to_ecef(args.lat, args.lon, args.height)
    os.makedirs(args.out_dir, exist_ok=True)

    state = {"bytes": 0, "tiles": 0, "json": 0, "skipped": 0}
    visit(args.url, args.url, args.out_dir, target, 0, args, state, None)

    print()
    pruned = prune_all(args.out_dir)
    print(f"Pruned {pruned} dangling references to tiles outside the subtree")
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

    if not intersects(node.get("boundingVolume"), target, args.radius, xform):
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


def prune_all(out_dir: str) -> int:
    """
    Drop references to tiles we didn't download.

    A partial subtree still carries the original tileset's full child lists, so
    the renderer happily requests siblings that were never fetched. Served from
    a static host with an SPA fallback those 404s come back as HTML, and the
    loader dies on `Content type "<!do" not supported` rather than skipping the
    tile — one missing sibling takes down the whole world.
    """
    removed = 0
    for dirpath, _dirs, files in os.walk(out_dir):
        for name in files:
            if not name.endswith(".json"):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    doc = json.load(fh)
            except (OSError, ValueError):
                continue
            if "root" not in doc:
                continue
            removed += prune_node(doc["root"], dirpath)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, separators=(",", ":"))
    return removed


def prune_node(node: dict, base_dir: str) -> int:
    """Strip missing content and unreachable children. Returns how many were removed."""
    removed = 0

    content = node.get("content") or {}
    uri = content.get("uri") or content.get("url")
    if uri and not uri.startswith(("http://", "https://")):
        if not os.path.exists(os.path.normpath(os.path.join(base_dir, uri.split("?")[0]))):
            node.pop("content", None)
            removed += 1

    kept = []
    for child in node.get("children", []):
        removed += prune_node(child, base_dir)
        # A child with neither content nor children of its own renders nothing.
        if child.get("content") or child.get("children"):
            kept.append(child)
        else:
            removed += 1
    if kept:
        node["children"] = kept
    else:
        node.pop("children", None)

    return removed


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


def intersects(bv: dict | None, point, radius: float, xform=None) -> bool:
    """
    Does this bounding volume come within `radius` of the target?

    Deliberately a sphere test rather than point containment. A 3D Tiles client
    refines by comparing screen-space error across a tile's *siblings*, so a
    subtree containing only the tiles directly above the target never supplies
    enough of the tree for refinement to proceed — you get the coarse root
    meshes and nothing else. Fetching a neighbourhood fixes that.
    """
    if not bv:
        return True
    if "box" in bv:
        b = bv["box"]
        # Move the box into world space rather than inverting the transform.
        centre = apply_point(xform, (b[0], b[1], b[2]))
        d = (point[0] - centre[0], point[1] - centre[1], point[2] - centre[2])
        # Distance from the point to the box: project onto each half-axis,
        # clamp to the box, and measure what's left over.
        remainder = list(d)
        for i in (3, 6, 9):
            ax = apply_vector(xform, (b[i], b[i + 1], b[i + 2]))
            length2 = ax[0] ** 2 + ax[1] ** 2 + ax[2] ** 2
            if length2 == 0:
                continue
            t = (d[0] * ax[0] + d[1] * ax[1] + d[2] * ax[2]) / length2
            t = max(-1.0, min(1.0, t))
            for j in range(3):
                remainder[j] -= t * ax[j]
        return math.sqrt(sum(v * v for v in remainder)) <= radius
    if "region" in bv:
        w, s, e, n = bv["region"][:4]
        lat, lon = ecef_to_latlon(point)
        pad = math.degrees(radius / WGS84_A)
        return (
            math.degrees(w) - pad <= lon <= math.degrees(e) + pad
            and math.degrees(s) - pad <= lat <= math.degrees(n) + pad
        )
    if "sphere" in bv:
        cx, cy, cz = apply_point(xform, bv["sphere"][:3])
        r = bv["sphere"][3] + radius
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

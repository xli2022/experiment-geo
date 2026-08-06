#!/usr/bin/env python3
"""Find surfaces that still share a plane after the offset pass.

Z-fighting needs three things at once: two triangles facing the same way, on
the same plane to within the depth buffer's resolution, and overlapping on
screen. Counting fighting *pixels* in a render only finds what happens to be in
frame, and depends on the depth precision of whichever GPU rendered it — a
conflict that resolves on a desktop can tile on a phone. This works on the
geometry instead, so it finds every conflict in the world regardless of where
the camera is or what hardware draws it.

Method: bin each triangle by face normal, project it onto that plane's 2D grid,
and record every metre-cell its bounds cover. Cells holding two differently
coloured surfaces within the threshold are conflicts. Colours stand in for
materials here, which is what the renderer actually shows.

Usage:  find-coplanar.py <tiles-dir> [--threshold M] [--top N]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import tempfile
import urllib.parse
from collections import defaultdict

import numpy as np

# Normal quantisation: 40 steps over [-1, 1] puts coplanar surfaces from the
# same generator in one bin while keeping distinct faces of a box apart.
NORMAL_STEPS = 40
# Side of the in-plane cells triangles are bucketed into, in metres.
CELL = 1.0
# A triangle whose bounds cover more cells than this is a large ground or roof
# polygon; it still participates, but through its centroid cell only, since
# enumerating its full footprint costs more than the conflict is worth.
MAX_CELLS = 4096


def load(gltf_path: str) -> tuple[dict, list[bytes]]:
    with open(gltf_path) as fh:
        j = json.load(fh)
    base = os.path.dirname(gltf_path)
    bufs = []
    for b in j.get('buffers', []):
        with open(os.path.join(base, urllib.parse.unquote(b['uri'])), 'rb') as fh:
            bufs.append(fh.read())
    return j, bufs


def accessor(j: dict, bufs: list[bytes], i: int) -> np.ndarray:
    """Read an accessor, honouring byteStride.

    Interleaved buffer views are what gltf-transform emits, and ignoring the
    stride silently returns the neighbouring attribute's bytes as if they were
    this one's — which reads as plausible data rather than as an error.
    """
    a = j['accessors'][i]
    ncomp = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4}[a['type']]
    dt = np.dtype({5126: '<f4', 5125: '<u4', 5123: '<u2', 5121: '<u1'}[a['componentType']])
    bv = j['bufferViews'][a['bufferView']]
    data = bufs[bv.get('buffer', 0)]
    off = bv.get('byteOffset', 0) + a.get('byteOffset', 0)
    packed = ncomp * dt.itemsize
    stride = bv.get('byteStride') or packed
    span = (a['count'] - 1) * stride + packed
    raw = np.frombuffer(data, dtype=np.uint8, offset=off, count=span)
    view = np.lib.stride_tricks.as_strided(raw, shape=(a['count'], packed), strides=(stride, 1))
    return np.ascontiguousarray(view).view(dt).reshape(a['count'], ncomp)


def node_transforms(j: dict) -> dict[int, np.ndarray]:
    """World matrix per mesh index, walking the scene graph."""
    out: dict[int, np.ndarray] = {}
    nodes = j.get('nodes', [])

    def walk(idx: int, parent: np.ndarray) -> None:
        n = nodes[idx]
        local = np.eye(4)
        if 'matrix' in n:
            local = np.array(n['matrix'], dtype=np.float64).reshape(4, 4).T
        else:
            if 'scale' in n:
                local[:3, :3] *= np.array(n['scale'])
            if 'rotation' in n:
                x, y, z, w = n['rotation']
                local[:3, :3] = local[:3, :3] @ np.array([
                    [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                    [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                    [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
                ])
            if 'translation' in n:
                local[:3, 3] = n['translation']
        world = parent @ local
        if 'mesh' in n:
            out[n['mesh']] = world
        for child in n.get('children', []):
            walk(child, world)

    for scene in j.get('scenes', []):
        for root in scene.get('nodes', []):
            walk(root, np.eye(4))
    return out


def to_rgb8(colors: np.ndarray) -> np.ndarray:
    if colors.dtype.kind == 'f':
        return np.clip(colors[:, :3] * 255, 0, 255).astype(np.uint8)
    scale = 65535.0 if colors.dtype.itemsize == 2 else 255.0
    return np.clip(colors[:, :3].astype(np.float64) / scale * 255, 0, 255).astype(np.uint8)


def triangles(j: dict, bufs: list[bytes]) -> list[tuple[np.ndarray, np.ndarray]]:
    """Every primitive as (positions[t,3,3], colour[t,3]) in tile space."""
    mats = node_transforms(j)
    out = []
    for mi, mesh in enumerate(j.get('meshes', [])):
        m = mats.get(mi, np.eye(4))
        for prim in mesh['primitives']:
            attrs = prim['attributes']
            if 'POSITION' not in attrs or prim.get('mode', 4) != 4:
                continue
            pos = accessor(j, bufs, attrs['POSITION']).astype(np.float64)
            # OSM2World writes non-indexed triangle soup; gltf-transform's Draco
            # round-trip adds indices. Both have to work, or this reads only the
            # compressed tiles and reports the uncompressed ones as clean.
            if 'indices' in prim:
                idx = accessor(j, bufs, prim['indices']).reshape(-1).astype(np.int64)
            else:
                idx = np.arange(len(pos), dtype=np.int64)
            if idx.size % 3 or idx.size == 0 or idx.max() >= len(pos):
                continue
            pos = pos @ m[:3, :3].T + m[:3, 3]
            if 'COLOR_0' in attrs:
                rgb = to_rgb8(accessor(j, bufs, attrs['COLOR_0']))
            else:
                rgb = np.zeros((len(pos), 3), dtype=np.uint8)
            tri = pos[idx].reshape(-1, 3, 3)
            col = rgb[idx].reshape(-1, 3, 3)[:, 0, :]
            out.append((tri, col))
    return out


def scan_tile(path: str, threshold: float) -> list[tuple]:
    j, bufs = load(path)
    conflicts = []
    for tri, col in triangles(j, bufs):
        e1 = tri[:, 1] - tri[:, 0]
        e2 = tri[:, 2] - tri[:, 0]
        nrm = np.cross(e1, e2)
        length = np.linalg.norm(nrm, axis=1)
        keep = length > 1e-9
        if not keep.any():
            continue
        tri, col, nrm = tri[keep], col[keep], nrm[keep] / length[keep, None]

        # Same-facing only: two surfaces pointing opposite ways never tile
        # against each other, because only one of them is ever the front face.
        ncell = np.round(nrm * NORMAL_STEPS).astype(np.int64)
        dist = np.einsum('ij,ij->i', nrm, tri[:, 0])
        key32 = (col[:, 0].astype(np.int64) << 16) | (col[:, 1].astype(np.int64) << 8) | col[:, 2]

        # One in-plane basis per normal bin, so every triangle in a bin lands on
        # the same grid.
        buckets: dict[tuple, list[int]] = defaultdict(list)
        for cell, group in group_by_rows(ncell):
            n = np.array(cell, dtype=np.float64)
            n /= np.linalg.norm(n)
            helper = np.array([0.0, 0.0, 1.0]) if abs(n[1]) > 0.9 else np.array([0.0, 1.0, 0.0])
            u = np.cross(n, helper)
            u /= np.linalg.norm(u)
            v = np.cross(n, u)
            pts = tri[group]
            pu = pts @ u
            pv = pts @ v
            lo_u = np.floor(pu.min(axis=1) / CELL).astype(np.int64)
            hi_u = np.floor(pu.max(axis=1) / CELL).astype(np.int64)
            lo_v = np.floor(pv.min(axis=1) / CELL).astype(np.int64)
            hi_v = np.floor(pv.max(axis=1) / CELL).astype(np.int64)
            span = (hi_u - lo_u + 1) * (hi_v - lo_v + 1)
            for k, t in enumerate(group):
                if span[k] > MAX_CELLS:
                    cu = (lo_u[k] + hi_u[k]) // 2
                    cv = (lo_v[k] + hi_v[k]) // 2
                    buckets[(cell, cu, cv)].append(t)
                    continue
                for cu in range(lo_u[k], hi_u[k] + 1):
                    for cv in range(lo_v[k], hi_v[k] + 1):
                        buckets[(cell, cu, cv)].append(t)

        for cellkey, members in buckets.items():
            if len(members) < 2:
                continue
            m = np.array(members)
            if len(np.unique(key32[m])) < 2:
                continue
            order = m[np.argsort(dist[m])]
            d = dist[order]
            k = key32[order]
            gap = np.abs(np.diff(d))
            differ = k[:-1] != k[1:]
            hit = np.nonzero(differ & (gap < threshold))[0]
            for h in hit:
                a = order[h]
                where = tuple(round(float(v), 2) for v in tri[a, 0])
                conflicts.append((float(gap[h]), int(k[h]), int(k[h + 1]), where, cellkey[0]))
    return conflicts


def group_by_rows(rows: np.ndarray):
    """Yield (row-as-tuple, indices) for each distinct row."""
    order = np.lexsort(rows.T[::-1])
    srt = rows[order]
    breaks = np.nonzero(np.any(srt[1:] != srt[:-1], axis=1))[0] + 1
    for chunk in np.split(order, breaks):
        yield tuple(int(x) for x in rows[chunk[0]]), chunk


def decode(glb: str, into: str) -> str | None:
    """Draco-decode a baked tile to plain glTF.

    The shipped tiles are Draco-compressed, which has no pure-Python decoder, so
    this shells out to the same gltf-transform the bake already depends on.
    Slow — a few seconds a tile — but this is a verification tool, and running it
    against the tiles that actually ship is the entire point: measuring the
    uncompressed bake would miss every conflict compression introduces, which is
    where most of them came from.
    """
    out = os.path.join(into, 'd.gltf')
    try:
        subprocess.run(
            ['npx', '--yes', '@gltf-transform/cli', 'cp', glb, out],
            check=True, capture_output=True, timeout=600,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as exc:
        print(f'  {glb}: decode failed ({type(exc).__name__})', file=sys.stderr)
        return None
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('root', help='directory of baked .glb tiles, or of decoded .gltf')
    ap.add_argument('--threshold', type=float, default=0.012, help='metres')
    ap.add_argument('--top', type=int, default=25)
    args = ap.parse_args()

    tiles = sorted(glob.glob(os.path.join(args.root, '**', '*.glb'), recursive=True))
    if not tiles:
        tiles = sorted(glob.glob(os.path.join(args.root, '**', '*.gltf'), recursive=True))
    if not tiles:
        print(f'error: no .glb or .gltf under {args.root}', file=sys.stderr)
        return 1

    pairs: dict[tuple[int, int], list] = defaultdict(list)
    total = 0
    for tile in tiles:
        name = os.path.relpath(tile, args.root)
        with tempfile.TemporaryDirectory() as tmp:
            path = decode(tile, tmp) if tile.endswith('.glb') else tile
            if path is None:
                continue
            found = scan_tile(path, args.threshold)
        total += len(found)
        for gap, ka, kb, where, _ in found:
            pairs[(min(ka, kb), max(ka, kb))].append((gap, where, name))
        print(f'  {name}: {len(found)}', file=sys.stderr)

    print(f'\ncoplanar conflicts within {args.threshold * 1000:.0f} mm: {total}')
    print(f'{"count":>7}  {"colour A":>9} {"colour B":>9}  {"median gap":>10}  example')
    ranked = sorted(pairs.items(), key=lambda kv: -len(kv[1]))
    for (ka, kb), hits in ranked[: args.top]:
        gaps = sorted(h[0] for h in hits)
        med = gaps[len(gaps) // 2]
        tile = hits[0][2]
        print(
            f'{len(hits):>7}  #{ka:06x}  #{kb:06x}  {med * 1000:>8.1f}mm  {hits[0][1]} in {tile}'
        )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env bash
#
# Compress baked .glb tiles with Draco geometry compression and WebP textures.
#
# Why this exists
# ---------------
# OSM2World writes uncompressed glTF: raw float32 vertex data and embedded
# JPEGs. Measured on Monaco (~7.8 km², LOD 2, 16 tiles at zoom 15):
#
#   stock, 4K textures                    1.6 GB    ~98 MB per tile
#   + tools/shrink-textures.py            960 MB    ~59 MB per tile
#   + this script                           37 MB    ~2.3 MB per tile  (4% of 1 GB)
#
# Geometry, not textures, is the dominant cost after downscaling — 53 MB of a
# 59 MB tile. Draco is what makes the pipeline viable: without it a single tile
# is a larger download than most whole web games.
#
# Note that `optimize` also runs `simplify`, which is lossy on geometry. Pass
# --no-simplify if architectural detail matters more than the last few MB.
#
# Usage:
#   tools/optimize-tiles.sh <tiles-dir> [--no-simplify]

set -euo pipefail

TILES_DIR="${1:?usage: optimize-tiles.sh <tiles-dir> [--no-simplify]}"
SIMPLIFY_FLAG=""
if [[ "${2:-}" == "--no-simplify" ]]; then
  SIMPLIFY_FLAG="--simplify false"
fi

if [[ ! -d "$TILES_DIR" ]]; then
  echo "error: not a directory: $TILES_DIR" >&2
  exit 1
fi

mapfile -t GLBS < <(find "$TILES_DIR" -name '*.glb' -type f)
if (( ${#GLBS[@]} == 0 )); then
  echo "error: no .glb files under $TILES_DIR" >&2
  exit 1
fi

BEFORE=$(du -sb "$TILES_DIR" | cut -f1)
echo "Optimizing ${#GLBS[@]} tiles in $TILES_DIR ($(du -sh "$TILES_DIR" | cut -f1))"

START=$(date +%s)
# Compression is split into two commands rather than left to `optimize`,
# because `optimize` gives no way to set the quantization grid and its default
# destroys the ground-layer offsets.
#
# Draco quantizes positions onto a *uniform* grid sized by the mesh's largest
# extent, so a 750 m wide tile lands every axis — including height — on one
# grid: 45.8 mm at the default 14 bits, 11.4 mm at 16 (both measured, not
# inferred). The separations tools/offset-ground.py and tools/vary-buildings.py
# apply run from 4 mm to a few centimetres, so at 16 bits the finer half of them
# snapped back to a shared height and the surfaces they were meant to separate
# came out exactly coplanar again — measured across one tile, 6,670 of 11,466
# coincident pairs existed only because of the grid.
#
# 20 bits gives 0.72 mm, which every separation clears by at least fivefold.
# It costs 1.44x the bytes (0.36 -> 0.53 MB on a z15 Berlin tile), which against
# a 1 GB budget the whole city currently uses 2.5% of is not a real constraint.
QUANTIZE_POSITION="${QUANTIZE_POSITION:-20}"

for glb in "${GLBS[@]}"; do
  # The temporary names have to keep the .glb extension. gltf-transform picks
  # its container from the extension alone, so writing to "$glb.opt" produced
  # glTF JSON with the buffer in a sidecar .bin — and since the result was then
  # moved onto a .glb path, every tile shipped as a JSON file named .glb with a
  # second file beside it. It loaded (three.js sniffs the magic and falls back),
  # but it cost two requests per tile instead of one.
  tmp="${glb%.glb}.opt.glb"
  pre="${glb%.glb}.pre.glb"
  if npx --yes @gltf-transform/cli optimize "$glb" "$pre" \
       --compress false --texture-compress webp $SIMPLIFY_FLAG >/dev/null 2>&1 \
     && npx --yes @gltf-transform/cli draco "$pre" "$tmp" \
       --quantize-position "$QUANTIZE_POSITION" >/dev/null 2>&1; then
    rm -f "$pre" "$glb.bin"
    mv "$tmp" "$glb"
    printf '.'
  else
    rm -f "$tmp"
    printf 'x'
    echo >&2
    echo "warning: failed to optimize $glb — left uncompressed" >&2
  fi
done
echo

AFTER=$(du -sb "$TILES_DIR" | cut -f1)
ELAPSED=$(( $(date +%s) - START ))

echo
echo "Done in ${ELAPSED}s"
python3 - "$BEFORE" "$AFTER" "${#GLBS[@]}" <<'PY'
import sys
before, after, count = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
mb = 1024 * 1024
limit = 1024 * mb
print(f"  {before/mb:.0f} MB -> {after/mb:.0f} MB ({before/after:.1f}x smaller)")
print(f"  {after/count/mb:.1f} MB per tile across {count} tiles")
print(f"  uses {after/limit*100:.0f}% of the 1 GB GitHub Pages budget")
PY

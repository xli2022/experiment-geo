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
#   + this script                          ~76 MB   ~4.7 MB per tile
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
for glb in "${GLBS[@]}"; do
  tmp="$glb.opt"
  if npx --yes @gltf-transform/cli optimize "$glb" "$tmp" \
       --compress draco --texture-compress webp $SIMPLIFY_FLAG >/dev/null 2>&1; then
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

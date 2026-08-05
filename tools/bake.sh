#!/usr/bin/env bash
#
# Bake an OSM extract into OGC 3D Tiles with OSM2World.
#
# The baked output is a build artifact, not source — it is gitignored, and this
# script plus a pinned extract date is the reproducible source of truth.
#
# Usage:
#   tools/bake.sh <city> <south,west north,east> [lod]
#
# Example:
#   tools/bake.sh monaco "43.7237,7.4090 43.7519,7.4398" 2
#
# Environment:
#   OSM2WORLD_HOME  Directory containing OSM2World.jar (default: vendor/osm2world)
#   OSM_PBF         Path to the input .osm.pbf (default: vendor/extracts/<city>.osm.pbf)
#   JAVA_HEAP       JVM max heap (default: 6g)

set -euo pipefail

CITY="${1:?usage: bake.sh <city> <\"south,west north,east\"> [lod]}"
BBOX="${2:?usage: bake.sh <city> <\"south,west north,east\"> [lod]}"
LOD="${3:-2}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OSM2WORLD_HOME="${OSM2WORLD_HOME:-$REPO_ROOT/vendor/osm2world}"
OSM_PBF="${OSM_PBF:-$REPO_ROOT/vendor/extracts/$CITY.osm.pbf}"
JAVA_HEAP="${JAVA_HEAP:-10g}"
OUT_DIR="$REPO_ROOT/public/tiles/$CITY"

if [[ ! -f "$OSM2WORLD_HOME/OSM2World.jar" ]]; then
  echo "error: OSM2World.jar not found in $OSM2WORLD_HOME" >&2
  echo "  Download: https://osm2world.org/download/files/latest/OSM2World-latest-bin.zip" >&2
  echo "  Then unzip it there, or set OSM2WORLD_HOME." >&2
  exit 1
fi

if [[ ! -f "$OSM_PBF" ]]; then
  echo "error: input extract not found: $OSM_PBF" >&2
  echo "  Download one from https://download.geofabrik.de/ or set OSM_PBF." >&2
  exit 1
fi

if ! command -v osmium >/dev/null 2>&1; then
  echo "error: osmium not found (apt-get install osmium-tool)" >&2
  echo "  Required — see the comment on the cut step below for why." >&2
  exit 1
fi

echo "Baking $CITY at LOD $LOD"
echo "  input:  $OSM_PBF ($(du -h "$OSM_PBF" | cut -f1))"
echo "  bbox:   $BBOX"
echo "  output: $OUT_DIR"

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

START=$(date +%s)

# Cut the input down to the target area first.
#
# OSM2World loads the *entire* input file into memory — --bbox only selects
# which tiles get written, it does not reduce what gets parsed. A city-sized
# Geofabrik extract (Berlin is 95 MB) exhausts a 12 GB heap before producing a
# single tile; the same area cut to 1.6 MB bakes in seconds.
#
# --set-bounds is load-bearing and very easy to miss: osmium omits the
# bounding-box header by default, and without it OSM2World derives a degenerate
# bounds and fails *every* tile with "a polygon's area must be positive" /
# "cannot construct polygon from zero area rectangle". Verified by cutting a
# known-good Monaco extract both ways — without the flag 16/16 tiles failed,
# with it 16/16 succeeded.
#
# -s smart keeps relations intact across the cut boundary.
CUT_PBF="$OUT_DIR/.input.osm.pbf"
read -r SW NE <<<"$BBOX"
IFS=, read -r S W <<<"$SW"
IFS=, read -r N E <<<"$NE"
# osmium wants west,south,east,north; our bbox is "south,west north,east".
# Pad the cut so geometry at the render boundary still has its context.
PAD=0.004
osmium extract -s smart --set-bounds \
  -b "$(bc -l <<<"$W-$PAD"),$(bc -l <<<"$S-$PAD"),$(bc -l <<<"$E+$PAD"),$(bc -l <<<"$N+$PAD")" \
  "$OSM_PBF" -o "$CUT_PBF" --overwrite
echo "  cut:    $(du -h "$CUT_PBF" | cut -f1)"

java "-Xmx$JAVA_HEAP" -jar "$OSM2WORLD_HOME/OSM2World.jar" tileset \
  --input "$CUT_PBF" \
  --baseDir "$OUT_DIR" \
  --bbox="$BBOX" \
  --lod="$LOD" \
  --overwrite=always \
  > "$OUT_DIR/.bake.log" 2>&1 || true

rm -f "$CUT_PBF"

ELAPSED=$(( $(date +%s) - START ))
TILES_OK=$(find "$OUT_DIR" -name '*.glb' -size +1k | wc -l)
TILES_FAILED=$(grep -c "Failed to create tile" "$OUT_DIR/.bake.log" || true)

echo
echo "Baked in ${ELAPSED}s"
echo "  tiles:  $TILES_OK written, $TILES_FAILED failed"

# A tile that fails is silently absent rather than loudly broken, so surface it.
# OSM2World aborts a whole tile on one bad polygon, and some regions trip
# unhandled cases in its geometry or material lookup (see README).
if (( TILES_OK == 0 )); then
  echo "  ERROR: no tiles were produced. Last errors:" >&2
  grep -oE "Failed to create tile.*|NullPointerException.*|OutOfMemoryError.*" \
    "$OUT_DIR/.bake.log" 2>/dev/null | sort -u | head -3 >&2
  exit 1
fi

SIZE_BYTES=$(du -sb "$OUT_DIR" | cut -f1)
echo "  size:   $(du -sh "$OUT_DIR" | cut -f1)"

# GitHub Pages hard-caps a published site at 1 GB. Because the app bundle is
# negligible next to the tileset, this is effectively the tileset's budget.
PAGES_LIMIT=$((1024 * 1024 * 1024))
PERCENT=$(( SIZE_BYTES * 100 / PAGES_LIMIT ))
echo "  uses ${PERCENT}% of the 1 GB GitHub Pages budget"
if (( SIZE_BYTES > PAGES_LIMIT )); then
  echo "  WARNING: over the GitHub Pages limit — shrink the bbox, drop a LOD," >&2
  echo "           or host the tileset on R2 and set VITE_TILESET_URL." >&2
fi

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
JAVA_HEAP="${JAVA_HEAP:-6g}"
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

echo "Baking $CITY at LOD $LOD"
echo "  input:  $OSM_PBF ($(du -h "$OSM_PBF" | cut -f1))"
echo "  bbox:   $BBOX"
echo "  output: $OUT_DIR"

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

START=$(date +%s)

# --precompressedTiles stores tiles gzipped but references the uncompressed
# names, which matters because GitHub Pages compresses text but not binary
# payloads. Compression has to happen here, not in transport.
java "-Xmx$JAVA_HEAP" -jar "$OSM2WORLD_HOME/OSM2World.jar" tileset \
  --input "$OSM_PBF" \
  --baseDir "$OUT_DIR" \
  --bbox="$BBOX" \
  --lod="$LOD" \
  --overwrite=always \
  2>&1 | grep -viE "^\s+at |^Picked up|^Caused by|^Polygon" || true

ELAPSED=$(( $(date +%s) - START ))
SIZE_BYTES=$(du -sb "$OUT_DIR" | cut -f1)
FILE_COUNT=$(find "$OUT_DIR" -type f | wc -l)

echo
echo "Baked in ${ELAPSED}s"
echo "  size:  $(du -sh "$OUT_DIR" | cut -f1) across $FILE_COUNT files"

# GitHub Pages hard-caps a published site at 1 GB. Because the app bundle is
# negligible next to the tileset, this is effectively the tileset's budget.
PAGES_LIMIT=$((1024 * 1024 * 1024))
PERCENT=$(( SIZE_BYTES * 100 / PAGES_LIMIT ))
echo "  uses ${PERCENT}% of the 1 GB GitHub Pages budget"
if (( SIZE_BYTES > PAGES_LIMIT )); then
  echo "  WARNING: over the GitHub Pages limit — shrink the bbox, drop a LOD," >&2
  echo "           or host the tileset on R2 and set VITE_TILESET_URL." >&2
fi

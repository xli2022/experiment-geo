#!/usr/bin/env bash
#
# Bake an OSM extract into OGC 3D Tiles with OSM2World, one tile at a time.
#
# The baked output is a build artifact, not source — this script plus a pinned
# extract date is the reproducible source of truth.
#
# Usage:
#   tools/bake.sh <city> <south,west north,east> [lod] [zoom]
#
# Example:
#   tools/bake.sh berlin/all "52.5085,13.3805 52.5255,13.4035" 2
#
# Environment:
#   OSM2WORLD_HOME  Directory containing OSM2World.jar (default: vendor/osm2world)
#   OSM_PBF         Path to the input .osm.pbf (default: vendor/extracts/<city>.osm.pbf)
#   JAVA_HEAP       JVM max heap (default: 10g)
#   O2W_CONFIG      OSM2World properties file (default: the generated low-poly config)

set -euo pipefail

CITY="${1:?usage: bake.sh <city> <\"south,west north,east\"> [lod] [zoom]}"
BBOX="${2:?usage: bake.sh <city> <\"south,west north,east\"> [lod] [zoom]}"
LOD="${3:-2}"
ZOOM="${4:-15}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OSM2WORLD_HOME="${OSM2WORLD_HOME:-$REPO_ROOT/vendor/osm2world}"
# The argument is a path under public/tiles — "berlin/all", not "berlin" —
# because a city holds one directory per layer. The extract is still per city,
# so the default takes the first segment; "berlin/all" reads berlin.osm.pbf.
CITY_NAME="${CITY%%/*}"
OSM_PBF="${OSM_PBF:-$REPO_ROOT/vendor/extracts/$CITY_NAME.osm.pbf}"
JAVA_HEAP="${JAVA_HEAP:-10g}"
O2W_CONFIG="${O2W_CONFIG:-$OSM2WORLD_HOME/lowpoly.properties}"
OUT_DIR="$REPO_ROOT/public/tiles/$CITY"

if [[ ! -f "$OSM2WORLD_HOME/OSM2World.jar" ]]; then
  echo "error: OSM2World.jar not found in $OSM2WORLD_HOME" >&2
  echo "  Download: https://osm2world.org/download/files/latest/OSM2World-latest-bin.zip" >&2
  exit 1
fi
if [[ ! -f "$OSM_PBF" ]]; then
  echo "error: input extract not found: $OSM_PBF" >&2
  echo "  Download one from https://download.geofabrik.de/ or set OSM_PBF." >&2
  exit 1
fi
if ! command -v osmium >/dev/null 2>&1; then
  echo "error: osmium not found (apt-get install osmium-tool)" >&2
  exit 1
fi
if [[ ! -f "$O2W_CONFIG" ]]; then
  echo "error: OSM2World config not found: $O2W_CONFIG" >&2
  echo "  Generate it: tools/make-lowpoly-config.py $OSM2WORLD_HOME/standard.properties \\" >&2
  echo "                 -o $OSM2WORLD_HOME/lowpoly.properties" >&2
  exit 1
fi

echo "Baking $CITY at LOD $LOD, zoom $ZOOM"
echo "  input:  $OSM_PBF ($(du -h "$OSM_PBF" | cut -f1))"
echo "  bbox:   $BBOX"
echo "  config: $O2W_CONFIG"
echo "  output: $OUT_DIR"

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"
# Scratch lives outside public/ — anything under it is copied into dist/ by
# Vite and would be published alongside the world.
WORK="$REPO_ROOT/vendor/.bake-work/$CITY"
rm -rf "$WORK"
mkdir -p "$WORK"
START=$(date +%s)

read -r SW NE <<<"$BBOX"
IFS=, read -r S W <<<"$SW"
IFS=, read -r N E <<<"$NE"

# Cut once to the whole area, then strip tag values OSM2World cannot map.
#
# OSM2World loads the *entire* input file into memory — a city-sized Geofabrik
# extract exhausts a 12 GB heap before producing a tile. And a single
# unmappable `surface=` value anywhere in the input aborts every tile in the
# run, not just the offending way (see tools/clean-osm.py).
#
# --set-bounds is load-bearing and easy to miss: osmium omits the bounding-box
# header by default, and without it OSM2World derives degenerate bounds and
# fails every tile with "a polygon's area must be positive". Verified by
# cutting a known-good Monaco extract both ways — without the flag 16/16 tiles
# failed, with it 16/16 succeeded.
AREA_PBF="$WORK/area.osm.pbf"
PAD=0.004
osmium extract -s smart --set-bounds \
  -b "$(bc -l <<<"$W-$PAD"),$(bc -l <<<"$S-$PAD"),$(bc -l <<<"$E+$PAD"),$(bc -l <<<"$N+$PAD")" \
  "$OSM_PBF" -o "$AREA_PBF" --overwrite
echo "  cut:    $(du -h "$AREA_PBF" | cut -f1)"

CLEAN_PBF="$WORK/clean.osm.pbf"
"$REPO_ROOT/tools/clean-osm.py" "$AREA_PBF" -o "$CLEAN_PBF" | sed 's/^/  /'

# Enumerate the XYZ tiles covering the bbox, each cut with a small overlap.
#
# OSM2World places a tile's origin using the WGS84 prime-vertical radius but
# sizes the tile's geometry with the spherical Web Mercator scale, so every
# tile comes out N(phi)/a = 0.2114% smaller than the slot it is placed in.
# Measured at z15/lat 52.5: adjacent origins 745.751 m apart carrying geometry
# that spans 744.178 m, leaving a 1.573 m gap you can see the sky through along
# every north-south seam.
#
# OSM2World does not clip output to the tile — it renders whatever the input
# contains — so widening the cut widens the geometry. OVERLAP_M of 3 m yields
# roughly 2.9 m of overlap after the shortfall, enough at any latitude (the
# ratio varies by under 0.0003% across a city). The overlapping band is flat
# terrain of identical colour, so coplanar z-fighting in it is invisible; the
# alternative, scaling each tile's transform, would close this seam but push
# the north-south seams, which already overlap by 0.283 m, into a visible one.
OVERLAP_M="${OVERLAP_M:-3}"
mapfile -t TILES < <(python3 - "$ZOOM" "$S" "$W" "$N" "$E" "$OVERLAP_M" <<'PY'
import math, sys
z = int(sys.argv[1])
s, w, n, e, overlap = map(float, sys.argv[2:7])
size = 2.0 ** z
def xt(lon): return int((lon + 180.0) / 360.0 * size)
def yt(lat): return int((1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * size)
def lon_edge(x): return x / size * 360.0 - 180.0
def lat_edge(y): return math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / size))))
for x in range(xt(w), xt(e) + 1):
    for y in range(yt(n), yt(s) + 1):
        tw, ts, te, tn = lon_edge(x), lat_edge(y + 1), lon_edge(x + 1), lat_edge(y)
        dlat = overlap / 111320.0
        dlon = overlap / (111320.0 * math.cos(math.radians((ts + tn) / 2.0)))
        # west south east north, as osmium wants them
        print(f"{x} {y} {tw - dlon} {ts - dlat} {te + dlon} {tn + dlat}")
PY
)

echo "  tiles:  ${#TILES[@]} to bake"
echo

# Bake each tile from an input cut to that tile's own bounds.
#
# This is the whole reason the script loops. OSM2World renders *everything in
# the input file* into every tile it writes — the tile argument only picks the
# output path. Feeding it the full area once produced N tiles that each
# contained the entire area: measured 4 tiles whose geometry AABBs were
# identical at 1558x1892 m, exactly the input extract's bounds, against a true
# z15 tile size of 1223x744 m. Stacked coincident copies z-fight against each
# other and multiply the triangle count by N.
OK=0
FAILED=0
for entry in "${TILES[@]}"; do
  read -r TX TY TW TS TE TN <<<"$entry"
  TILE_PBF="$WORK/tile.osm.pbf"
  # The cut carries only the few metres of OVERLAP_M computed above. The
  # earlier 0.004 degree (~445 m) pad is what produced whole-area duplication;
  # at 3 m only features actually touching a seam appear in both tiles.
  if ! osmium extract -s smart --set-bounds -b "$TW,$TS,$TE,$TN" \
       "$CLEAN_PBF" -o "$TILE_PBF" --overwrite 2>>"$WORK/bake.log"; then
    echo "  tile $TX/$TY: cut failed"
    FAILED=$((FAILED + 1))
    continue
  fi

  if (cd "$OSM2WORLD_HOME" && java "-Xmx$JAVA_HEAP" -jar OSM2World.jar tileset \
        --input "$TILE_PBF" --baseDir "$OUT_DIR" \
        --bboxTiles="$ZOOM,$TX,$TY" --lod="$LOD" \
        --config "$O2W_CONFIG" --overwrite=always >>"$WORK/bake.log" 2>&1) \
     && [[ -s "$(find "$OUT_DIR" -name "$TY.glb" -path "*/$TX/*" | head -1)" ]]; then
    OK=$((OK + 1))
    printf '  tile %s/%s ok (%d/%d)\n' "$TX" "$TY" "$((OK + FAILED))" "${#TILES[@]}"
  else
    FAILED=$((FAILED + 1))
    printf '  tile %s/%s FAILED (%d/%d)\n' "$TX" "$TY" "$((OK + FAILED))" "${#TILES[@]}"
  fi
done

rm -rf "$WORK/area.osm.pbf" "$WORK/clean.osm.pbf" "$WORK/tile.osm.pbf"

ELAPSED=$(( $(date +%s) - START ))
echo
echo "Baked in ${ELAPSED}s"
echo "  tiles:  $OK written, $FAILED failed"

if (( OK == 0 )); then
  echo "  ERROR: no tiles were produced. Last errors:" >&2
  grep -oE "Caused by:.*|Failed to create tile.*|OutOfMemoryError.*" \
    "$WORK/bake.log" 2>/dev/null | sort -u | head -5 >&2
  exit 1
fi

SIZE_BYTES=$(du -sb "$OUT_DIR" | cut -f1)
echo "  size:   $(du -sh "$OUT_DIR" | cut -f1) (before tools/optimize-tiles.sh)"
echo
echo "Next:"
echo "  tools/vary-buildings.py $OUT_DIR"
echo "  tools/optimize-tiles.sh $OUT_DIR --no-simplify"
echo "  tools/make-root-tileset.py $OUT_DIR"

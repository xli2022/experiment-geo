# experiment-geo

A world-exploration game built on open map data.

**Version 1 goal:** a flying camera you can move freely around in 3D space, over a real
city with realistic streets and buildings.

| | |
|---|---|
| Platform | Web — TypeScript + Vite + three.js |
| Geometry | Pre-baked with [OSM2World](https://osm2world.org/) → OGC 3D Tiles |
| Look | Realistic — real roof shapes, lane markings, PBR materials |
| Streaming | [`3d-tiles-renderer`](https://github.com/NASA-AMMOS/3DTilesRendererJS) |
| Game layer | None yet — a world viewer first |

## Quick start

```bash
npm install
npm run dev
```

A baked world (Monaco) is committed, so this runs with nothing else to set up.
See **Baking a world** below to build a different one.

```bash
npm run typecheck   # tsc --noEmit
npm test            # vitest
npm run build       # typecheck + vite build
```

## Controls

Click the canvas to capture the pointer, then:

| Input | Action |
|---|---|
| Mouse | Look |
| `W` `A` `S` `D` | Move horizontally, relative to where you're looking |
| `Q` / `E` / `Space` | Move straight down / up, always world-aligned |
| `Shift` | Boost (×4) |
| `Esc` | Release the pointer |

Movement speed scales with altitude, so the controls feel the same whether you're
inspecting a doorway or crossing the city.

## Baking a world

Geometry is generated offline rather than at runtime, because realism has to come from a
renderer that knows how buildings are shaped — under 0.4% of OSM buildings carry any
material or colour tag, so there is nothing to render realistically *from* in the raw
data.

```bash
# 1. OSM2World -> vendor/osm2world/  (~478 MB, mostly its texture library)
curl -L https://osm2world.org/download/files/latest/OSM2World-latest-bin.zip -o o2w.zip
unzip -q o2w.zip -d vendor/osm2world && rm o2w.zip

# 2. An extract from https://download.geofabrik.de/
mkdir -p vendor/extracts
curl -L https://download.geofabrik.de/europe/germany/berlin-latest.osm.pbf \
     -o vendor/extracts/berlin.osm.pbf

# 3. Shrink the texture library — once, before the first bake
pip install Pillow && tools/shrink-textures.py

# 4. Bake, then compress
tools/bake.sh berlin "52.5125,13.3845 52.5215,13.3995" 2
tools/optimize-tiles.sh public/tiles/berlin
tools/make-root-tileset.py public/tiles/berlin
```

**Steps 3 and 4 are not optional.** OSM2World writes uncompressed glTF and embeds its 4K
PBR texture sets into *every* tile, so raw output is roughly 100 MB per tile. Measured on
Monaco (~7.8 km², LOD 2, 16 tiles at zoom 15):

| Stage | Total | Per tile | % of 1 GB budget |
|---|---|---|---|
| Stock output | 1.6 GB | ~98 MB | 154% — doesn't fit |
| `shrink-textures.py` (512px) | 960 MB | ~59 MB | 94% |
| `optimize-tiles.sh` (Draco + WebP) | **37 MB** | **2.3 MB** | **4%** |

That's a **45× reduction overall**, and it moves the budget from *"Monaco alone doesn't
fit"* to roughly **200 km² of city within the 1 GB Pages limit** — larger than the whole
of San Francisco or Paris. Compressing all 16 tiles takes ~60 s.

Note that geometry, not textures, dominates after downscaling — 53 MB of a 59 MB tile —
so Draco compression is what actually makes this viable. `optimize-tiles.sh` also runs
`simplify`, which is lossy; pass `--no-simplify` if architectural detail matters more than
the last few MB.

**LOD is the other cost lever.** LOD 4 did not finish within 6 minutes on 4 cores for an
area this size; LOD 2 takes ~100 s. Start low.

Baked tiles land in `public/tiles/<city>/` and **are committed**, because at ~37 MB they
fit comfortably and that lets GitHub Pages deploy a working world straight from a push —
no Java, no 478 MB download, no bake in CI. `tools/bake.sh` plus a pinned extract date
remains the source of truth for regenerating them; re-bake when the world needs to change
rather than casually, since each one adds a copy to git history.

After baking a new world, generate its root tileset and point the app at it:

```bash
tools/make-root-tileset.py public/tiles/<city>
# then set TILESET_URL in src/config.ts, or VITE_TILESET_URL at build time
```

## Hosting

Built for GitHub Pages, whose **1 GB published-site limit is the binding constraint** on
how much city you can ship. Two things follow:

- `vite.config.ts` sets `base: '/experiment-geo/'`, since project sites are served from a
  subpath. Override with `BASE_PATH=/` for other hosts.
- Deployment goes through the Pages artifact (`.github/workflows/deploy.yml`), not by
  committing `dist/`. Note that **Git LFS does not work with GitHub Pages** — it serves
  only the pointer files — so there is no way to sidestep the limit that way.

If the tileset outgrows 1 GB, host it on Cloudflare R2 (zero egress fees) and set
`VITE_TILESET_URL`. That's the only change needed.

## Data & licensing

Map data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), under
the Open Database License. Geometry rendered with [OSM2World](https://osm2world.org/)
(MIT, textures included).

Attribution is displayed in-app without requiring interaction, as ODbL requires. Note that
**baked geometry is a Derivative Database** rather than a Produced Work, so a published
tileset should itself be offered under ODbL — the application code and any original art
are unaffected.

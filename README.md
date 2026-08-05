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

The app needs a baked tileset to show anything. See **Baking a world** below.

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

1. Download OSM2World and unzip it to `vendor/osm2world/`:
   <https://osm2world.org/download/files/latest/OSM2World-latest-bin.zip> (~478 MB, mostly
   its shared texture library).
2. Download an extract from [Geofabrik](https://download.geofabrik.de/) to
   `vendor/extracts/<city>.osm.pbf`.
3. Bake:

   ```bash
   tools/bake.sh monaco "43.7237,7.4090 43.7519,7.4398" 2
   ```

The script reports output size as a percentage of the 1 GB GitHub Pages budget, and warns
if you exceed it. Baked tiles land in `public/tiles/<city>/` and are gitignored — they're
build artifacts, and `tools/bake.sh` plus a pinned extract date is the source of truth.

**LOD is the main cost lever.** Higher levels are dramatically more expensive to bake;
start at LOD 2 and raise it only if the detail is worth the time and bytes.

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

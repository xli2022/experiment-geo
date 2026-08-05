# experiment-geo

A world-exploration game built on open map data.

**Version 1 goal:** a flying camera you can move freely around in 3D space, over a real
city with realistic streets and buildings.

| | |
|---|---|
| Platform | Web — TypeScript + Vite + three.js |
| Geometry | Berlin's open photogrammetric mesh (OGC 3D Tiles) |
| Look | Stylized — aerial imagery flattened toward a city-builder palette |
| Streaming | [`3d-tiles-renderer`](https://github.com/NASA-AMMOS/3DTilesRendererJS) |
| Game layer | None yet — a world viewer first |

## Quick start

```bash
npm install
npm run dev
```

A world (Berlin Mitte) is committed, so this runs with nothing else to set up.

```bash
npm run typecheck   # tsc --noEmit
npm test            # vitest
npm run build       # typecheck + vite build
```

## Controls

**Desktop** — click the canvas to capture the pointer, then:

| Input | Action |
|---|---|
| Mouse | Look |
| `W` `A` `S` `D` | Move horizontally, relative to where you're looking |
| `Q` / `E` / `Space` | Move straight down / up, always world-aligned |
| `Shift` | Boost (×4) |
| `Esc` | Release the pointer |

**Touch** — no pointer lock and no keyboard, so the scheme is different:

| Input | Action |
|---|---|
| Left half | Floating virtual stick — strafe and forward/back |
| Right half | Drag to look |
| ▲ / ▼ | Climb / descend |
| » | Boost |

The stick materialises wherever your thumb lands rather than sitting at a fixed spot —
on a phone held any number of ways, a fixed stick is a constant small aiming task.
Touches are tracked by identifier, so moving and looking work simultaneously.

Movement speed scales with altitude on both, so the controls feel the same whether you're
inspecting a doorway or crossing the city.

Input lives behind `InputSource` (`src/camera/input/`), and both sources are attached when
the device supports both — a touchscreen laptop gets each without either knowing about the
other.

## The world

Berlin publishes a **photogrammetric 3D mesh of the whole city as open data** —
real aerial-survey imagery, under
[dl-de/zero-2-0](https://www.govdata.de/dl-de/zero-2-0), which carries no restrictions
at all. It is served as OGC 3D Tiles with `access-control-allow-origin: *`, so a browser
reads it directly.

`tools/fetch-3dtiles.py` downloads a bounded neighbourhood around a point for
self-hosting (311 tiles / 162 MB around Mitte — 16% of the Pages budget):

```bash
tools/fetch-3dtiles.py \
  https://download-berlin3d.virtualcitymap.de/datasource-data/HOSTING-Berlin-DLPortal/Mesh_2025/tileset.json \
  public/tiles/berlin3d --lat 52.5170 --lon 13.3889 --radius 600
```

Two things it has to get right, both of which fail silently otherwise:

- **Bounding volumes live in each tile's local frame** and 3D Tiles composes a
  `transform` down the tree. Ignoring it doesn't skew the test — an ECEF point comes out
  21,000 half-widths outside a box it is squarely inside, and the crawl returns nothing.
- **A partial subtree must have its dangling child references pruned.** A 3D Tiles client
  refines across *siblings*, so it will request tiles you never fetched; served from a
  static host those 404s come back as HTML and the loader dies on
  `Content type "<!do" not supported`, taking the whole world down rather than one tile.

The same tool works against [PLATEAU](https://www.mlit.go.jp/plateau/en/) (~250 Japanese
cities) and [Helsinki 3D](https://www.hel.fi/en/decision-making/information-on-helsinki/maps-and-geospatial-data/helsinki-3d).

### Restyling the textures

Raw photogrammetry is accurate but visually noisy — desaturated, grainy, and full of
high-frequency detail that reads as mush from altitude and grime up close.
`tools/stylize-tiles.py` rewrites the textures inside the `.b3dm` files toward a flatter
city-builder look, leaving the meshes untouched:

```bash
tools/stylize-tiles.py public/tiles/berlin3d --preview /tmp/look.png  # tune first
tools/stylize-tiles.py public/tiles/berlin3d                          # then commit to it
```

**"Less detail" means less variation, not less sharpness.** A city-builder render is
*crisper* than a photograph — large flat areas of colour separated by hard edges.
Downscaling and blurring produce the opposite, so the pipeline keeps full resolution and
sharpens at the end:

1. **Bilateral filter** — the load-bearing step. It weights neighbours by colour distance
   as well as position, so roof averages with roof and road with road, but neither bleeds
   across the boundary between them. A median or Gaussian blur softens both equally, which
   is exactly the mush to avoid.
2. **Lift the shadows** — aerial shadows carry a heavy blue cast, and saturating them
   directly turns every shaded wall electric blue. It reads as broken, not stylized.
3. Saturate and add contrast.
4. **Palette-quantize** — after filtering, never before; do it first and the filter smears
   the palette straight back into gradients.
5. **Unsharp mask** — restores the crispness quantizing softens, and pushes past the
   original.

Cost: **162 MB → 191 MB**. Sharpening is expensive in JPEG — the edge halos are exactly
what the codec is worst at — so the quality default is 78 rather than the 88 the look
would otherwise want. Downscaling would claw all of it back and more, but it is the one
lever that directly destroys the effect.

### The OSM route, and why it was abandoned

The original pipeline baked geometry from OpenStreetMap with
[OSM2World](https://osm2world.org/). It works and the tooling is still here
(`tools/bake.sh`, `shrink-textures.py`, `optimize-tiles.sh`, `make-root-tileset.py`), but
it cannot look real: only ~0.4% of OSM buildings carry a material or colour tag, so almost
every building renders in one default plaster. Measured across 16 cities, Berlin Mitte was
the sole outlier at 71% material coverage — and OSM2World then throws a
NullPointerException on Mitte's unusual `surface=` values, aborting whole tiles.

Worth keeping in mind: **San Francisco has 71.7% height coverage** where Berlin's data is
flat, so it remains the better choice if the priority ever shifts to skyline or once
terrain lands.


## Baking a world from OSM (the alternative route)

Still supported, and the right choice if you need a city with no open photogrammetric
mesh. It produces stylized geometry, not photographs.

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

The shipped world is the 3D mesh © [Geoportal Berlin](https://daten.berlin.de/), under
[dl-de/zero-2-0](https://www.govdata.de/dl-de/zero-2-0) — no restrictions, commercial use
included.

The OSM route uses map data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright)
under ODbL, rendered with [OSM2World](https://osm2world.org/) (MIT, textures included).
Note that **baked OSM geometry is a Derivative Database** rather than a Produced Work, so
a published tileset built that way should itself be offered under ODbL — application code
and original art are unaffected.

Attribution is displayed in-app without requiring interaction, as both licences expect.

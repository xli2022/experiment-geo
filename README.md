# experiment-geo

A world-exploration game built on open map data.

**Version 1 goal:** a flying camera you can move freely around in 3D space, over a real
city with realistic streets and buildings.

| | |
|---|---|
| Platform | Web — TypeScript + Vite + three.js |
| Geometry | OpenStreetMap, baked with [OSM2World](https://osm2world.org/) (OGC 3D Tiles) |
| Colour | Berlin's open photogrammetric mesh, projected on as an orthophoto |
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

The shipped world is a **hybrid**: geometry from OpenStreetMap, colour from Berlin's
photogrammetric mesh. Neither source works alone.

| | Geometry | Colour |
|---|---|---|
| OSM2World | clean — hard edges, real roof planes | none; ~0.4% of OSM buildings carry a material tag |
| Photogrammetry | melted, blobby reconstruction | real aerial survey imagery |

Taking the colour off one and draping it over the other gives buildings that are both
recognisable *and* correctly coloured — orange roofs orange, parks green, the Spree
blue — on geometry that still reads as architecture rather than melted wax.

`public/tiles/berlin-hybrid/` is committed and is what `npm run dev` loads: 9 tiles over
Mitte spanning 2233 m × 2233 m (5.0 km²), **14 MB published — 1.4% of the Pages budget**.

**Known gap: the colour does not reach the edge of the geometry.** The ortho was captured
at `--extent 1400`, so it covers 2.0 km² of the 5.0 km² the tiles span — roughly the
middle 40%. Everything outside it samples transparent ortho and renders black, which is
the dark band visible at the far edge when you fly out. Re-capturing at `--extent 2300`
closes it; that needs the 162 MB mesh fetched again, so it has not been done.

### Reproducing it

Three steps, because the colour has to be rendered before it can be projected.

```bash
# 1. Bake the geometry from OSM  (see "Baking a world from OSM" below for setup)
tools/bake.sh berlin "52.5125,13.3845 52.5215,13.3995" 2
tools/optimize-tiles.sh public/tiles/berlin
tools/make-root-tileset.py public/tiles/berlin

# 2. Fetch the photogrammetric mesh and render it straight down into an orthophoto
tools/fetch-3dtiles.py \
  https://download-berlin3d.virtualcitymap.de/datasource-data/HOSTING-Berlin-DLPortal/Mesh_2025/tileset.json \
  public/tiles/berlin3d --lat 52.5170 --lon 13.3889 --radius 600
npx vite preview &        # capture renders through the app, against the mesh
VITE_TILESET_URL=/experiment-geo/tiles/berlin3d/tileset.json npm run build
CHROMIUM_PATH=... node tools/capture-ortho.mjs --size 4096 --extent 1400

# 3. Project the orthophoto onto the OSM geometry
tools/project-ortho.py public/tiles/berlin ortho.png ortho.json \
  --wall-shade 0.6 --wall-quantize 10.0
```

Capture renders the mesh **through the app rather than rasterising it in Python** on
purpose: the renderer already gets tile transforms, LOD selection and occlusion right,
and any of those subtly wrong in a reimplementation stays invisible until the colours
land on the wrong buildings.

Three things the projection has to get right, all of which fail quietly:

- **glTF is Y-up; 3D Tiles is Z-up**, and the spec has the *client* apply the conversion
  between glTF content and the tile transform. Skip it and north silently swaps with
  height — every UV collapses to a line and the texture renders as vertical smears.
- **The orthophoto already contains baked sunlight**, so the material must be
  `KHR_materials_unlit`. Lighting it a second time washes it out.
- **glTF buffers carry trailing padding**, so a new accessor's offset must come from the
  real buffer length rather than the extent of existing views, or the UVs are garbage.

**Walls are the known compromise.** A top-down projection has nothing to say about a
vertical surface, so it smears whatever is directly above down the facade.
`--wall-shade` darkens steep faces to read as shadowed sides and `--wall-quantize` snaps
their UVs to a 10 m grid so the smear is at least uniform. At flying altitude this is
convincing; at street level it is not. Fixing it properly needs facade imagery, which
the top-down mesh does not contain.

The ortho ships as one shared `ortho.webp` referenced externally rather than embedded
per tile, so all 9 tiles pull a single 1.3 MB download instead of carrying a copy each.
The `ortho.png` beside it is the uncompressed intermediate — gitignored, never published.

### The photogrammetric source

Berlin publishes a **photogrammetric 3D mesh of the whole city as open data** — real
aerial-survey imagery, under [dl-de/zero-2-0](https://www.govdata.de/dl-de/zero-2-0),
which carries no restrictions at all. It is served as OGC 3D Tiles with
`access-control-allow-origin: *`, so a browser reads it directly.

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

The fetched mesh is **not committed** — it is an intermediate the ortho capture consumes,
and at 162 MB it would cost 16% of the Pages budget to publish something no longer
shipped. Re-run the fetch when the world needs rebuilding.

### Two routes that were tried and rejected

Both are still in the tree, because the measurements are worth keeping.

**Restyling the photogrammetry** (`tools/stylize-tiles.py`) rewrites the textures inside
the `.b3dm` files toward a flatter city-builder look, leaving the meshes untouched.
The pipeline is sound — bilateral filter at full resolution, shadow lift, saturate,
palette-quantize, unsharp — and it is deliberately built around one finding:

> **"Less detail" means less variation, not less sharpness.** A city-builder render is
> *crisper* than a photograph — large flat areas of colour separated by hard edges.
> Downscaling and blurring produce the opposite. The first attempt did exactly that and
> came out blurrier than the original, which is the opposite of the goal.

It still doesn't work, for a reason no amount of filter tuning fixes: the *geometry* is
melted. Photogrammetric buildings have no hard edges to preserve, so flattening the
colour just makes the blobbiness more obvious. Cost was **162 MB → 191 MB** — sharpening
is expensive in JPEG, since edge halos are what the codec handles worst.

**Baking from OSM alone** gives the opposite failure: clean geometry, no colour. Only
~0.4% of OSM buildings carry a material or colour tag, so almost every building renders
in one default plaster. Measured across 16 cities, Berlin Mitte was the sole outlier at
71% material coverage — and OSM2World then throws a NullPointerException on Mitte's
unusual `surface=` values, aborting whole tiles.

The hybrid exists because each route fails on exactly what the other has.

Worth keeping in mind: **San Francisco has 71.7% height coverage** where Berlin's data is
flat, so it is the better choice if the priority ever shifts to skyline or once terrain
lands — but it has no open photogrammetric mesh, so the colour half of the hybrid would
need another source.


## Baking a world from OSM

Step 1 of the hybrid, and on its own the right choice for a city with no open
photogrammetric mesh — it produces clean untextured geometry rather than photographs.

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

OSM2World emits no root tileset of its own, and the per-tile bounding regions it writes
are identical across tiles — `make-root-tileset.py` derives the true bounds from each
tile's `z/x/y` path instead. Without it the client cannot tell the tiles apart spatially
and culls almost everything.

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

The shipped world derives from **two sources, and both licences apply to it**:

- **Geometry** — map data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright)
  under **ODbL**, rendered with [OSM2World](https://osm2world.org/) (MIT, textures
  included).
- **Colour** — the 3D mesh © [Geoportal Berlin](https://daten.berlin.de/), under
  [dl-de/zero-2-0](https://www.govdata.de/dl-de/zero-2-0), which carries no restrictions.

Because the geometry half comes from OSM, **the published tileset is a Derivative
Database rather than a Produced Work**, and is offered under ODbL. That is the binding
constraint of the two — dl-de/zero-2-0 imposes nothing further. Rendered frames are a
Produced Work, so application code and original art are unaffected.

Attribution for both is displayed in-app without requiring interaction, as ODbL expects.

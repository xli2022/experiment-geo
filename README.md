# experiment-geo

A world-exploration game built on open map data.

**Version 1 goal:** a flying camera you can move freely around in 3D space, over a real
city rendered as readable low-poly geometry.

Two areas ship today — **Berlin Mitte** and **Tokyo Shinjuku** — switchable from the
picker in the HUD, or with `?city=berlin-mitte` / `?city=tokyo-shinjuku`.

| | |
|---|---|
| Platform | Web — TypeScript + Vite + three.js |
| Geometry | OpenStreetMap via [OSM2World](https://osm2world.org/), and [PLATEAU](https://www.mlit.go.jp/plateau/) for Tokyo (OGC 3D Tiles) |
| Look | Low-poly for OSM worlds — textures removed, one flat palette colour per surface type. PLATEAU keeps its own photo textures. |
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
| Mouse | Look — needs pointer lock |
| `←` `→` `↑` `↓` | Look, at ~80°/s — works without pointer lock |
| `W` `A` `S` `D` | Move horizontally, relative to where you're looking |
| `Q` / `E` | Move straight down / up, always world-aligned |
| `Shift` | Boost (×4) |
| `Esc` | Release the pointer |

Arrows look rather than move because movement is already covered and turning is not:
mouse look needs pointer lock, so before you click the canvas — or after `Esc` releases
it — the camera can fly but cannot turn. Binding them to WASD's job would duplicate a
capability instead of adding the missing one. Unlike the mouse, a held key reports only
that it is down, so the angle comes from the frame time; `test/arrowLook.test.ts` pins
that a one-second press turns the same amount at 30 fps and at 144.

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

### How the tiles are laid out

```
public/tiles/<city>/<layer>/tileset.json
```

One directory per city, one per layer inside it. Berlin bakes as a single
tileset carrying ground, buildings and trees together, so its layer is `all`.
PLATEAU splits a city into CityGML packages and publishes each as its own
tileset, so Tokyo has `bldg`, `tran`, `wtr` and so on. The shape is the same
either way, which is what lets a city be a list of layers rather than a special
case per source.


The shipped world is **low-poly**: OpenStreetMap geometry rendered by OSM2World with every
texture removed and one flat colour per surface type. No photogrammetry, no imagery.

That is a deliberate choice made against measurements rather than taste. The two things
this project has are clean geometry and OSM2World's ~83 *named* materials (`ASPHALT`,
`BRICK`, `ROOF_DEFAULT`, `GRASS`, `WATER`, ...) assigned by feature type. The two things
it does not have are facade imagery of any kind, and a trustworthy source of real colour:

| Probe | Result |
|---|---|
| OSM `building:material` / `building:colour` coverage | ~0.4% of buildings |
| Berlin orthophoto saturation | median **0.077** — near-greyscale |
| Berlin orthophoto hue split | 61.5% grey · **24.2% blue** · 2.6% green · 1.8% red-orange |
| OSM2World `COLOR_0` vertex attribute | 247 values, **all r=g=b** — an AO term, not material |

That 24.2% blue is shadow, not water. Aerial shadows carry a heavy blue cast, so the
strongest non-grey signal in the imagery is a lighting artifact — which is why the colour
is keyed on material names instead.

Low-poly is the one style whose defining quality is the *absence* of the surface detail
that is missing. Its colour requirement — one flat value per surface — is satisfiable from
material semantics alone, and it drops every artifact the textured attempts produced:
smeared walls, blur, and the hard edge of imagery coverage all stop existing.

The palette lives in `tools/make-lowpoly-config.py` and is muted on purpose. OSM2World
ships `ROOF_DEFAULT` as `#cc0000`, pillar-box red; at city scale saturated defaults read
as a fairground rather than a city.

### Lighting, and one thing the renderer has to get right

Flat colour has no texture detail to imply form, so the lighting carries all of it. The
balance is deliberately sun-dominant: a strong directional key with a modest hemisphere
fill, because under a strong ambient term every face of a box receives nearly the same
light and the shape disappears — which is most of why untextured buildings read as coloured
cubes. Ground-contact occlusion does the rest, since without it the join between a wall and
the pavement is a hard colour change and nothing else.

**Screen-space effects and `logarithmicDepthBuffer` do not compose by default.** three.js
injects `USE_LOGARITHMIC_DEPTH_BUFFER` into *every* material when the renderer asks for log
depth, including the `MeshNormalMaterial` that `GTAOPass` uses for its own depth prepass —
but `GTAOShader` unprojects that depth with `perspectiveDepthToViewZ`, the formula for an
ordinary buffer. The result is not subtly off: against an 80 km far plane a surface 100 m
away reconstructs as 3.4 m away, and its neighbour a metre behind it reconstructs 4 mm
further, so every occlusion test answers on rounding noise and what reaches the screen is
the pass's own 5×5 sampling pattern — a fixed halftone over every facade that reads exactly
like z-fighting. `src/engine/gtaoLogDepth.ts` recovers the view distance from the log
encoding; both shaders route through one `getViewPosition`, so that is the whole fix. It
throws rather than degrades if a three.js upgrade rewrites the shader, because the silent
failure mode sends you hunting in the geometry for a week.

### Two routes that were tried and rejected

Both are still in the tree, because the measurements are worth keeping.

**Restyling photogrammetry** (`tools/stylize-tiles.py`) flattens the textures inside
Berlin's photogrammetric mesh toward a city-builder look — bilateral filter at full
resolution, shadow lift, saturate, palette-quantize, unsharp. It is built around one
finding worth repeating:

> **"Less detail" means less variation, not less sharpness.** A city-builder render is
> *crisper* than a photograph — large flat areas of colour separated by hard edges.
> Downscaling and blurring produce the opposite.

It still fails, because the *geometry* is melted. Photogrammetric buildings have no hard
edges to preserve, so flattening the colour only makes the blobbiness plainer. Cost was
162 MB → 191 MB.

**Projecting an orthophoto onto OSM geometry** (`tools/capture-ortho.mjs` +
`tools/project-ortho.py`) took colour from the mesh and draped it over OSM2World shapes.
Closer, but a top-down projection has nothing to say about a vertical surface, so facades
got whatever was directly above them smeared down them.

Both remain reproducible; see `tools/fetch-3dtiles.py` for the mesh, which is
[dl-de/zero-2-0](https://www.govdata.de/dl-de/zero-2-0) open data from
[Geoportal Berlin](https://daten.berlin.de/) and works the same way against
[PLATEAU](https://www.mlit.go.jp/plateau/en/) (~250 Japanese cities) and
[Helsinki 3D](https://www.hel.fi/en/decision-making/information-on-helsinki/maps-and-geospatial-data/helsinki-3d).

Dropping photogrammetry also un-pins the project from the handful of cities that publish a
mesh. Low-poly from OSM works anywhere on earth, which is the point of a world-exploring
game — and it puts **San Francisco** back in reach (71.7% building-height coverage against
Berlin's much flatter data) for when terrain lands.

## Baking a world

```bash
# 1. OSM2World -> vendor/osm2world/  (~478 MB, mostly its texture library)
curl -L https://osm2world.org/download/files/latest/OSM2World-latest-bin.zip -o o2w.zip
unzip -q o2w.zip -d vendor/osm2world && rm o2w.zip

# 2. An extract from https://download.geofabrik.de/
mkdir -p vendor/extracts
curl -L https://download.geofabrik.de/europe/germany/berlin-latest.osm.pbf \
     -o vendor/extracts/berlin.osm.pbf

# 3. Generate the flat palette config — once, and again after an OSM2World update
tools/make-lowpoly-config.py vendor/osm2world/standard.properties \
    -o vendor/osm2world/lowpoly.properties

# 4. Bake, separate the ground layers, vary the buildings, compress, root tileset
tools/bake.sh berlin-mitte/all "52.4970,13.3560 52.5370,13.4220" 2 15
tools/offset-ground.py public/tiles/berlin-mitte/all
tools/vary-buildings.py public/tiles/berlin-mitte/all
tools/optimize-tiles.sh public/tiles/berlin-mitte/all --no-simplify
tools/make-root-tileset.py public/tiles/berlin-mitte/all

# 5. Check the result — every surface that still shares a plane with another
tools/find-coplanar.py public/tiles/berlin-mitte/all
```

`offset-ground.py` fixes the z-fighting along kerbs, road edges and area
boundaries. OSM2World puts terrain, roads, pavements, kerbs, ballast and lane markings all
at ground level, and ground level means *exactly* `y = 0` — measured in one z15 tile,
**19,902 near-horizontal triangles within 6 cm of zero**, 35.8% asphalt against 31.3%
terrain and 15.4% pavement, every one competing for the same depth value. No renderer
setting fixes that: a depth buffer cannot order coplanar surfaces, `logarithmicDepthBuffer`
(already on) improves precision across *distance* rather than resolving ties, and polygon
offset has nothing to bias against because a tile is a single material. So the tool pushes
each layer to its own height, in the order a street is built — 8 cm across the whole stack,
invisible in flight and orders of magnitude more than the depth buffer needs.

It does the same above ground for roof coverings, and along their own normals rather than
straight up, so a dome separates from its structure everywhere instead of splitting at the
top and staying welded at the sides. **Water gets its own layer just under the
carriageway**: OSM2World draws a water body's top face at exactly `y = 0`, the same height
as the quays and bridge approaches along its banks, and that alone was ~121,000 of the
252,696 coincident pairs in the world. Down rather than up, because a quay stands above the
river beside it.

The last two passes are hashes rather than tables. Every table here is an enumeration and
OSM2World does not draw from a closed set — with `useBuildingColors` on it writes whatever
`building:colour` and `roof:colour` say — so a roof can arrive in a colour nothing
anticipates, matching no table and no variant in `vary-buildings.py` either. Those were the
only surfaces in the world still getting no offset at all. Hashing the colour to a level
covers them without anyone enumerating them.

`vary-buildings.py` runs on the raw bake, before compression — Draco-compressed
accessors have no readable buffer view. OSM2World assigns materials per *feature
type*, so every untagged roof lands on one shade of terracotta and the skyline reads as a
single flat sheet. The tool finds connected components among roof-coloured triangles and
gives each one a palette variant chosen by a stable hash of its centroid, so a building
keeps its colour across re-bakes. Components are joined only within one material, never
across, which is what stops a Berlin terrace collapsing into a single colour.

It does the same for walls. The wall spread is deliberately tighter than the roof
spread — a roof is seen at an angle and in pieces, while walls are large flat areas
filling the frame at street level, so the same spread that reads as variety on roofs
reads as blotchiness on walls.

**Windows are the one texture this pipeline keeps.** `make-lowpoly-config.py` generates a
~300 byte tiling PNG — flat, hard-edged, one bay per 2.5 m storey — and points
`BUILDING_WINDOWS` at it with `colorable = true`, so the texture multiplies against
whatever wall colour the building was given rather than painting a fixed colour of its own.
Measured cost: **88,483 triangles against 89,251 without**, i.e. none, because the windows
are painted rather than modelled.

Two alternatives were measured and rejected. `windowImplementation = FULL_GEOMETRY`
produced a *byte-identical* bake at LOD 2. LOD 3 does add detail, but it is street
furniture — benches, lamp posts, bus shelters — at **+94% triangles**, with facades left
blank.

Note `--no-simplify`: `optimize-tiles.sh` otherwise runs a lossy geometry simplify pass,
and crisp edges are the entire point of this style. The tiles are small enough without it.

### Six things the bake has to get right

Each of these fails quietly — you get output, it is just wrong.

**Draco's quantization grid can be coarser than the geometry you carefully placed.** It
snaps positions onto a *uniform* grid sized by the mesh's largest extent, so on a 750 m
tile every axis — including height — shares one step: 45.8 mm at the default 14 bits,
11.4 mm at 16 (measured, not inferred). The separations `offset-ground.py` and
`vary-buildings.py` apply run from 4 mm up, so at 16 bits the finer half of them rounded
straight back to a shared height and the surfaces came out exactly coplanar again —
**6,670 of 11,466 coincident pairs in one tile existed only because of the grid**. The
offsets were all applied correctly; nothing in the tools was wrong; the world still
z-fought. `QUANTIZE_POSITION` is 20 bits (0.72 mm) for that reason, at 1.44× the bytes.
`tools/find-coplanar.py` is what measures this — it works on the geometry rather than on
rendered pixels, so it finds every conflict in the world regardless of where the camera is
or how much depth precision the GPU rendering it happens to have.

**gltf-transform picks its container from the file extension.** Writing to a temporary name
like `$glb.opt` produced glTF JSON with the buffer in a sidecar `.bin`, which was then
moved onto a `.glb` path — so every tile shipped as a JSON file named `.glb` with a second
file beside it. It loaded fine (three.js sniffs the magic and falls back), which is exactly
why it went unnoticed for so long; it just cost two requests per tile instead of one.

**OSM2World renders the whole input file into every tile.** The tile argument only chooses
the output path; it does not clip geometry. Baking a multi-tile area from one input cut
produced tiles whose geometry AABBs were *identical* at 1558×1892 m — exactly the input
extract's bounds — against a true z15 tile size of 744×744 m at this latitude. Every tile
held a full copy of the area, so N tiles meant N coincident copies z-fighting against each
other and N× the triangles. `tools/bake.sh` therefore loops, cutting the input to each
tile's own bounds before baking that tile. The cut carries only a few metres of
deliberate overlap (see below) — the original 0.004° (~445 m) pad is what produced the
whole-area duplication in the first place.

**Tiles need a metres-wide overlap, because OSM2World sizes them slightly too
small.** It places a tile's origin using the WGS84 prime-vertical radius but sizes the
tile's geometry with the spherical Web Mercator scale, so every tile comes out
`N(φ)/a` = **0.2114%** smaller than the slot it is placed in. Measured at z15 / lat 52.5:
adjacent origins 745.751 m apart carrying geometry that spans 744.178 m, leaving a
**1.573 m gap you can see the sky through** along every north–south seam — invisible from
altitude, obvious at street level. `OVERLAP_M` (default 3 m) widens each per-tile cut to
cover it. The overlapping band is flat terrain of one flat colour, so coplanar z-fighting
inside it is invisible. Scaling each tile's transform instead would close this seam but
push the east–west seams, which already overlap by 0.283 m, into a visible one.

**One unmappable tag value kills the entire run.** OSM2World resolves `surface=*` through
`DefaultMaterials.getSurfaceMaterial()`, and for a value it doesn't know that returns null
which the caller dereferences immediately. The NPE propagates out of the ForkJoin pool and
takes down every tile, not just the offending way. Berlin Mitte trips this on
`concrete:plates`, `tactile_paving`, `metal` and the invalid multi-value
`sett;paving_stones`. `tools/clean-osm.py` strips anything outside a conservative
allowlist — 64 tags out of 4324 in a typical extract.

**`osmium extract` needs `--set-bounds`.** It omits the bounding-box header by default,
and without it OSM2World derives degenerate bounds and fails every tile with "a polygon's
area must be positive". Verified by cutting a known-good Monaco extract both ways: without
the flag 16/16 tiles failed, with it 16/16 succeeded.

### Why the compression steps are not optional

Measured on the Berlin bake, whose geometry is untextured apart from one tiling window
pattern:

| Stage | Per tile | Note |
|---|---|---|
| Stock OSM2World, textured | ~98 MB | 4K PBR sets embedded in *every* tile |
| Low-poly config (no textures) | ~11 MB | unindexed float32 vertices |
| `optimize-tiles.sh` (Draco) | **~1.2 MB** | 36× smaller |

Geometry, not textures, dominates once textures are gone, so Draco is what makes this
viable. OSM2World writes unindexed triangles — every triangle carries three unique
vertices at 36 bytes each — which is exactly the redundancy Draco removes.

**LOD is the other cost lever.** LOD 4 did not finish within 6 minutes on 4 cores for a
small area; LOD 2 bakes a tile in ~25 s. Start low.

Baked tiles land in `public/tiles/<city>/<layer>/` and **are committed**, because that lets GitHub
Pages deploy a working world straight from a push — no Java, no 478 MB download, no bake
in CI. `tools/bake.sh` plus a pinned extract date remains the source of truth; re-bake when
the world needs to change rather than casually, since each one adds a copy to git history.

Worlds are listed in `src/cities.ts`, one entry per area with its layers, anchor and
credit line. `VITE_TILES_BASE` repoints all of them at another host — Cloudflare R2 or any
CORS-enabled bucket — if the tilesets outgrow the 1 GB Pages limit.

### WebP or KTX2

Both are used, and the choice is about memory rather than download size.

**WebP is a transport format.** It compresses the download, then decodes to RGBA8 and
uploads uncompressed — a 1024-square texture costs 4 MB of VRAM however small the file
was. Measured on the Tokyo bake, 0.4 MB of WebP became **9 MB** once decoded, and that is
paid for every tile resident at once.

**KTX2 (Basis Universal) is a GPU format.** It transcodes to whatever the hardware wants —
BC7, ASTC, ETC2 — and *stays* compressed in VRAM, roughly 4-8× smaller, with mipmaps in
the container rather than generated at load. `KHR_texture_basisu` is also a ratified
Khronos extension where `EXT_texture_webp` is a vendor one.

The difference is visible, not theoretical: Tokyo streamed **4** visible tiles on WebP and
would not improve however long it settled; on KTX2 it reaches **37**. Transcoding and
uploading a compressed texture is cheaper than decoding to RGBA8 and uploading that.

So `TEXTURE_FORMAT=ktx2` wherever textures carry real image detail, and the `webp` default
where they do not. Berlin's only texture is a 192-byte tiling window pattern; KTX2's
container overhead alone would exceed it, and there is no VRAM problem to solve.

Two traps, both silent:

- **Basis cannot read WebP** and skips it with a *warning*, so asking for `ktx2` on
  WebP-textured input produces a clean-looking run that encodes nothing. PLATEAU publishes
  WebP, so `optimize-tiles.sh` runs `webp-to-png.py` first rather than leaving it to be
  remembered.
- **Reading KTX2 needs a transcoder**, vendored in `public/basis/`, and `detectSupport`
  must see the real renderer. Get either wrong and materials simply have no map — the
  building renders untextured, which reads as a styling choice rather than a broken asset.
  `test/_citycheck.mjs` counts how many loaded textures are GPU-compressed for exactly
  this reason.

Encoding needs the `ktx` CLI from [KTX-Software](https://github.com/KhronosGroup/KTX-Software)
on `PATH`; without it the run fails with an unhelpful `command -v ktx` error.

### One content format

Tile content is glTF (`.glb`) everywhere. `b3dm` is the 3D Tiles 1.0 container and 1.1
carries glTF directly, so `tools/b3dm-to-glb.py` converts anything downloaded in the old
format. That is not only tidiness: `optimize-tiles.sh` finds work with `-name '*.glb'`, so
a b3dm tileset silently skipped every compression step.

The RTC offset is the part that has to survive. b3dm tiles record their true centre as
`RTC_CENTER` or the `CESIUM_RTC` extension; both are folded into a root node translation,
which is equivalent and needs no extension to read. Drop it and the geometry lands near
the centre of the earth. The batch table — per-feature IDs and usage codes — is discarded,
since nothing here reads it; `EXT_structural_metadata` is where that goes if features ever
need to be selectable.

## Hosting

Built for GitHub Pages, whose **1 GB published-site limit is the binding constraint** on
how much city you can ship. Two things follow:

- `vite.config.ts` sets `base: '/experiment-geo/'`, since project sites are served from a
  subpath. Override with `BASE_PATH=/` for other hosts.
- Deployment goes through the Pages artifact (`.github/workflows/deploy.yml`), not by
  committing `dist/`. Note that **Git LFS does not work with GitHub Pages** — it serves
  only the pointer files — so there is no way to sidestep the limit that way.

If the tilesets outgrow 1 GB, host them on Cloudflare R2 (zero egress fees) and set
`VITE_TILES_BASE`. That's the only change needed — every city resolves its layers under
that base, so one variable moves all of them.

Current usage: Berlin Mitte 40 MB, Tokyo Shinjuku 74 MB — about 12% of the limit.

## Data & licensing

**Berlin Mitte** — map data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright)
under **ODbL**, rendered with [OSM2World](https://osm2world.org/) (MIT, textures included —
though this pipeline uses almost none of them).

**Tokyo Shinjuku** — 3D City Model © [Project PLATEAU](https://www.mlit.go.jp/plateau/),
Ministry of Land, Infrastructure, Transport and Tourism, under **PDL 1.0**, which the
site policy states is CC BY 4.0 compatible. It requires the source to be named and,
separately, requires derived data to say it was modified. This build re-tiles and
re-encodes what it downloads, so the in-app credit reads "processed for this build" —
that is the second half of the licence, not politeness.

Each city's credit is shown while *its* data is on screen: the lines are per-city in
`src/cities.ts` and swap with the picker, because Berlin's OSM line is wrong over Tokyo's
data and PLATEAU's is wrong over Berlin's.

Baked geometry is a **Derivative Database** rather than a Produced Work, so the published
tileset is offered under ODbL. Rendered frames are a Produced Work, so application code
and original art — including the palette — are unaffected.

Attribution is displayed in-app without requiring interaction, as ODbL expects.

The photogrammetry tooling that remains in `tools/` targets Berlin's 3D mesh ©
[Geoportal Berlin](https://daten.berlin.de/) under
[dl-de/zero-2-0](https://www.govdata.de/dl-de/zero-2-0), which carries no restrictions.
Nothing derived from it ships in the current world.

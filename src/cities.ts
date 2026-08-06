/**
 * The worlds this build can fly over.
 *
 * A city is a baked tileset plus the things that cannot be derived from it: a
 * name, where to stand when it opens, and who to credit. The last of those is
 * the reason this is a registry rather than a list of URLs — the sources carry
 * different licences, and each one has to be shown while *its* data is on
 * screen. Berlin's OSM line is wrong for Tokyo and Tokyo's PLATEAU line is
 * wrong for Berlin.
 */

/**
 * Where tilesets are served from.
 *
 * Layout under it is `<city>/<layer>/tileset.json`, one directory per layer.
 * Berlin bakes as a single tileset carrying ground, buildings and trees
 * together, so its layer is `all`; PLATEAU splits a city into CityGML packages
 * and each arrives as its own tileset, so Tokyo has `bldg`, `tran`, `wtr` and
 * the rest. Keeping both under the same shape means a city is a list of layers
 * either way, rather than a special case per source.
 */
const TILES_BASE = import.meta.env.VITE_TILES_BASE ?? `${import.meta.env.BASE_URL}tiles/`;

/** One tileset within a city. */
export interface Layer {
  /** Directory under `<city>/`, e.g. `bldg`. */
  readonly dir: string;
  /**
   * Draw at a coarser screen-space error than the city default.
   *
   * Roads and vegetation are backdrop: they cover the whole ground plane and
   * carry far less of what you look at than the buildings do, so refining them
   * to the same tolerance spends memory where it does not show.
   */
  readonly errorTarget?: number;
}

export interface City {
  /** Stable key. Appears in the URL, so renaming one breaks shared links. */
  readonly id: string;
  readonly label: string;
  /**
   * Tilesets making up this city, drawn together.
   *
   * More than one because PLATEAU publishes a city as separate CityGML
   * packages — buildings, roads, water, vegetation — rather than one combined
   * tileset. Berlin's OSM2World bake carries all of that in a single file, so
   * it lists exactly one.
   */
  readonly layers: readonly Layer[];
  /**
   * Point to place at the world origin. Pinning it keeps the opening view over
   * the same part of the city across re-bakes, rather than drifting with the
   * bounding sphere whenever the baked area changes.
   */
  readonly anchor: { lat: number; lon: number; height?: number };
  /**
   * Credit line, as HTML, shown while this city is loaded.
   *
   * Set as innerHTML — safe because these are compile-time constants in this
   * file and never anything a user or a tileset supplies.
   */
  readonly attribution: string;
  /** Opening height, for cities whose interesting scale is not Berlin's. */
  readonly startAltitude?: number;
  /**
   * Draw a ground sheet under this city.
   *
   * Only for tilesets that contain no terrain of their own. Berlin's bake draws
   * its own ground, and a sheet there would either z-fight it or hide it.
   */
  readonly ground?: { color?: string; depth?: number; extent?: number };
  /**
   * PBR overrides applied to every material as tiles load.
   *
   * For photo-textured sources. PLATEAU's materials come in at metalness 0.5
   * and roughness 0.3 — glossy half-metal — which darkens a texture that
   * already carries its own baked lighting. Buildings are neither metallic nor
   * polished, and the photograph has done the shading, so the material should
   * be as close to plain diffuse as the renderer allows.
   */
  readonly material?: { metalness?: number; roughness?: number };
}

const OSM_CREDIT =
  'Map data © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">' +
  'OpenStreetMap contributors</a> · ODbL, rendered with ' +
  '<a href="https://osm2world.org/" target="_blank" rel="noopener">OSM2World</a>';

// PDL 1.0, which the site policy states is CC BY 4.0 compatible. It requires
// the source to be named and — separately — requires derived data to say it was
// modified. This build re-tiles and restyles what it downloads, so "processed
// for this build" is not politeness, it is the second half of the licence.
const PLATEAU_CREDIT =
  '3D City Model © <a href="https://www.mlit.go.jp/plateau/" target="_blank" rel="noopener">' +
  'Project PLATEAU</a>, MLIT Japan · PDL 1.0, processed for this build';

// Neither city is a whole city, and the ids say so. This is a 5.2 x 5.2 km box
// centred on Mitte — it takes in edges of Tiergarten, Kreuzberg, Friedrichshain
// and Moabit, but Mitte is what it is about. Calling it `berlin` implied the
// other 890 km² were in there somewhere.
const BERLIN_MITTE: City = {
  id: 'berlin-mitte',
  label: 'Berlin — Mitte',
  layers: [{ dir: 'all' }],
  anchor: { lat: 52.517, lon: 13.389 },
  attribution: OSM_CREDIT,
};

// A 1.4 km radius around Nishi-Shinjuku, so the skyscraper district and the
// station rather than the whole ward.
const TOKYO_SHINJUKU: City = {
  id: 'tokyo-shinjuku',
  label: 'Tokyo — Shinjuku',
  layers: [
    { dir: 'bldg' },
    // Backdrop layers, held coarser than the buildings on purpose.
    { dir: 'tran', errorTarget: 24 },
    // `wtr` is omitted: its two meshes land ±5 km vertically and 9 km out,
    // which no amount of water in Shinjuku explains. 92 KB of a ward that is
    // essentially waterless is not worth shipping broken.
    { dir: 'veg-cover', errorTarget: 32 },
    { dir: 'veg-trees', errorTarget: 32 },
  ],
  // Height matters here in a way it does not for Berlin, whose bake carries
  // its own ground. PLATEAU places buildings at their true elevation, so
  // without this the city hangs above the ground sheet.
  //
  // Measured from the loaded geometry rather than taken from the tileset's
  // bounding-volume regions, which claim the lowest base sits at 53 m and do
  // not agree with where the meshes actually are: anchored at 53 the median
  // building base came out 147 m *below* the sheet. The anchor is whatever
  // puts that median at zero.
  anchor: { lat: 35.6898, lon: 139.696, height: -94 },
  attribution: PLATEAU_CREDIT,
  material: { metalness: 0, roughness: 1 },
  // PLATEAU has no terrain relief for Shinjuku — there is no `dem` package in
  // the catalogue at all — and `tran` covers only the carriageways, so the
  // blocks between them would still be sky. The sheet fills those in under
  // everything else.
  ground: { color: '#8a8f83', depth: 0, extent: 13_000 },
};

// Typed as non-empty so DEFAULT_CITY does not need a runtime check for a case
// the source rules out.
export const CITIES: readonly [City, ...City[]] = [BERLIN_MITTE, TOKYO_SHINJUKU];

export const DEFAULT_CITY = BERLIN_MITTE;

/** The city named by `?city=`, or the default when it names nothing known. */
export function cityFromLocation(search = window.location.search): City {
  const want = new URLSearchParams(search).get('city');
  return CITIES.find((c) => c.id === want) ?? DEFAULT_CITY;
}

/**
 * Record the current city in the URL without adding a history entry.
 *
 * replaceState rather than pushState: switching city is changing a view, not
 * navigating, and stacking entries would make Back walk through every city the
 * user tried instead of leaving the page.
 */
/** Absolute tileset URL for one of a city's layers. */
export function layerUrl(city: City, layer: Layer): string {
  return `${TILES_BASE}${city.id}/${layer.dir}/tileset.json`;
}

export function rememberCity(city: City): void {
  const url = new URL(window.location.href);
  if (city.id === DEFAULT_CITY.id) url.searchParams.delete('city');
  else url.searchParams.set('city', city.id);
  window.history.replaceState(null, '', url);
}

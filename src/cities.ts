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

/** Where tilesets are served from. */
const TILES_BASE = import.meta.env.VITE_TILES_BASE ?? `${import.meta.env.BASE_URL}tiles/`;

export interface City {
  /** Stable key. Appears in the URL, so renaming one breaks shared links. */
  readonly id: string;
  readonly label: string;
  readonly url: string;
  /**
   * Point to place at the world origin. Pinning it keeps the opening view over
   * the same part of the city across re-bakes, rather than drifting with the
   * bounding sphere whenever the baked area changes.
   */
  readonly anchor: { lat: number; lon: number };
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
  readonly ground?: { color?: string; depth?: number };
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

const BERLIN: City = {
  id: 'berlin',
  label: 'Berlin — Mitte',
  url: `${TILES_BASE}berlin/tileset.json`,
  anchor: { lat: 52.517, lon: 13.389 },
  attribution: OSM_CREDIT,
};

const SHINJUKU: City = {
  id: 'shinjuku',
  label: 'Tokyo — Shinjuku',
  url: `${TILES_BASE}shinjuku/tileset.json`,
  anchor: { lat: 35.6898, lon: 139.696 },
  attribution: PLATEAU_CREDIT,
  // PLATEAU ships buildings without terrain, so this supplies the missing
  // ground. Shinjuku's is a little under the ellipsoid the anchor sits on.
  ground: { color: '#8a8f83', depth: 4 },
};

// Typed as non-empty so DEFAULT_CITY does not need a runtime check for a case
// the source rules out.
export const CITIES: readonly [City, ...City[]] = [BERLIN, SHINJUKU];

export const DEFAULT_CITY = BERLIN;

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
export function rememberCity(city: City): void {
  const url = new URL(window.location.href);
  if (city.id === DEFAULT_CITY.id) url.searchParams.delete('city');
  else url.searchParams.set('city', city.id);
  window.history.replaceState(null, '', url);
}

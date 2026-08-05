/**
 * Where the baked 3D Tiles live.
 *
 * Defaults to a path under the site's own base URL, so a plain
 * `npm run build` produces something GitHub Pages can serve. Point
 * `VITE_TILESET_URL` at Cloudflare R2 (or any CORS-enabled bucket) if the
 * tileset outgrows the 1 GB Pages limit — that is the whole reason this is a
 * variable rather than a constant.
 */
export const TILESET_URL =
  import.meta.env.VITE_TILESET_URL ?? `${import.meta.env.BASE_URL}tiles/berlin-hybrid/tileset.json`;

/** Screen-space error target in pixels. Lower = more detail, more memory. */
export const ERROR_TARGET = Number(import.meta.env.VITE_ERROR_TARGET ?? 12);

/** Starting camera height above the tileset centre, in metres. */
export const START_ALTITUDE = 700;

/**
 * Optional point to place at the world origin, as "lat,lon".
 *
 * Needed when the tileset is a partial subtree of a city-wide one (see
 * tools/fetch-3dtiles.py) — otherwise it re-centres on its own bounding
 * sphere, which is the middle of the whole city rather than the part that was
 * actually downloaded.
 */
// Berlin Mitte — the centre of the downloaded subtree.
const anchorEnv = import.meta.env.VITE_TILESET_ANCHOR ?? '52.5170,13.3889';
export const TILESET_ANCHOR = anchorEnv
  ? (([lat, lon]) => ({ lat, lon }))(anchorEnv.split(',').map(Number) as [number, number])
  : undefined;

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
  import.meta.env.VITE_TILESET_URL ?? `${import.meta.env.BASE_URL}tiles/berlin/tileset.json`;

/** Screen-space error target in pixels. Lower = more detail, more memory. */
export const ERROR_TARGET = Number(import.meta.env.VITE_ERROR_TARGET ?? 12);

/** Starting camera height above the tileset centre, in metres. */
export const START_ALTITUDE = 700;

/**
 * Optional point to place at the world origin, as "lat,lon".
 *
 * The bake's own bounds are correct, so this is no longer load-bearing the way
 * it was for a partial subtree of a city-wide tileset — but pinning it keeps
 * the opening view over the same part of the city across re-bakes, rather than
 * drifting with the bounding sphere whenever the baked area changes.
 */
// Berlin Mitte — the centre of the baked area.
const anchorEnv = import.meta.env.VITE_TILESET_ANCHOR ?? '52.5170,13.3890';
export const TILESET_ANCHOR = anchorEnv
  ? (([lat, lon]) => ({ lat, lon }))(anchorEnv.split(',').map(Number) as [number, number])
  : undefined;

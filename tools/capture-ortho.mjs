/**
 * Render the photogrammetric tileset straight down into a georeferenced colour
 * raster — an orthophoto of whatever the world currently holds.
 *
 * Why this exists
 * ---------------
 * OSM2World gives clean, readable geometry: boxes with crisp edges and defined
 * roof planes. Photogrammetry gives real colour but melted geometry. This takes
 * the colour off the photogrammetry so it can be projected onto the clean
 * geometry, which is the combination that actually reads as a stylised city:
 * recognisable buildings, real roof colours, none of the blobby reconstruction.
 *
 * Rendering it rather than rasterising the meshes in Python is deliberate — the
 * renderer already handles the tile transforms, LOD selection and occlusion
 * correctly, and getting any of those subtly wrong in a reimplementation would
 * be invisible until the colours landed on the wrong buildings.
 *
 * Usage:
 *   CHROMIUM_PATH=... node tools/capture-ortho.mjs [--size 4096] [--extent 1400]
 *
 * Writes ortho.png plus ortho.json holding the world-space bounds the image
 * covers, which is what the projection step needs to map geometry to pixels.
 */

import fs from 'node:fs';
import { chromium } from 'playwright';

const args = process.argv.slice(2);
const opt = (name, fallback) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 ? Number(args[i + 1]) : fallback;
};

const SIZE = opt('size', 4096); // output pixels, square
const EXTENT = opt('extent', 1400); // metres covered, square, centred on the anchor
const URL = args.find((a) => a.startsWith('http')) ?? 'http://localhost:4173/experiment-geo/';
const OUT = 'ortho.png';

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH,
  args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--no-sandbox'],
});
// Render at the target resolution directly; downscaling a small capture would
// throw away exactly the roof detail this exists to capture.
const page = await browser.newPage({ viewport: { width: SIZE, height: SIZE } });
await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 120000 });
await page.waitForTimeout(15000);

const info = await page.evaluate(
  ({ extent }) => {
    const { viewer, world, THREE } = window.app;
    // Record where the world origin actually is — the projection step needs it
    // to bring georeferenced geometry back into this same local frame.
    const anchor = window.__anchor ?? null;

    // Straight down, orthographic, centred on the world origin (which the
    // anchor already placed at the target lat/lon).
    const half = extent / 2;
    const cam = new THREE.OrthographicCamera(-half, half, half, -half, 1, 20000);
    cam.position.set(0, 3000, 0);
    cam.lookAt(0, 0, 0);
    cam.updateProjectionMatrix();
    cam.updateMatrixWorld(true);

    // Flat lighting: this is a colour capture, not a beauty render. Baked
    // shading would be projected onto geometry that gets lit again later.
    viewer.scene.background = null;
    viewer.scene.fog = null;
    viewer.scene.environmentIntensity = 0;
    for (const o of [...viewer.scene.children]) {
      if (o.isLight) viewer.scene.remove(o);
    }
    viewer.scene.add(new THREE.AmbientLight(0xffffff, Math.PI));

    window.__ortho = { cam, extent };
    // Drive the tile selection from the ortho camera so it loads what this
    // view needs rather than what the fly camera was looking at.
    world.tiles.deleteCamera(viewer.camera);
    world.tiles.setCamera(cam);
    world.tiles.setResolution(cam, window.innerWidth, window.innerHeight);
    world.tiles.errorTarget = 1;
    return { extent, half, anchor };
  },
  { extent: EXTENT },
);

// Streaming this much detail under software rendering is slow; poll until the
// visible-tile count stops climbing rather than guessing a fixed wait.
let stable = 0;
let previous = -1;
for (let i = 0; i < 90; i++) {
  await page.waitForTimeout(4000);
  const n = await page.evaluate(() => {
    const { viewer, world } = window.app;
    world.tiles.setResolution(window.__ortho.cam, window.innerWidth, window.innerHeight);
    world.tiles.update();
    viewer.renderer.render(viewer.scene, window.__ortho.cam);
    return world.tiles.visibleTiles.size;
  });
  stable = n === previous ? stable + 1 : 0;
  previous = n;
  process.stdout.write(`\r  tiles ${n}, stable ${stable}/4`);
  if (stable >= 4 && n > 0) break;
}
console.log();

// Final render, then read the canvas rather than screenshotting the page — the
// page has a HUD over it.
const dataUrl = await page.evaluate(() => {
  const { viewer } = window.app;
  viewer.renderer.render(viewer.scene, window.__ortho.cam);
  return viewer.renderer.domElement.toDataURL('image/png');
});
fs.writeFileSync(OUT, Buffer.from(dataUrl.split(',')[1], 'base64'));

// The bounds the image covers, in the same local metres the geometry uses.
// three.js is Y-up with north at -z, so image +v runs north.
fs.writeFileSync(
  'ortho.json',
  JSON.stringify(
    {
      size: SIZE,
      extentMetres: EXTENT,
      metresPerPixel: EXTENT / SIZE,
      anchor: info.anchor,
      bounds: { minX: -info.half, maxX: info.half, minZ: -info.half, maxZ: info.half },
      note: 'x maps to image u left-to-right; z maps to image v top-to-bottom (north is -z, so v increases southward)',
    },
    null,
    2,
  ),
);

console.log(`Wrote ${OUT} (${SIZE}x${SIZE}, ${EXTENT} m, ${(EXTENT / SIZE).toFixed(2)} m/px)`);
await browser.close();

/**
 * End-to-end smoke test: does the app load the baked tileset and render it?
 *
 * Checks correctness, not performance. CI has no GPU, so Chromium falls back
 * to SwiftShader software rendering and frame rates measured here are
 * meaningless — judge feel and frame budget on real hardware.
 *
 * Usage:
 *   npm run build
 *   npx vite preview --port 4173 &
 *   CHROMIUM_PATH=/path/to/chrome node test/smoke.mjs [url] [--screenshot out.png]
 */

import { chromium } from 'playwright';

const args = process.argv.slice(2);
const url = args.find((a) => a.startsWith('http')) ?? 'http://localhost:4173/experiment-geo/';
const shotIndex = args.indexOf('--screenshot');
const screenshot = shotIndex >= 0 ? args[shotIndex + 1] : null;

// Software rendering needs a generous budget to stream and draw a few tiles.
const SETTLE_MS = Number(process.env.SMOKE_SETTLE_MS ?? 25_000);

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH,
  args: [
    '--use-gl=angle',
    '--use-angle=swiftshader',
    '--enable-unsafe-swiftshader',
    '--no-sandbox',
  ],
});

const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });

const errors = [];
const failedRequests = [];
page.on('console', (m) => {
  if (m.type() === 'error') errors.push(m.text());
});
page.on('pageerror', (e) => errors.push(String(e)));
page.on('response', (r) => {
  // A missing favicon is noise, not a failure.
  if (r.status() >= 400 && !r.url().endsWith('favicon.ico')) {
    failedRequests.push(`${r.status()} ${r.url()}`);
  }
});

let state = null;
try {
  await page.goto(url, { waitUntil: 'networkidle', timeout: 60_000 });
  await page.waitForTimeout(SETTLE_MS);
  state = await page.evaluate(() => {
    const app = window.app;
    if (!app) return null;
    const info = app.viewer.renderer.info;
    return {
      triangles: info.render.triangles,
      drawCalls: info.render.calls,
      textures: info.memory.textures,
      visibleTiles: app.worlds[0].tiles.visibleTiles.size,
      rootLoaded: Boolean(app.worlds[0].tiles.rootTileset),
    };
  });
  if (screenshot) await page.screenshot({ path: screenshot });
} finally {
  await browser.close();
}

console.log('state:', JSON.stringify(state));
if (errors.length) console.log('console errors:', errors.slice(0, 5));
if (failedRequests.length) console.log('failed requests:', failedRequests.slice(0, 5));

const ok =
  state !== null &&
  state.rootLoaded &&
  state.triangles > 0 &&
  state.visibleTiles > 0 &&
  failedRequests.length === 0;

console.log(ok ? 'PASS' : 'FAIL');
process.exit(ok ? 0 : 1);

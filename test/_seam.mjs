// Close-up look at a tile seam. The default overview camera sits at 420 m,
// where a 1.5 m gap is sub-pixel — the defect only shows from street level.
import fs from 'node:fs';
import { chromium } from 'playwright';

// Metres east of the anchor. The x=17602|17603 boundary sits at lon 13.3923,
// and the anchor is 13.3890, so the seam is ~224 m east of the origin.
const SEAM_EAST = Number(process.env.SEAM_EAST ?? 224);
const ALT = Number(process.env.SEAM_ALT ?? 70);
const OUT = process.env.SEAM_OUT ?? 'seam.png';

const b = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH,
  args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--no-sandbox'],
});
const p = await b.newPage({ viewport: { width: 900, height: 900 } });
const bad = [];
p.on('pageerror', (e) => bad.push(String(e).slice(0, 150)));

await p.goto('http://localhost:4173/experiment-geo/', {
  waitUntil: 'domcontentloaded',
  timeout: 120000,
});
await p.waitForTimeout(30000);

await p.evaluate(
  ({ east, alt }) => {
    const { viewer } = window.app;
    // Look straight down the seam from just above roof height.
    viewer.camera.position.set(east, alt, 90);
    viewer.camera.lookAt(east, 0, -260);
    viewer.camera.updateMatrixWorld(true);
  },
  { east: SEAM_EAST, alt: ALT },
);
await p.waitForTimeout(25000);

const dataUrl = await p.evaluate(() => {
  const { viewer } = window.app;
  viewer.render();
  return viewer.renderer.domElement.toDataURL('image/png');
});
fs.writeFileSync(OUT, Buffer.from(dataUrl.split(',')[1], 'base64'));
console.log('wrote', OUT, '| problems:', bad.length ? bad.slice(0, 3) : 'none');
await b.close();

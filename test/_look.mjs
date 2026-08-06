/** Render a specific spot, given as metres east/north of the world anchor. */
import fs from 'node:fs';
import { chromium } from 'playwright';
const E = Number(process.env.LOOK_E ?? 0);
const N = Number(process.env.LOOK_N ?? 0);
const ALT = Number(process.env.LOOK_ALT ?? 250);
const OUT = process.env.LOOK_OUT ?? 'look.png';
const b = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH,
  args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--no-sandbox'],
});
const p = await b.newPage({ viewport: { width: 900, height: 900 } });
await p.goto('http://localhost:4173/experiment-geo/', { waitUntil: 'domcontentloaded', timeout: 120000 });
await p.waitForTimeout(35000);
await p.evaluate(({ e, n, alt }) => {
  const { viewer } = window.app;
  viewer.camera.position.set(e, alt, -n + alt * 0.8);
  viewer.camera.lookAt(e, 0, -n);
  viewer.camera.updateMatrixWorld(true);
}, { e: E, n: N, alt: ALT });
await p.waitForTimeout(25000);
const d = await p.evaluate(() => {
  const { viewer } = window.app;
  viewer.render();
  return viewer.renderer.domElement.toDataURL('image/png');
});
fs.writeFileSync(OUT, Buffer.from(d.split(',')[1], 'base64'));
console.log('wrote', OUT);
await b.close();

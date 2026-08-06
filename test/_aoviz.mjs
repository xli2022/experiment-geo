/**
 * Look at what ambient occlusion is actually drawing.
 *
 * `_ao.mjs` counts how many pixels AO darkens, which cannot distinguish
 * "occlusion in the right places" from "per-pixel noise across the whole
 * frame" — both darken a lot of pixels. This writes the AO term out as an
 * image instead, so the shape of it is visible: contact shading follows walls
 * and eaves, whereas a broken depth reconstruction looks like static.
 */
import fs from 'node:fs';
import { chromium } from 'playwright';

const ALT = Number(process.env.AO_ALT ?? 55);
const OUT = process.env.AO_OUT ?? 'ao';

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH,
  args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: 700, height: 700 } });
await page.goto('http://localhost:4173/experiment-geo/', {
  waitUntil: 'domcontentloaded',
  timeout: 120000,
});
await page.waitForTimeout(35000);

const shots = await page.evaluate((alt) => {
  const { viewer } = window.app;
  viewer.camera.position.set(0, alt, 130);
  viewer.camera.lookAt(0, 0, -180);
  viewer.camera.updateMatrixWorld(true);

  const grab = () => {
    const c = document.createElement('canvas');
    c.width = viewer.renderer.domElement.width;
    c.height = viewer.renderer.domElement.height;
    const g = c.getContext('2d');
    g.drawImage(viewer.renderer.domElement, 0, 0);
    return { data: g.getImageData(0, 0, c.width, c.height), url: c.toDataURL('image/png') };
  };

  viewer.render(); // composer, AO on
  const on = grab();
  viewer.renderer.render(viewer.scene, viewer.camera); // forward, AO off
  const off = grab();

  // The AO term, isolated: white where AO left the frame alone, black where it
  // removed the most light. Amplified 3x so a subtle-but-correct effect is
  // still legible rather than washing out to flat white.
  const c = document.createElement('canvas');
  c.width = on.data.width;
  c.height = on.data.height;
  const g = c.getContext('2d');
  const diff = g.createImageData(c.width, c.height);
  const a = on.data.data;
  const b = off.data.data;
  let sumAbs = 0;
  // High-frequency energy: how much each pixel's delta differs from its right
  // neighbour. Contact shading is smooth, so this stays low; static does not.
  let hf = 0;
  let n = 0;
  const deltas = new Float32Array(c.width * c.height);
  for (let i = 0, p = 0; i < a.length; i += 4, p++) {
    const d = (a[i] + a[i + 1] + a[i + 2] - b[i] - b[i + 1] - b[i + 2]) / 3;
    deltas[p] = d;
    sumAbs += Math.abs(d);
    n++;
    const v = Math.max(0, Math.min(255, 255 + d * 3));
    diff.data[i] = diff.data[i + 1] = diff.data[i + 2] = v;
    diff.data[i + 3] = 255;
  }
  for (let y = 0; y < c.height; y++) {
    for (let x = 0; x < c.width - 1; x++) {
      const p = y * c.width + x;
      hf += Math.abs(deltas[p] - deltas[p + 1]);
    }
  }
  g.putImageData(diff, 0, 0);

  return {
    on: on.url,
    off: off.url,
    diff: c.toDataURL('image/png'),
    meanAbs: sumAbs / n,
    meanHf: hf / n,
  };
}, ALT);

for (const [k, v] of Object.entries(shots)) {
  if (typeof v === 'string') fs.writeFileSync(`${OUT}-${k}.png`, Buffer.from(v.split(',')[1], 'base64'));
}
console.log(
  `AO mean |delta| ${shots.meanAbs.toFixed(2)}/255, high-frequency energy ${shots.meanHf.toFixed(2)}`,
);
await browser.close();

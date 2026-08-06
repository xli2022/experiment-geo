/**
 * Render one spot several ways, so an artifact can be attributed rather than
 * guessed at.
 *
 * Writes the frame as shipped (through the composer), the same frame forward-
 * only, and a z-fight mask built from a 2 mm camera nudge. A pattern present in
 * all three is geometry; one that vanishes forward-only is post-processing.
 *
 * Position is metres east/north of the world anchor; heading is degrees
 * clockwise from north, pitch degrees below horizontal.
 */
import fs from 'node:fs';
import { chromium } from 'playwright';

const view = {
  e: Number(process.env.P_E ?? 0),
  n: Number(process.env.P_N ?? 0),
  alt: Number(process.env.P_ALT ?? 211),
  heading: Number(process.env.P_HEADING ?? 0),
  pitch: Number(process.env.P_PITCH ?? 50),
};
const OUT = process.env.P_OUT ?? 'probe';
const W = Number(process.env.P_W ?? 640);
const H = Number(process.env.P_H ?? 1040);

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH,
  args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: W, height: H } });

// World axes: +x east, +z south, so heading 0 looks north.
await page.addInitScript(() => {
  window.__aim = ({ e, n, alt, heading, pitch }, nudge = 0) => {
    const { viewer, THREE } = window.app;
    const yaw = THREE.MathUtils.degToRad(heading);
    const p = THREE.MathUtils.degToRad(pitch);
    const dir = new THREE.Vector3(
      Math.sin(yaw) * Math.cos(p),
      -Math.sin(p),
      -Math.cos(yaw) * Math.cos(p),
    );
    viewer.camera.position.set(e, alt + nudge, -n + nudge);
    viewer.camera.lookAt(viewer.camera.position.clone().add(dir));
    viewer.camera.updateMatrixWorld(true);
  };
});

await page.goto('http://localhost:4173/experiment-geo/', {
  waitUntil: 'domcontentloaded',
  timeout: 120000,
});
await page.waitForTimeout(30000);
await page.evaluate((v) => window.__aim(v), view);
await page.waitForTimeout(25000);

const shots = await page.evaluate((v) => {
  const { viewer } = window.app;
  const grab = () => {
    const c = document.createElement('canvas');
    c.width = viewer.renderer.domElement.width;
    c.height = viewer.renderer.domElement.height;
    const g = c.getContext('2d');
    g.drawImage(viewer.renderer.domElement, 0, 0);
    return g.getImageData(0, 0, c.width, c.height);
  };
  const toUrl = (img) => {
    const c = document.createElement('canvas');
    c.width = img.width;
    c.height = img.height;
    c.getContext('2d').putImageData(img, 0, 0);
    return c.toDataURL('image/png');
  };

  window.__aim(v);
  viewer.render();
  const composed = grab();
  viewer.renderer.render(viewer.scene, viewer.camera);
  const forward = grab();

  window.__aim(v, 0.002);
  viewer.renderer.render(viewer.scene, viewer.camera);
  const forwardB = grab();
  window.__aim(v);

  // Depth ties flip between two unrelated colours under a sub-pixel move;
  // correctly ordered surfaces shift by far less than one pixel.
  const mask = new ImageData(forward.width, forward.height);
  let changed = 0;
  for (let i = 0; i < forward.data.length; i += 4) {
    const d =
      Math.abs(forward.data[i] - forwardB.data[i]) +
      Math.abs(forward.data[i + 1] - forwardB.data[i + 1]) +
      Math.abs(forward.data[i + 2] - forwardB.data[i + 2]);
    if (d > 24) {
      changed++;
      mask.data[i] = 255;
      mask.data[i + 1] = 0;
      mask.data[i + 2] = 0;
    } else {
      mask.data[i] = forward.data[i] * 0.35;
      mask.data[i + 1] = forward.data[i + 1] * 0.35;
      mask.data[i + 2] = forward.data[i + 2] * 0.35;
    }
    mask.data[i + 3] = 255;
  }

  return {
    composed: toUrl(composed),
    forward: toUrl(forward),
    mask: toUrl(mask),
    changed,
    total: forward.width * forward.height,
  };
}, view);

for (const [k, v] of Object.entries(shots)) {
  if (typeof v === 'string') fs.writeFileSync(`${OUT}-${k}.png`, Buffer.from(v.split(',')[1], 'base64'));
}
console.log(
  `${OUT}: z-fight pixels ${shots.changed} / ${shots.total} (${((100 * shots.changed) / shots.total).toFixed(2)}%)`,
);
await browser.close();

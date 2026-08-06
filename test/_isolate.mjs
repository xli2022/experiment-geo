/**
 * Render one view four ways, to attribute a screen artifact to its cause.
 *
 * A stipple on a large flat surface has three plausible causes and they look
 * alike in a still: coplanar geometry, shadow acne, and screen-space ambient
 * occlusion. Turning each off in turn separates them — whatever survives all
 * three is in the geometry, and the rest names itself.
 *
 * Position is metres east/north of the world anchor; heading is degrees
 * clockwise from north, pitch degrees below horizontal.
 */
import fs from 'node:fs';
import { chromium } from 'playwright';

const view = {
  e: Number(process.env.I_E ?? 0),
  n: Number(process.env.I_N ?? 0),
  alt: Number(process.env.I_ALT ?? 200),
  heading: Number(process.env.I_HEADING ?? 0),
  pitch: Number(process.env.I_PITCH ?? 45),
};
const OUT = process.env.I_OUT ?? 'isolate';
const W = Number(process.env.I_W ?? 900);
const H = Number(process.env.I_H ?? 600);

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH,
  args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: W, height: H } });

// World axes: +x east, +z south, so heading 0 looks north.
await page.addInitScript(() => {
  window.__aim = ({ e, n, alt, heading, pitch }) => {
    const { viewer, THREE } = window.app;
    const yaw = THREE.MathUtils.degToRad(heading);
    const p = THREE.MathUtils.degToRad(pitch);
    viewer.camera.position.set(e, alt, -n);
    viewer.camera.lookAt(
      e + Math.sin(yaw) * Math.cos(p),
      alt - Math.sin(p),
      -n - Math.cos(yaw) * Math.cos(p),
    );
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
    c.getContext('2d').drawImage(viewer.renderer.domElement, 0, 0);
    return c.toDataURL('image/png');
  };
  const setShadows = (on) => {
    viewer.renderer.shadowMap.enabled = on;
    viewer.scene.traverse((o) => {
      if (o.isMesh && o.material) o.material.needsUpdate = true;
    });
  };

  window.__aim(v);
  const out = {};
  out.shipped = (viewer.render(), grab());
  out.noAo = (viewer.renderer.render(viewer.scene, viewer.camera), grab());
  setShadows(false);
  out.noShadow = (viewer.render(), grab());
  out.neither = (viewer.renderer.render(viewer.scene, viewer.camera), grab());
  setShadows(true);
  return out;
}, view);

for (const [k, v] of Object.entries(shots)) {
  fs.writeFileSync(`${OUT}-${k}.png`, Buffer.from(v.split(',')[1], 'base64'));
}
console.log(`wrote ${Object.keys(shots).length} views to ${OUT}-*.png`);
await browser.close();

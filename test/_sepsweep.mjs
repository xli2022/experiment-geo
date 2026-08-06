/**
 * Measure how much vertical separation this scene actually needs.
 *
 * The offset lattice in offset-ground.py picks its step size from the Draco
 * grid, on the reasoning that anything clearing the grid is separated. That is
 * a statement about the geometry, not about what survives to the screen: the
 * depth buffer, float32 in the vertex shader, and the grazing angle a roof is
 * seen at all erode it further. This sweeps the separation on the real meshes
 * and finds where the fighting actually stops.
 *
 * Fighting is measured the reliable way rather than by eye: jitter the camera a
 * centimetre and count pixels that flip. Stable geometry does not flip.
 */
import { chromium } from 'playwright';

const view = {
  e: Number(process.env.S_E ?? 985),
  n: Number(process.env.S_N ?? -25),
  alt: Number(process.env.S_ALT ?? 150),
  heading: Number(process.env.S_HEADING ?? 318),
  pitch: Number(process.env.S_PITCH ?? 36),
};
// Millimetres of extra lift applied to every mesh carrying TARGET.
const STEPS = (process.env.S_STEPS ?? '0,0.5,1,2,4,8,16,32').split(',').map(Number);
const TARGET = (process.env.S_TARGET ?? '#2a2e38').toLowerCase();

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH,
  args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: 480, height: 260 } });

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
  // Collect every mesh whose material carries the target colour, once, so the
  // sweep moves the same set at every step.
  // The palette lives in vertex colours, not material colours — every material
  // is white. So the surfaces have to be found per-vertex, and moved the same
  // way offset-ground.py moves them: by adding to Y in the GLB's own frame.
  window.__collect = (hex) => {
    const { world } = window.app;
    // find-coplanar.py prints raw COLOR_0 bytes, and glTF defines COLOR_0 as
    // linear — so these hex codes are linear values, not sRGB. Compare them the
    // same way rather than round-tripping through THREE.Color, which would
    // convert and miss.
    const want = {
      r: parseInt(hex.slice(1, 3), 16) / 255,
      g: parseInt(hex.slice(3, 5), 16) / 255,
      b: parseInt(hex.slice(5, 7), 16) / 255,
    };
    const hits = [];
    world.tiles.group.traverse((o) => {
      if (!o.isMesh) return;
      const col = o.geometry.attributes.color;
      const pos = o.geometry.attributes.position;
      if (!col || !pos) return;
      const idx = [];
      for (let i = 0; i < col.count; i++) {
        if (
          Math.abs(col.getX(i) - want.r) < 0.004 &&
          Math.abs(col.getY(i) - want.g) < 0.004 &&
          Math.abs(col.getZ(i) - want.b) < 0.004
        )
          idx.push(i);
      }
      if (idx.length) hits.push({ mesh: o, idx, base: idx.map((i) => pos.getY(i)) });
    });
    window.__hits = hits;
    return hits.reduce((n, h) => n + h.idx.length, 0);
  };
  window.__lift = (mm) => {
    for (const { mesh, idx, base } of window.__hits) {
      const pos = mesh.geometry.attributes.position;
      idx.forEach((v, k) => pos.setY(v, base[k] + mm / 1000));
      pos.needsUpdate = true;
      mesh.geometry.computeBoundingSphere();
    }
  };
  // Freeze the streaming before measuring. The tiles renderer keeps choosing
  // LODs while the loop runs, and a tile swapping between the two grabs changes
  // most of the frame — which reads as fighting and swamps the real signal.
  window.__freeze = () => window.app.viewer.stop();
  window.__grab = () => {
    const { viewer } = window.app;
    viewer.render();
    return viewer.renderer.domElement.toDataURL('image/png');
  };
});

await page.goto('http://localhost:4173/experiment-geo/', {
  waitUntil: 'domcontentloaded',
  timeout: 120000,
});
await page.waitForTimeout(30000);
await page.evaluate((v) => window.__aim(v), view);
await page.waitForTimeout(25000);

await page.evaluate(() => window.__freeze());
const found = await page.evaluate((t) => window.__collect(t), TARGET);
console.log(`vertices carrying ${TARGET}: ${found}`);
if (!found) {
  console.log('nothing to sweep — colour not present in the loaded tiles');
  await browser.close();
  process.exit(1);
}

const decode = (dataUrl) => Buffer.from(dataUrl.split(',')[1], 'base64');

console.log('\n  lift    flipped px   (pixels changing >30 under a 1 cm camera jitter)');
const results = [];
for (const mm of STEPS) {
  await page.evaluate((v) => window.__lift(v), mm);
  await page.evaluate((v) => window.__aim(v), view);
  const a = decode(await page.evaluate(() => window.__grab()));
  await page.evaluate((v) => window.__aim({ ...v, e: v.e + 0.01 }), view);
  const b = decode(await page.evaluate(() => window.__grab()));
  results.push({ mm, a, b });
}

await browser.close();

// Compare outside the browser so the PNGs decode with a real decoder.
const { PNG } = await import('pngjs');
for (const { mm, a, b } of results) {
  const pa = PNG.sync.read(a);
  const pb = PNG.sync.read(b);
  let flips = 0;
  for (let i = 0; i < pa.data.length; i += 4) {
    const d = Math.max(
      Math.abs(pa.data[i] - pb.data[i]),
      Math.abs(pa.data[i + 1] - pb.data[i + 1]),
      Math.abs(pa.data[i + 2] - pb.data[i + 2]),
    );
    if (d > 30) flips++;
  }
  console.log(`  ${String(mm).padStart(5)} mm  ${String(flips).padStart(9)}`);
}

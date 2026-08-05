/**
 * Is ambient occlusion actually doing anything?
 *
 * GTAO reconstructs position from depth, and this renderer runs with
 * `logarithmicDepthBuffer`, which is a known-awkward combination — the effect
 * can silently produce nothing, or garbage, and a still frame will not tell you
 * which. So render the same view through the composer and forward-only and
 * compare: AO should darken contact regions and leave everything else alone.
 */
import { chromium } from 'playwright';
const b = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH,
  args: ['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader','--no-sandbox'] });
const p = await b.newPage({ viewport: { width: 600, height: 600 } });
await p.goto('http://localhost:4173/experiment-geo/', { waitUntil: 'domcontentloaded', timeout: 120000 });
await p.waitForTimeout(35000);
const r = await p.evaluate(() => {
  const { viewer } = window.app;
  viewer.camera.position.set(0, 55, 130);
  viewer.camera.lookAt(0, 0, -180);
  viewer.camera.updateMatrixWorld(true);
  const grab = () => {
    const c = document.createElement('canvas');
    c.width = viewer.renderer.domElement.width; c.height = viewer.renderer.domElement.height;
    c.getContext('2d').drawImage(viewer.renderer.domElement, 0, 0);
    return c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
  };
  viewer.render();                                    // composer: AO on
  const withAO = grab();
  viewer.renderer.render(viewer.scene, viewer.camera); // forward: AO off
  const noAO = grab();
  let darker = 0, lighter = 0, sum = 0, n = 0;
  for (let i = 0; i < withAO.length; i += 4) {
    const a = withAO[i] + withAO[i+1] + withAO[i+2];
    const c = noAO[i] + noAO[i+1] + noAO[i+2];
    if (a < c - 12) darker++; else if (a > c + 12) lighter++;
    sum += a - c; n++;
  }
  return { darker, lighter, meanDelta: sum / n / 3, total: n };
});
console.log(JSON.stringify(r));
await b.close();

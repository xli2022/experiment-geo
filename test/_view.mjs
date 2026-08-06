/**
 * Screenshot a city from a given camera, for looking at rather than measuring.
 *
 * _isolate.mjs renders one view four ways to attribute an artifact; this just
 * takes the picture. It also accepts a city, which _isolate.mjs does not —
 * that one predates there being more than one world to point at.
 *
 * Positions are metres east/north of the city's anchor, heading degrees
 * clockwise from north, pitch degrees below horizontal.
 */
import { chromium } from 'playwright';

const CITY = process.env.V_CITY ?? 'berlin';
const OUT = process.env.V_OUT ?? 'view';
const W = Number(process.env.V_W ?? 1000);
const H = Number(process.env.V_H ?? 560);
const SETTLE = Number(process.env.V_SETTLE ?? 40000);

// "label:e,n,alt,heading,pitch; label:..."
const SHOTS = (process.env.V_SHOTS ?? 'default:0,0,400,0,35').split(';').map((spec) => {
  const [label, nums] = spec.split(':');
  const [e, n, alt, heading, pitch] = nums.split(',').map(Number);
  return { label: label.trim(), e, n, alt, heading, pitch };
});

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH,
  args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: W, height: H } });

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

await page.goto(`http://localhost:4173/experiment-geo/?city=${CITY}`, {
  waitUntil: 'domcontentloaded',
  timeout: 120000,
});
await page.waitForTimeout(SETTLE);

for (const shot of SHOTS) {
  await page.evaluate((v) => window.__aim(v), shot);
  // The tiles renderer picks LODs from where the camera is, so a fresh angle
  // needs time to stream in before it is worth photographing.
  await page.waitForTimeout(SETTLE);
  await page.evaluate((v) => window.__aim(v), shot);
  await page.waitForTimeout(4000);
  await page.screenshot({ path: `${OUT}-${shot.label}.png` });
  const tiles = await page.evaluate(() => window.app?.world?.tiles?.visibleTiles?.size ?? 0);
  console.log(`${shot.label}: ${tiles} visible tiles -> ${OUT}-${shot.label}.png`);
}

await browser.close();

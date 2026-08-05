/**
 * Drives the touch controls in an emulated mobile browser.
 *
 * The stick and look zones are only reachable through real multi-touch events,
 * so this uses CDP's Input.dispatchTouchEvent directly — Playwright's tap()
 * sends one touch at a time and cannot express "thumb on the stick while the
 * other thumb drags to look", which is the case most likely to break.
 *
 * Usage:
 *   CHROMIUM_PATH=... node test/touch.mjs [url]
 */

import { chromium, devices } from 'playwright';

const url = process.argv[2] ?? 'http://localhost:4173/experiment-geo/';

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH,
  args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--no-sandbox'],
});
const context = await browser.newContext({ ...devices['Pixel 7'] });
const page = await context.newPage();

const errors = [];
page.on('pageerror', (e) => errors.push(String(e).slice(0, 160)));
page.on('console', (m) => {
  if (m.type() === 'error' && !m.text().includes('404')) errors.push(m.text().slice(0, 160));
});

await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 90000 });
await page.waitForTimeout(15000);

const cdp = await context.newCDPSession(page);
const touch = (type, points) =>
  cdp.send('Input.dispatchTouchEvent', {
    type,
    touchPoints: points.map((p) => ({ x: p.x, y: p.y, id: p.id })),
  });

const size = page.viewportSize();
const results = {};

results.controlsPresent = await page.evaluate(() => ({
  stick: Boolean(document.querySelector('.touch-stick')),
  buttons: document.querySelectorAll('.touch-btn').length,
}));

const before = await page.evaluate(() => window.app.viewer.camera.position.toArray());

// Left thumb pushes the stick forward; right thumb drags to look. Both at once.
const L = { x: size.width * 0.25, y: size.height * 0.6, id: 1 };
const R = { x: size.width * 0.75, y: size.height * 0.5, id: 2 };
await touch('touchStart', [L, R]);
await page.waitForTimeout(150);

results.stickVisible = await page.evaluate(
  () => !document.querySelector('.touch-stick').hidden,
);

for (let i = 1; i <= 10; i++) {
  await touch('touchMove', [
    { ...L, y: L.y - i * 6 },
    { ...R, x: R.x - i * 5 },
  ]);
  await page.waitForTimeout(60);
}
await page.waitForTimeout(600);

const after = await page.evaluate(() => window.app.viewer.camera.position.toArray());
const yaw = await page.evaluate(() => {
  const e = new window.app.THREE.Euler().setFromQuaternion(
    window.app.viewer.camera.quaternion,
    'YXZ',
  );
  return e.y;
});

await touch('touchEnd', []);
await page.waitForTimeout(200);
results.stickHiddenAfterRelease = await page.evaluate(
  () => document.querySelector('.touch-stick').hidden,
);

results.moved = Math.hypot(after[0] - before[0], after[1] - before[1], after[2] - before[2]);
results.yawChanged = Math.abs(yaw) > 1e-4;

// Climb button must move the camera up on its own.
const yBefore = await page.evaluate(() => window.app.viewer.camera.position.y);
await page.locator('.touch-btn[data-action="up"]').dispatchEvent('pointerdown');
await page.waitForTimeout(900);
await page.locator('.touch-btn[data-action="up"]').dispatchEvent('pointerup');
const yAfter = await page.evaluate(() => window.app.viewer.camera.position.y);
results.climbed = yAfter - yBefore;

await page.screenshot({ path: 'mobile.png' });
await browser.close();

console.log(JSON.stringify(results, null, 1));
console.log('errors:', errors.length ? errors.slice(0, 3) : 'none');

const ok =
  results.controlsPresent.stick &&
  results.controlsPresent.buttons === 3 &&
  results.stickVisible &&
  results.stickHiddenAfterRelease &&
  results.moved > 1 &&
  results.yawChanged &&
  results.climbed > 1 &&
  errors.length === 0;
console.log(ok ? 'PASS' : 'FAIL');
process.exit(ok ? 0 : 1);

import { chromium } from 'playwright';

const b = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH,
  args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--no-sandbox'],
});
const p = await b.newPage({ viewport: { width: 1280, height: 720 } });
const bad = [];
p.on('pageerror', (e) => bad.push(String(e).slice(0, 150)));
p.on('response', (r) => {
  if (r.status() >= 400 && !r.url().endsWith('favicon.ico')) bad.push(r.status() + ' ' + r.url().slice(-70));
});

await p.goto('http://localhost:4173/experiment-geo/', {
  waitUntil: 'domcontentloaded',
  timeout: 120000,
});
await p.waitForTimeout(50000);

console.log(
  JSON.stringify(
    await p.evaluate(() => {
      const i = window.app.viewer.renderer.info;
      return {
        triangles: i.render.triangles,
        drawCalls: i.render.calls,
        textures: i.memory.textures,
        tiles: window.app.world.tiles.visibleTiles.size,
        alt: Math.round(window.app.viewer.camera.position.y),
      };
    }),
  ),
);
console.log('problems:', bad.length ? bad.slice(0, 4) : 'none');
// page.screenshot() times out compositing this many triangles under software
// rendering; reading the canvas is instant and skips the HUD anyway.
import fs from 'node:fs';
const dataUrl = await p.evaluate(() => {
  const { viewer } = window.app;
  viewer.renderer.render(viewer.scene, viewer.camera);
  return viewer.renderer.domElement.toDataURL('image/png');
});
fs.writeFileSync('hybrid.png', Buffer.from(dataUrl.split(',')[1], 'base64'));
await b.close();

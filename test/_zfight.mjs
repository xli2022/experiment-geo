/**
 * Detect z-fighting by rendering the same view twice from imperceptibly
 * different camera distances.
 *
 * Z-fighting is a depth *tie*: which surface wins is decided by rounding, so a
 * sub-millimetre camera move flips whole regions between two unrelated colours
 * while every correctly-ordered surface shifts by well under a pixel. Counting
 * pixels that change a lot between the two frames therefore isolates the
 * fighting ones, which eyeballing a still frame cannot do — a still shows the
 * pattern but not whether it is geometry or shimmer.
 */
import fs from 'node:fs';
import { chromium } from 'playwright';

const ALT = Number(process.env.ZF_ALT ?? 120);
const EAST = Number(process.env.ZF_EAST ?? 0);
const OUT = process.env.ZF_OUT ?? 'zfight.png';

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH,
  args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: 800, height: 800 } });
await page.goto('http://localhost:4173/experiment-geo/', {
  waitUntil: 'domcontentloaded',
  timeout: 120000,
});
await page.waitForTimeout(35000);

const shoot = async (nudge) =>
  page.evaluate(
    ({ east, alt, nudge }) => {
      const { viewer } = window.app;
      viewer.camera.position.set(east, alt + nudge, 140 + nudge);
      viewer.camera.lookAt(east, 0, -200);
      viewer.camera.updateMatrixWorld(true);
      viewer.render();
      return viewer.renderer.domElement.toDataURL('image/png');
    },
    { east: EAST, alt: ALT, nudge },
  );

const a = await shoot(0);
const b = await shoot(0.002); // 2 mm

fs.writeFileSync(OUT, Buffer.from(a.split(',')[1], 'base64'));
fs.writeFileSync(OUT.replace('.png', '-b.png'), Buffer.from(b.split(',')[1], 'base64'));

// Compare in the page, where both frames are already decoded.
const stats = await page.evaluate(
  async ([da, db]) => {
    const load = (src) =>
      new Promise((res) => {
        const i = new Image();
        i.onload = () => res(i);
        i.src = src;
      });
    const [ia, ib] = await Promise.all([load(da), load(db)]);
    const c = document.createElement('canvas');
    c.width = ia.width;
    c.height = ia.height;
    const g = c.getContext('2d');
    g.drawImage(ia, 0, 0);
    const pa = g.getImageData(0, 0, c.width, c.height).data;
    g.clearRect(0, 0, c.width, c.height);
    g.drawImage(ib, 0, 0);
    const pb = g.getImageData(0, 0, c.width, c.height).data;
    let changed = 0;
    const total = c.width * c.height;
    for (let i = 0; i < pa.length; i += 4) {
      const d =
        Math.abs(pa[i] - pb[i]) + Math.abs(pa[i + 1] - pb[i + 1]) + Math.abs(pa[i + 2] - pb[i + 2]);
      if (d > 24) changed++;
    }
    // Paint the fighting pixels red over a dimmed frame so their shape is
    // visible — z-fighting between coplanar surfaces shows up as lines along
    // shared edges, which localises the cause far better than a count.
    const mask = g.getImageData(0, 0, c.width, c.height);
    for (let i = 0; i < pa.length; i += 4) {
      const d =
        Math.abs(pa[i] - pb[i]) + Math.abs(pa[i + 1] - pb[i + 1]) + Math.abs(pa[i + 2] - pb[i + 2]);
      if (d > 24) {
        mask.data[i] = 255; mask.data[i + 1] = 0; mask.data[i + 2] = 0;
      } else {
        mask.data[i] = pa[i] * 0.35; mask.data[i + 1] = pa[i + 1] * 0.35; mask.data[i + 2] = pa[i + 2] * 0.35;
      }
      mask.data[i + 3] = 255;
    }
    g.putImageData(mask, 0, 0);
    return { total, changed, pct: (100 * changed) / total, mask: c.toDataURL('image/png') };
  },
  [a, b],
);

fs.writeFileSync(
  OUT.replace('.png', '-mask.png'),
  Buffer.from(stats.mask.split(',')[1], 'base64'),
);
console.log(
  `z-fight pixels: ${stats.changed} / ${stats.total} (${stats.pct.toFixed(2)}%) at alt ${ALT} m`,
);
await browser.close();

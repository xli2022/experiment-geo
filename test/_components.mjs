/**
 * Do buildings separate into components without any metadata?
 *
 * The flat-palette style needs one colour per building, and PLATEAU's feature
 * IDs are dropped by the current conversion. src/style/components.ts recovers
 * identity from the geometry instead, on the assumption that LOD2 buildings are
 * disconnected solids. This checks that assumption: a tile of ~50 buildings
 * that yields ~50 components has separated them; one that yields 3 has not.
 */
import { chromium } from 'playwright';

const b = await chromium.launch({ executablePath: process.env.CHROMIUM_PATH,
  args: ['--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader','--no-sandbox'] });
const p = await b.newPage({ viewport: { width: 900, height: 520 } });
await p.goto('http://localhost:4173/experiment-geo/?city=tokyo-shinjuku&style=palette',
  { waitUntil: 'domcontentloaded', timeout: 120000 });
await p.waitForTimeout(45000);

const r = await p.evaluate(() => {
  const rows = [];
  window.app.worlds[0].tiles.group.traverse((o) => {
    if (!o.isMesh) return;
    const seed = o.geometry.getAttribute('aStyleSeed');
    if (!seed) { rows.push({ verts: o.geometry.getAttribute('position').count, comps: null }); return; }
    const seen = new Set();
    for (let i = 0; i < seed.count; i++) seen.add(seed.getX(i));
    rows.push({ verts: seed.count, comps: seen.size });
  });
  return rows;
});

const withSeed = r.filter((x) => x.comps !== null);
console.log(`${r.length} meshes, ${withSeed.length} seeded\n`);
console.log('  vertices   components   verts/component');
for (const x of withSeed.sort((a, c) => c.verts - a.verts).slice(0, 12)) {
  console.log(`  ${String(x.verts).padStart(8)} ${String(x.comps).padStart(12)}` +
    `   ${(x.verts / x.comps).toFixed(0).padStart(6)}`);
}
const tv = withSeed.reduce((s, x) => s + x.verts, 0);
const tc = withSeed.reduce((s, x) => s + x.comps, 0);
console.log(`\n  total ${tv} vertices in ${tc} components (${(tv/tc).toFixed(0)} verts each)`);
console.log(`  ${withSeed.filter((x) => x.comps === 1).length} meshes collapsed to a single component`);
await b.close();

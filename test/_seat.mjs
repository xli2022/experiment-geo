/**
 * Measure whether a city actually sits on its ground.
 *
 * "Floating buildings" is a look problem with a number behind it, and getting
 * that number right took three attempts. Two traps:
 *
 * A bounding box is not the geometry once you rotate it. Tiles arrive in
 * ECEF-aligned axes, where no local axis is up — at Tokyo world-up is local
 * (-0.62, 0.58, -0.53) — so a tile's 800 m of horizontal spread lives in all
 * three local axes at once. `Box3.setFromObject` transforms the box's eight
 * corners rather than the points inside it, and that turns 800 m of ground
 * plan into 1,700 m of apparent height: buildings sitting 22 m underground got
 * reported as 875 m underground. The declared bounds are exactly right; the
 * loose transform is not. Everything here reads vertices.
 *
 * And a mesh is not a building either: PLATEAU merges a whole city block, up
 * to 860 m across, into one mesh. Its lowest vertex is the lowest point of the
 * block, which says nothing about where any particular building meets terrain
 * varying 40 m across the same span. So vertices are binned into small cells
 * and each cell is compared with the ground directly beneath it.
 *
 * Read the *low* percentiles. Most cells contain only roof, so the median
 * residual is a building's height and means nothing. But nothing should sit
 * far below the ground, and plenty should touch it, so a seated city has p05
 * near zero. A city floating 37 m has p05 near +37, whatever its median says.
 *
 * The ground is raycast from the real mesh in the scene rather than re-read
 * from the height field, so this exercises the shipped placement path — anchor,
 * geoid, plane transform — not a copy of its arithmetic that could agree with
 * it while both are wrong.
 */
import { chromium } from 'playwright';

const BASE = 'http://localhost:4173/experiment-geo/';
const CITY = process.env.S_CITY ?? 'tokyo-shinjuku';
const SETTLE = Number(process.env.S_SETTLE ?? 45000);
const CELL = Number(process.env.S_CELL ?? 20);

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH,
  args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: 900, height: 520 } });
page.on('pageerror', (e) => console.log('pageerror:', String(e).slice(0, 200)));

await page.goto(`${BASE}?city=${CITY}`, { waitUntil: 'domcontentloaded', timeout: 120000 });
await page.waitForTimeout(SETTLE);

const result = await page.evaluate(({ cell }) => {
  const THREE = window.app.THREE;
  const scene = window.app.viewer.scene;

  // The ground is whatever plane geometry sits directly in the scene: the
  // surveyed grid, and the flat surround holding the horizon out to the fog.
  // Tiles arrive inside groups, so this cannot pick up a building by accident.
  const ground = [];
  scene.traverse((o) => {
    if (o.isMesh && o.geometry?.type === 'PlaneGeometry') ground.push(o);
  });

  // Lowest vertex per cell of ground plan.
  const low = new Map();
  const v = new THREE.Vector3();
  let meshes = 0;
  let verts = 0;
  window.app.worlds[0]?.tiles?.group?.traverse?.((o) => {
    if (!o.isMesh) return;
    meshes++;
    const pos = o.geometry.getAttribute('position');
    verts += pos.count;
    for (let i = 0; i < pos.count; i++) {
      v.fromBufferAttribute(pos, i).applyMatrix4(o.matrixWorld);
      const key = `${Math.floor(v.x / cell)},${Math.floor(v.z / cell)}`;
      const prev = low.get(key);
      if (prev === undefined || v.y < prev) low.set(key, v.y);
    }
  });

  const ray = new THREE.Raycaster();
  const down = new THREE.Vector3(0, -1, 0);
  const from = new THREE.Vector3();
  const groundAt = (x, z) => {
    from.set(x, 20000, z);
    ray.set(from, down);
    const hits = ray.intersectObjects(ground, false);
    // Inside the surveyed field the ray passes through both the grid and the
    // flat surround beneath it. One hit means only the surround is there, and
    // comparing against a sheet at the median would be measuring nothing.
    return hits.length < 2 ? null : Math.max(...hits.map((h) => h.point.y));
  };

  // One ray per cell and no more. The ground is a 147k-vertex plane with no
  // BVH, so a ray costs ~30 ms; sampling neighbours with four more rays each
  // turns a two-minute run into half an hour. The neighbours are cells too, so
  // their ground heights are already here.
  const groundOf = new Map();
  let outside = 0;
  for (const key of low.keys()) {
    const [cx, cz] = key.split(',').map(Number);
    const g = groundAt((cx + 0.5) * cell, (cz + 0.5) * cell);
    if (g === null) outside++;
    else groundOf.set(key, g);
  }

  const residuals = [];
  const bySlope = {};
  for (const [key, y] of low) {
    const g = groundOf.get(key);
    if (g === undefined) continue;
    const r = y - g;
    residuals.push(r);

    // How much the ground moves across a building-sized patch. A flat floor
    // slab laid on sloping ground is buried at its uphill end by roughly this
    // much, and that is the difference between geometry that is wrong and
    // geometry that is right about a city built on hills.
    const [cx, cz] = key.split(',').map(Number);
    const around = [`${cx - 1},${cz}`, `${cx + 1},${cz}`, `${cx},${cz - 1}`, `${cx},${cz + 1}`]
      .map((k) => groundOf.get(k))
      .filter((v) => v !== undefined);
    if (around.length === 4) {
      const relief = Math.max(...around) - Math.min(...around);
      const b = relief < 1 ? '0-1' : relief < 2 ? '1-2' : relief < 4 ? '2-4' : relief < 8 ? '4-8' : '8+';
      (bySlope[b] ??= []).push(r);
    }
  }

  residuals.sort((a, b) => a - b);
  const q = (p) => residuals[Math.floor((residuals.length - 1) * p)];
  const hist = {};
  for (const r of residuals) {
    const b = Math.max(-5, Math.min(9, Math.floor(r / 10)));
    hist[b] = (hist[b] ?? 0) + 1;
  }

  return {
    meshes, verts, cells: residuals.length, outside,
    tiles: window.app.worlds.reduce((n, w) => n + (w.tiles?.visibleTiles?.size ?? 0), 0),
    groundMeshes: ground.length,
    p01: q(0.01), p05: q(0.05), p10: q(0.1), p25: q(0.25), p50: q(0.5), p95: q(0.95),
    min: residuals[0], max: residuals[residuals.length - 1],
    touching: residuals.filter((r) => Math.abs(r) <= 2).length,
    below5: residuals.filter((r) => r < -5).length,
    hist,
    slope: Object.fromEntries(Object.entries(bySlope).map(([k, v]) => {
      v.sort((a, b) => a - b);
      return [k, { n: v.length, p05: v[Math.floor(v.length * 0.05)], p50: v[Math.floor(v.length * 0.5)] }];
    })),
  };
}, { cell: CELL });

const f = (v) => (Number.isFinite(v) ? v.toFixed(1) : 'n/a');
if (!result.groundMeshes) {
  // Berlin's bake draws its own ground, so there is no sheet to measure
  // against and nothing here applies.
  console.log(`\n${CITY} has no ground sheet — its tileset carries its own terrain.`);
  await browser.close();
  process.exit(0);
}
console.log(`\n${CITY}: ${result.verts} vertices in ${result.meshes} meshes -> ` +
  `${result.cells} cells of ${CELL} m (${result.outside} outside the surveyed field)`);
console.log(`  ${result.tiles} tiles visible, ${result.groundMeshes} ground meshes\n`);
console.log(`  lowest vertex over ground, by percentile:`);
console.log(`    p01 ${f(result.p01)}   p05 ${f(result.p05)}   p10 ${f(result.p10)}` +
  `   p25 ${f(result.p25)}   p50 ${f(result.p50)}   p95 ${f(result.p95)} m`);
console.log(`    range ${f(result.min)} .. ${f(result.max)} m`);
console.log(`  ${result.touching} cells within 2 m of the ground, ${result.below5} more than 5 m under it`);
console.log(`\n  by how much the ground moves across one cell either way:`);
for (const k of ['0-1', '1-2', '2-4', '4-8', '8+']) {
  const s = result.slope[k];
  if (s) console.log(`    relief ${k.padEnd(4)} m: ${String(s.n).padStart(5)} cells, p05 ${f(s.p05).padStart(6)}  p50 ${f(s.p50).padStart(6)} m`);
}

console.log(`\n  residual histogram (10 m bins):`);
for (const b of Object.keys(result.hist).map(Number).sort((a, c) => a - c)) {
  const label = b <= -5 ? '  <-50' : b >= 9 ? '   90+' : `${String(b * 10).padStart(4)}..`;
  const n = result.hist[b];
  console.log(`    ${label} ${String(n).padStart(5)} ${'#'.repeat(Math.round((n / result.cells) * 120))}`);
}

await browser.close();

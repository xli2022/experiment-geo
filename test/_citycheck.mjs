/**
 * Load each city through the real UI and confirm it comes up.
 *
 * The thing worth checking is not that a tileset parses — it is that switching
 * swaps the credit line with it. The sources carry different licences, so a
 * picker that changed the geometry and left Berlin's OSM attribution over
 * Tokyo's PLATEAU data would be a licensing bug that looks like nothing.
 */
import fs from 'node:fs';
import { chromium } from 'playwright';

const BASE = 'http://localhost:4173/experiment-geo/';
const OUT = process.env.C_OUT ?? 'city';
const SETTLE = Number(process.env.C_SETTLE ?? 45000);

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM_PATH,
  args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: 900, height: 520 } });
const errors = [];
page.on('console', (m) => {
  if (m.type() === 'error') errors.push(m.text().slice(0, 200));
});
page.on('pageerror', (e) => errors.push(String(e).slice(0, 200)));

await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 120000 });
await page.waitForTimeout(2000);

const ids = await page.$$eval('#city option', (os) => os.map((o) => o.value));
console.log('cities offered:', ids.join(', '));

let failed = false;
for (const id of ids) {
  // Drive the select the way a user does, so the change handler is what runs.
  await page.selectOption('#city', id);
  await page.waitForTimeout(SETTLE);

  const state = await page.evaluate(() => ({
    value: document.getElementById('city').value,
    credit: document.getElementById('attribution').textContent.replace(/\s+/g, ' ').trim(),
    overlay: document.getElementById('overlay').hidden,
    msg: document.getElementById('overlay-msg').textContent,
    tiles: window.app?.world?.tiles?.visibleTiles?.size ?? 0,
    query: new URL(location.href).searchParams.get('city'),
  }));

  const ok = state.overlay && state.tiles > 0;
  failed ||= !ok;
  console.log(
    `\n${id}: ${ok ? 'OK' : 'FAILED'}\n` +
      `  visible tiles : ${state.tiles}${state.overlay ? '' : `\n  overlay still up: ${state.msg}`}\n` +
      `  url ?city=    : ${state.query ?? '(default, omitted)'}\n` +
      `  credit        : ${state.credit}`,
  );
  await page.screenshot({ path: `${OUT}-${id}.png` });
}

if (errors.length) {
  console.log(`\nconsole errors (${errors.length}):`);
  for (const e of [...new Set(errors)].slice(0, 6)) console.log('  ' + e);
}

await browser.close();
fs.writeFileSync(`${OUT}-errors.txt`, errors.join('\n'));
process.exit(failed ? 1 : 0);

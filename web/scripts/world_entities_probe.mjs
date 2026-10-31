// The world entities layer, driven like an operator: a nearby NPC gets its
// client figure, a label, and a row in the Navigator's search. Throwaway
// harness (the daemon must serve a project with server rows); exits non-zero
// on a failed check.
import { mkdirSync } from 'node:fs';
import { chromium } from 'playwright';

const base = process.argv[2] ?? 'http://127.0.0.1:8761';
const outDir = process.argv[3] ?? '/tmp/camelot-world-entities';
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch({
	args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--use-gl=angle']
});
const page = await browser.newPage({ viewport: { width: 1400, height: 900 } });
const errors = [];
page.on('console', (message) => {
	if (message.type() === 'error') errors.push(message.text());
});
page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));

await page.goto(`${base}/world`, { waitUntil: 'load' });
await page.waitForSelector('.zones button', { timeout: 120000 });
await page.locator('.zones button', { hasText: 'Camelot Hills' }).first().click();
await page.waitForFunction('window.__world && window.__world.name', { timeout: 300000 });
// The list sits in the dock's flex column; the zone's sections must not
// squeeze it to zero once they appear.
const zonesHeight = await page.evaluate(
	`document.querySelector('.zones')?.getBoundingClientRect().height ?? 0`
);
if (zonesHeight < 1) throw new Error('the zone list collapsed after opening a zone');

const spawns = await page.evaluate(
	`fetch('/api/v1/world/zone/0/spawns').then((response) => response.json())`
);
const mob =
	spawns.mobs.find((row) => row.model && row.guild) ?? spawns.mobs.find((row) => row.model);
if (!mob) throw new Error('no mob with a model in this zone');

await page.evaluate(`window.__worldLook(${mob.x}, ${mob.y}, 180, 1600)`);
await page.waitForFunction('window.__worldViewer.stats().previews > 0', { timeout: 180000 });
await page.waitForFunction('document.querySelectorAll(".world-label").length > 0', {
	timeout: 60000
});
const stats = await page.evaluate('window.__worldViewer.stats()');

const needle = mob.name.split(' ')[0];
await page.fill('.entities input[type=search]', needle);
await page.waitForTimeout(500);
const list = await page.evaluate(
	`[...document.querySelectorAll('.entities .hits button')].map((button) => button.textContent.replace(/\\s+/g, ' ').trim())`
);
const found = list.some((text) => text.toLowerCase().includes(needle.toLowerCase()));

const report = {
	zone: 'Camelot Hills',
	zonesHeight,
	mob: { name: mob.name, model: mob.model },
	stats,
	search: { needle, found, first: list.slice(0, 4) }
};
console.log(JSON.stringify(report, null, 1));
await page.screenshot({ path: `${outDir}/world-entities.png`, timeout: 120000 });
console.log('console errors:', errors.length ? errors : 'none');
await browser.close();

if (zonesHeight < 1 || stats.previews < 1 || stats.labels < 1 || !found || errors.length) {
	process.exitCode = 1;
}

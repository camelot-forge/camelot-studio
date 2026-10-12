// Read-only probe: which server rows does the viewer propose to carry with a
// fixture (B2), measured on a real zone? Nothing is written — the daemon runs
// against the pristine client and no edit flag is passed.
import { mkdirSync } from 'node:fs';
import { chromium } from 'playwright';

const base = process.argv[2] ?? 'http://127.0.0.1:8761';
const zoneId = Number(process.argv[3] ?? 0);
const fixtureName = process.argv[4] ?? 'Keep';
const outDir = process.argv[5] ?? '/tmp/camelot-rows';
mkdirSync(outDir, { recursive: true });
const SHOT = { timeout: 120000 };

const browser = await chromium.launch({
	args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--use-gl=angle']
});
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
const errors = [];
page.on('console', (msg) => {
	if (msg.type() === 'error') errors.push(msg.text());
});

await page.goto(`${base}/world`, { waitUntil: 'load' });
await page.waitForFunction('document.querySelectorAll("aside select option").length > 0', {
	timeout: 120000
});
await page.fill('input[type=search]', String(zoneId));
await page.locator('.zones button', { hasText: 'Camelot Hills' }).first().click();
await page.waitForFunction('window.__world && window.__world.name', { timeout: 180000 });
await page.waitForTimeout(4000);

const report = await page.evaluate(`(async () => {
	const zone = await fetch('${base}/api/v1/world/zone/${zoneId}').then((r) => r.json());
	const spawns = await fetch('${base}/api/v1/world/zone/${zoneId}/spawns').then((r) => r.json());
	const wanted = ${JSON.stringify(fixtureName)};
	const hit = zone.fixtures.find((f) => f.name === wanted) ??
		zone.fixtures.find((f) => f.name.toLowerCase().includes(wanted.toLowerCase()));
	const out = { zone: zone.info.name, fixtures: zone.fixtures.length,
		doors: spawns.doors.length, mobs: spawns.mobs.length, fixture: hit ?? null };
	if (!hit) return out;
	window.__worldSelect(hit.id);
	window.__worldFocus(hit.x, hit.y, 9000);
	window.__worldEditMode('translate');
	await new Promise((done) => setTimeout(done, 300));
	out.editor = window.__worldEditor();
	out.nearby = spawns.doors
		.map((door) => ({ id: door.id, name: door.name,
			distance: Math.round(Math.hypot(door.x - hit.x, door.y - hit.y)) }))
		.filter((door) => door.distance < 3000)
		.sort((a, b) => a.distance - b.distance);
	return out;
})()`);
console.log(JSON.stringify(report, null, 1));
const keep = report.fixture;
if (keep) {
	await page.waitForTimeout(1500);
	await page.screenshot({
		path: `${outDir}/keep-${zoneId}-carried.png`,
		...SHOT
	});
}
console.log('console errors:', errors.length ? errors : 'none');
await browser.close();

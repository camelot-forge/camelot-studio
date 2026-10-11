// The /world verification shot: open the daemon's viewer, load one zone, and
// save screenshots (plain, overlays, and a selected+centred fixture) plus the
// scene stats.
// Usage: node scripts/world_shot.mjs [base-url] [zone-name] [out-dir] [x y | fixture-name]
// Env: SHOT_WIDTH / SHOT_HEIGHT shrink the viewport (the software WebGL path
// gets expensive once the ground samples its sheets without mipmaps).
import { mkdirSync } from 'node:fs';
import { chromium } from 'playwright';

const base = process.argv[2] ?? 'http://127.0.0.1:8760';
const zoneName = process.argv[3] ?? 'Camelot Hills';
const outDir = process.argv[4] ?? process.env.SHOT_DIR ?? '/tmp/camelot-world';
const focusX = Number.isFinite(Number(process.argv[5])) ? Number(process.argv[5]) : null;
const focusY = Number.isFinite(Number(process.argv[6])) ? Number(process.argv[6]) : null;
const azimuth = Number.isFinite(Number(process.argv[7])) ? Number(process.argv[7]) : null;
const distance_ = Number.isFinite(Number(process.argv[8])) ? Number(process.argv[8]) : null;
const fixtureName = process.argv[5] && focusX === null ? process.argv[5] : null;
const width = Number(process.env.SHOT_WIDTH ?? 1280);
const height = Number(process.env.SHOT_HEIGHT ?? 800);
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch({
	args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--use-gl=angle']
});
const page = await browser.newPage({ viewport: { width, height } });
page.on('console', (msg) => {
	if (msg.type() === 'error') console.log('console error:', msg.text());
});

await page.goto(`${base}/world`, { waitUntil: 'load' });
// The index arrives first; the region select fills with it.
await page.waitForFunction('document.querySelectorAll("aside select option").length > 0', {
	timeout: 30000
});
await page.fill('input[type=search]', zoneName);
const button = page.locator('.zones button', { hasText: zoneName }).first();
await button.click();
await page.waitForFunction('window.__world && window.__world.name', { timeout: 120000 });
await page.waitForTimeout(2500); // let the ground sheets and meshes land

const stats = await page.evaluate('window.__world');
const after = await page.evaluate('window.__worldStats ? window.__worldStats() : {}');
console.log('scene at load:', JSON.stringify(stats));
console.log('scene after settle:', JSON.stringify(after));
const slug = zoneName.replace(/[^a-z0-9_.-]/gi, '_');
if (focusX !== null && focusY !== null) {
	if (azimuth !== null) {
		await page.evaluate(
			distance_ === null
				? `window.__worldLook(${focusX}, ${focusY}, ${azimuth})`
				: `window.__worldLook(${focusX}, ${focusY}, ${azimuth}, ${distance_})`
		);
	} else {
		await page.evaluate(`window.__worldFocus(${focusX}, ${focusY}, 8000)`);
	}
	await page.waitForTimeout(700);
}
await page.screenshot({ path: `${outDir}/${slug}.png` });
console.log(`shot -> ${outDir}/${slug}.png`);

for (const layer of ['bounds', 'jumps']) {
	await page.getByLabel(layer).check();
}
await page.waitForTimeout(500);
await page.screenshot({ path: `${outDir}/${slug}-overlays.png` });
console.log(`shot -> ${outDir}/${slug}-overlays.png`);

if (fixtureName) {
	// Select the first fixture of that name through the page's own hook, then
	// recentre — the click path cannot be scripted reliably without knowing
	// where the object is on screen.
	const found = await page.evaluate(`(() => {
		const stats = window.__world;
		return stats ? stats.fixtures : 0;
	})()`);
	console.log('fixtures in scene:', found);
	const picked = await page.evaluate(`(async () => {
		const zone = await fetch('${base}/api/v1/world/zone/' + window.__world.zone).then((r) => r.json());
		const hit = zone.fixtures.find((f) => f.name.toLowerCase().includes(${JSON.stringify(
			fixtureName.toLowerCase()
		)}));
		if (!hit) return null;
		window.__worldSelect(hit.id);
		return hit;
	})()`);
	if (picked) {
		await page.keyboard.press('f');
		await page.waitForTimeout(700);
		await page.screenshot({ path: `${outDir}/${slug}-selected.png` });
		console.log(`shot -> ${outDir}/${slug}-selected.png (${picked.name} #${picked.id})`);
	} else {
		console.log(`no fixture named ${fixtureName}`);
	}
}

await browser.close();

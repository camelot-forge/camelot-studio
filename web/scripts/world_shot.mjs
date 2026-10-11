// The /world verification shot: open the daemon's viewer, load one zone, and
// save screenshots (plain, then with the overlays on) plus the scene stats.
// Usage: node scripts/world_shot.mjs [base-url] [zone-name] [out-dir] [focus-x focus-y]
import { mkdirSync } from 'node:fs';
import { chromium } from 'playwright';

const base = process.argv[2] ?? 'http://127.0.0.1:8760';
const zoneName = process.argv[3] ?? 'Camelot Hills';
const outDir = process.argv[4] ?? process.env.SHOT_DIR ?? '/tmp/camelot-world';
const focusX = process.argv[5] ? Number(process.argv[5]) : null;
const focusY = process.argv[6] ? Number(process.argv[6]) : null;
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch({
	args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--use-gl=angle']
});
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
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
	await page.evaluate(`window.__worldFocus(${focusX}, ${focusY}, 8000)`);
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

await browser.close();

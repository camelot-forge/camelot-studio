// Diagnostic screenshots: render each named figure via the /render route and
// save a PNG so orientation can be reviewed without the UI.
// Usage: node scripts/shots.mjs <base-url> <Name.NIF> [Name2.NIF ...]
import { mkdirSync } from 'node:fs';
import { chromium } from 'playwright';

const base = process.argv[2] ?? 'http://localhost:8760';
const names = process.argv.slice(3);
const outDir = process.env.SHOT_DIR ?? '/tmp/camelot-shots';
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 900, height: 700 } });

for (const name of names) {
	const qs = new URLSearchParams({ q: name, type: 'nif', limit: '5' });
	const res = await fetch(`${base}/api/v1/assets?${qs}`);
	const items = (await res.json()).items ?? [];
	const hit = items.find((a) => a.name.toLowerCase() === name.toLowerCase()) ?? items[0];
	if (!hit) {
		console.log(`${name}: not found`);
		continue;
	}
	const glb = `/api/v1/assets/${encodeURIComponent(hit.id)}/model`;
	await page.goto(`${base}/render?glb=${encodeURIComponent(glb)}`, { waitUntil: 'load' });
	await page.waitForFunction('window.__ready === true', { timeout: 30000 }).catch(() => {});
	await page.waitForTimeout(300);
	const file = `${outDir}/${hit.name.replace(/[^a-z0-9_.-]/gi, '_')}.png`;
	await page.screenshot({ path: file });
	console.log(`shot ${hit.name} -> ${file}`);
}

await browser.close();

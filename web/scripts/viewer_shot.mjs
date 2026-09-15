// Real-viewer screenshots: open /, search the figure in the Library, select
// it, wait for the model + idle to load, then capture.
// Usage: node scripts/viewer_shot.mjs <base-url> <Name> [Name2 ...]
import { mkdirSync } from 'node:fs';
import { chromium } from 'playwright';

const base = process.argv[2] ?? 'http://localhost:8760';
const names = process.argv.slice(3);
const outDir = process.env.SHOT_DIR ?? '/tmp/camelot-viewer-shots';
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const errors = [];
page.on('console', (m) => {
	if (m.type() === 'error') errors.push(m.text());
});
page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`));

for (const name of names) {
	errors.length = 0;
	await page.goto(base, { waitUntil: 'load' });
	const search = page.locator('input[type="search"]').first();
	await search.fill(name);
	// debounced search: wait for the results to settle
	await page.waitForTimeout(700);
	// click the row whose text matches the figure (case-insensitive)
	const row = page.locator('aside button.row').filter({ hasText: name }).first();
	if ((await row.count()) === 0) {
		console.log(`${name}: no library row matched`);
		continue;
	}
	await row.click();
	// wait for the Loading overlay to appear then clear
	await page.waitForTimeout(1200);
	await page
		.waitForFunction(() => !document.querySelector('.center')?.textContent?.includes('Loading'), {
			timeout: 30000
		})
		.catch(() => {});
	await page.waitForTimeout(1200);
	const file = `${outDir}/${name.replace(/[^a-z0-9_.-]/gi, '_')}.png`;
	await page.screenshot({ path: file });
	console.log(`shot ${name} -> ${file}${errors.length ? `  errors: ${errors[0]}` : ''}`);
}

await browser.close();

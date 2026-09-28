// Builder armour-material check: drives /builder in headless Chromium and
// reports the Armour <select> state, the model requests it triggers, and any
// console / network error. Renders a figure, not a texture-correctness test.
import { mkdirSync } from 'node:fs';
import { chromium } from 'playwright';

const base = process.argv[2] ?? 'http://localhost:8760';
const outDir = process.env.SHOT_DIR ?? '/tmp/camelot-shots';
mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });

const errors = [];
const failed = [];
const models = [];
page.on('console', (m) => {
	if (m.type() === 'error') errors.push(m.text());
});
page.on('response', (r) => {
	const u = r.url();
	if (u.includes('/character/') && u.includes('/model')) models.push(`${r.status()} ${u}`);
	if (r.status() >= 400) failed.push(`${r.status()} ${u}`);
});

await page.goto(`${base}/builder`, { waitUntil: 'networkidle' });

// Pick Briton Female so the figure assembles.
await page
	.getByRole('button', { name: /briton/i })
	.first()
	.click();
await page.waitForTimeout(6000);

const select = page.locator('select');
const optionCount = await select.locator('option').count();
const initial = await select.inputValue();
console.log(`armour select present: ${(await select.count()) > 0}`);
console.log(`options: ${optionCount}`);
console.log(`initial value: ${JSON.stringify(initial)}`);

const meshCount = async () => page.evaluate(() => document.querySelectorAll('canvas').length);
console.log(`canvas present: ${(await meshCount()) > 0}`);

await page.screenshot({ path: `${outDir}/builder-armour-default.png` });

for (const material of ['Plate', 'Cloth']) {
	await select.selectOption(material);
	await page.waitForTimeout(7000);
	console.log(
		`after selectOption(${material}): value=${JSON.stringify(await select.inputValue())}`
	);
	await page.screenshot({ path: `${outDir}/builder-armour-${material.toLowerCase()}.png` });
}

// Gender switch keeps the pick and rebuilds.
await page
	.getByRole('button', { name: /^male$/i })
	.first()
	.click();
await page.waitForTimeout(7000);
console.log(`after gender=male: value=${JSON.stringify(await select.inputValue())}`);
await page.screenshot({ path: `${outDir}/builder-armour-male.png` });

console.log(`model requests (${models.length}):`);
for (const m of models) console.log(`  ${m}`);
console.log(`console errors (${errors.length}):`);
for (const e of errors) console.log(`  ${e}`);
console.log(`failed requests (${failed.length}):`);
for (const f of failed) console.log(`  ${f}`);

await browser.close();

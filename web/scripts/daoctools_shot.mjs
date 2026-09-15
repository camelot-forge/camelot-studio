// Pick a race on daoctools and screenshot the head.
import { chromium } from 'playwright';

const race = process.argv[2] ?? 'Saracen';
const outDir = process.env.SHOT_DIR ?? '/tmp/camelot-viewer-shots';
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
await page.goto('https://daoctools.net/model-viewer', { waitUntil: 'load', timeout: 60000 });
await page.waitForTimeout(5000);
await page
	.getByRole('button', { name: race, exact: true })
	.click()
	.catch(async (e) => {
		console.log('race click failed:', e.message);
	});
await page.waitForTimeout(4000);
await page.screenshot({ path: `${outDir}/daoctools_${race}.png` });
console.log(`shot daoctools_${race}`);
await browser.close();

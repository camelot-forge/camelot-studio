// End-to-end validation: the real three.js GPU skinning must reproduce the
// proven CPU bake for Boar idle (max vertex error ~1e-4, see vertex proof).
// Usage: node scripts/anim_check.mjs (needs `camelot serve` on :8765).
import { chromium } from 'playwright';

const base = 'http://localhost:8765';

function fail(msg) {
	console.error(`FAIL: ${msg}`);
	process.exitCode = 1;
}

function approx(a, b, tol, label) {
	const d = Math.abs(a - b);
	if (!(d <= tol)) fail(`${label}: ${a} vs ${b} (tol ${tol})`);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 900, height: 700 } });
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));

const assets = await (await fetch(`${base}/api/v1/assets?q=Boar&type=nif&limit=5`)).json();
const hit = assets.items.find((a) => a.name === 'Boar.NIF') ?? assets.items[0];
const id = encodeURIComponent(hit.id);
const glb = `/api/v1/assets/${id}/model?skinned=true`;
const anim = `/api/v1/assets/${id}/anim`;

async function shot(tGame) {
	const params = new URLSearchParams({ glb, anim, t: String(tGame), debug: '1' });
	await page.goto(`${base}/render?${params}`, { waitUntil: 'load' });
	await page.waitForFunction('window.__ready === true', { timeout: 30000 });
	return {
		dims: await page.evaluate('window.__dims'),
		skinnedDims: await page.evaluate('window.__skinnedDims'),
		joints: await page.evaluate('window.__joints'),
		posed: await page.evaluate('window.__posed')
	};
}

// Static reference (plain mesh, no skinning): the approved bind framing.
await page.goto(
	`${base}/render?${new URLSearchParams({ glb: `/api/v1/assets/${id}/model`, fixedcam: '1', flat: '1' })}`,
	{ waitUntil: 'load' }
);
await page.waitForFunction('window.__ready === true', { timeout: 30000 });
const stat = await page.evaluate('window.__dims');
console.log('static bind dims', stat.map((x) => +x.toFixed(2)).join(', '));

const bind = await shot(0);
console.log(
	'skinned bind dims',
	bind.skinnedDims.map((x) => +x.toFixed(2)).join(', '),
	`joints=${bind.joints} posed=${bind.posed}`
);
if (bind.joints !== 31) fail(`expected 31 joints, got ${bind.joints}`);
if (bind.posed !== 31) fail(`expected 31 posed joints, got ${bind.posed}`);
for (let i = 0; i < 3; i++) approx(bind.skinnedDims[i], stat[i], 2.5, `bind dim ${i}`);

const idle = await shot(2.5); // game seconds (key 0.5 at rate 0.2)
console.log('skinned idle dims', idle.skinnedDims.map((x) => +x.toFixed(2)).join(', '));
for (let i = 0; i < 3; i++) approx(idle.skinnedDims[i], stat[i], 0.3 * stat[i], `idle dim ${i}`);
// CPU bake at the same game time must match the GPU pose exactly.
for (const [i, want] of [21.03, 37.07, 89.21].entries())
	approx(idle.skinnedDims[i], want, 0.6, `idle-vs-bake dim ${i}`);
const moved = idle.skinnedDims.some((d, i) => Math.abs(d - bind.skinnedDims[i]) > 0.5);
if (!moved) fail('idle pose identical to bind pose (animation not applied?)');

if (errors.length) fail(`page errors: ${errors.join('; ')}`);
await browser.close();
if (!process.exitCode) console.log('anim_check: OK');

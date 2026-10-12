// The /world editor verification: select a fixture, move it with the journal,
// build the pack through the panel's own buttons, then revert — and save a
// screenshot of each step. Drives the same code path the UI does (`__worldEdit`
// is the hook the panel's handlers use), never a private shortcut.
// Usage: node scripts/world_edit_shot.mjs [base-url] [zone-name] [out-dir] [fixture-name]
import { mkdirSync } from 'node:fs';
import { chromium } from 'playwright';

const base = process.argv[2] ?? 'http://127.0.0.1:8760';
const zoneName = process.argv[3] ?? 'Camelot Hills';
const outDir = process.argv[4] ?? process.env.SHOT_DIR ?? '/tmp/camelot-editor';
const fixtureName = process.argv[5] ?? 'Pine';
const width = Number(process.env.SHOT_WIDTH ?? 1280);
const height = Number(process.env.SHOT_HEIGHT ?? 800);
mkdirSync(outDir, { recursive: true });
// Software WebGL: a full-frame capture takes tens of seconds, more than
// Playwright's default patience.
const SHOT = { timeout: 120000 };

const browser = await chromium.launch({
	args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--use-gl=angle']
});
const page = await browser.newPage({ viewport: { width, height } });
const errors = [];
page.on('console', (msg) => {
	if (msg.type() === 'error') errors.push(msg.text());
});

async function state(label) {
	const editor = await page.evaluate('window.__worldEditor ? window.__worldEditor() : null');
	console.log(`${label}:`, JSON.stringify(editor));
	return editor;
}

await page.goto(`${base}/world`, { waitUntil: 'load' });
await page.waitForFunction('document.querySelectorAll("aside select option").length > 0', {
	timeout: 30000
});
await page.fill('input[type=search]', zoneName);
await page.locator('.zones button', { hasText: zoneName }).first().click();
await page.waitForFunction('window.__world && window.__world.name', { timeout: 120000 });
await page.waitForTimeout(2000);

// The editor panel must be there (the daemon was started with --edits).
await page.waitForSelector('section.editor', { timeout: 10000 });
await state('editor at load');

// Pick a fixture by name through the page's own selection hook, stand close,
// and arm the translate gizmo.
const picked = await page.evaluate(`(async () => {
	const needle = ${JSON.stringify(fixtureName.toLowerCase())};
	const zone = await fetch('${base}/api/v1/world/zone/' + window.__world.zone).then((r) => r.json());
	const hit = zone.fixtures.find((f) => f.name.toLowerCase().includes(needle)) ??
		zone.fixtures.find((f) => f.name);
	if (!hit) return null;
	window.__worldSelect(hit.id);
	window.__worldEditMode('translate');
	window.__worldFocus(hit.x, hit.y, 7000);
	return hit;
})()`);
if (!picked) throw new Error(`no fixture named ${fixtureName}`);
console.log('fixture:', JSON.stringify(picked));
await page.waitForTimeout(800);
await page.screenshot({
	path: `${outDir}/${zoneName.replace(/[^a-z0-9_.-]/gi, '_')}-gizmo.png`,
	...SHOT
});

// One move step through the journal (the panel's own path).
const moved = await page.evaluate(`(async () => {
	const zone = await fetch('${base}/api/v1/world/zone/' + window.__world.zone).then((r) => r.json());
	const hit = zone.fixtures.find((f) => f.name.toLowerCase().includes(${JSON.stringify(
		fixtureName.toLowerCase()
	)}) );
	if (!hit) return null;
	await window.__worldEdit({ kind: 'move', id: hit.id, x: hit.x + 512, y: hit.y + 512, z: hit.z });
	return hit;
})()`);
console.log('moved from:', JSON.stringify(moved));
await page.waitForTimeout(500);
await state('after move');
await page.screenshot({
	path: `${outDir}/${zoneName.replace(/[^a-z0-9_.-]/gi, '_')}-moved.png`,
	...SHOT
});

await page.getByRole('button', { name: 'build' }).click();
await page.waitForFunction('window.__worldEditor().status?.startsWith("built")', {
	timeout: 120000
});
await state('after build');
await page.screenshot({
	path: `${outDir}/${zoneName.replace(/[^a-z0-9_.-]/gi, '_')}-built.png`,
	...SHOT
});

await page.getByRole('button', { name: 'revert' }).click();
await page.waitForFunction('window.__worldEditor().status === "journal cleared"', {
	timeout: 30000
});
await state('after revert');

console.log('console errors:', errors.length ? errors : 'none');
await browser.close();

// Slice 4 — the dock system and the world editor's own dock (Inspect /
// Journal), driven like an operator. Throwaway harness; exits non-zero on a
// failed check.
//
//   node scripts/dock_probe.mjs http://127.0.0.1:8760 /tmp/camelot-ux "Camelot Hills"
//
// The world half writes into the *operator's* session through its own routes
// and reverts at the end (the journal is left empty, as it found it).
import { mkdirSync } from 'node:fs';
import { chromium } from 'playwright';

const base = process.argv[2] ?? 'http://127.0.0.1:8760';
const outDir = process.argv[3] ?? '/tmp/camelot-ux';
const zoneName = process.argv[4] ?? 'Camelot Hills';
mkdirSync(outDir, { recursive: true });

let failures = 0;
function check(label, ok, detail = '') {
	console.log(`${ok ? 'ok  ' : 'FAIL'} ${label}${detail ? ` — ${detail}` : ''}`);
	if (!ok) failures++;
}

const browser = await chromium.launch({
	args: ['--use-gl=swiftshader', '--enable-unsafe-swiftshader', '--no-sandbox']
});
const page = await browser.newPage({ viewport: { width: 1600, height: 950 } });
// A zone opens on a corpus-scale client: seconds, not milliseconds — but a
// harness that waits three minutes for a button is a harness that hid a bug.
page.setDefaultTimeout(120000);
const errors = [];
page.on('console', (msg) => {
	if (msg.type() === 'error') errors.push(msg.text());
});
page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));

const shot = (name) => page.screenshot({ path: `${outDir}/${name}.png` });
const settle = (ms = 600) => page.waitForTimeout(ms);
const text = (sel) =>
	page.evaluate(
		(s) => document.querySelector(s)?.textContent?.replace(/\s+/g, ' ').trim() ?? null,
		sel
	);
const box = (sel) => page.locator(sel).first().boundingBox();
const dockWidth = (sel) =>
	page.evaluate((s) => Math.round(document.querySelector(s).getBoundingClientRect().width), sel);
const stored = (key) => page.evaluate((k) => JSON.parse(localStorage.getItem(k) ?? 'null'), key);
const journal = async () => {
	const state = await page.evaluate(
		`(async () => await fetch('/api/v1/world/edit', { cache: 'no-store' }).then((r) => r.json()))()`
	);
	const ops = state.zones.flatMap((zone) => zone.ops);
	const patches = state.rows.flatMap((entry) => entry.patches);
	return { ops, patches, count: ops.length + patches.length };
};

/** A resize: press the grip's middle, drag the inner edge, release.
 * `dx` is in the grip's own direction (a right-hand dock's inner edge is on
 * its left, so widening it means dragging left). */
async function resize(sel, dx) {
	const grip = await box(`${sel} .dock-grip`);
	const dir = (await page.evaluate(
		(s) => document.querySelector(s).classList.contains('right'),
		sel
	))
		? -1
		: 1;
	await page.mouse.move(grip.x + grip.width / 2, grip.y + grip.height / 2);
	await page.mouse.down();
	await page.mouse.move(grip.x + grip.width / 2 + dir * dx, grip.y + grip.height / 2, { steps: 5 });
	await page.mouse.up();
	await settle(400);
}

// --- the figures page: the library and the inspector are docks now ------------

await page.goto(base, { waitUntil: 'load' });
await page.waitForSelector('.dock.left .dock-head', { timeout: 60000 });
check(
	'the library wears the shared head',
	(await text('.dock.left .dock-head .name')) === 'Library',
	await text('.dock.left .dock-head .name')
);
check(
	'it carries a pin and a resize grip',
	(await page.locator('.dock.left .pin').count()) === 1 &&
		(await page.locator('.dock.left .dock-grip').count()) === 1
);

const widthBefore = await dockWidth('.dock.left');
await resize('.dock.left', 90);
const widthAfter = await dockWidth('.dock.left');
check('the grip resizes the dock', widthAfter > widthBefore + 60, `${widthBefore} → ${widthAfter}`);
check(
	'and the width is remembered per dock',
	(await stored('camelot.dock.figures.library'))?.w === widthAfter,
	JSON.stringify(await stored('camelot.dock.figures.library'))
);
await resize('.dock.left', -400);
check(
	'the width stops at its floor',
	(await dockWidth('.dock.left')) === 260,
	`${await dockWidth('.dock.left')}`
);

const floatingBefore = await box('.dock.left');
await page.click('.dock.left .pin');
await settle(500);
const floatingWidth = await dockWidth('.dock.left');
const pinnedAgain = await page.evaluate(() =>
	document.querySelector('.dock.left').classList.contains('floating')
);
check('the pin lets the dock float', pinnedAgain, `${floatingWidth}px wide`);
check(
	'a floating dock is out of the flow (the page takes the room)',
	(await box('.content')).width > floatingBefore.width,
	`content ${Math.round((await box('.content')).width)}`
);

const grip = await box('.dock.left .dock-head');
await page.mouse.move(grip.x + 40, grip.y + 20);
await page.mouse.down();
await page.mouse.move(grip.x + 280, grip.y + 160, { steps: 6 });
await page.mouse.up();
await settle(400);
const movedTo = await box('.dock.left');
check(
	'a floating dock is dragged by its head',
	movedTo.x > floatingBefore.x + 150,
	`${Math.round(floatingBefore.x)} → ${Math.round(movedTo.x)}`
);
const spot = await stored('camelot.dock.figures.library');
check(
	'and its spot is remembered too',
	spot?.pinned === false && spot?.x > 100,
	JSON.stringify(spot)
);
await shot('dock-01-floating');

await page.reload({ waitUntil: 'load' });
await page.waitForSelector('.dock.left.floating', { timeout: 60000 });
await settle(500);
const remembered = await box('.dock.left');
check(
	'the dock comes back where it was left',
	Math.abs(remembered.x - movedTo.x) < 4 && Math.abs(remembered.y - movedTo.y) < 4,
	`${Math.round(remembered.x)},${Math.round(remembered.y)} vs ${Math.round(movedTo.x)},${Math.round(movedTo.y)}`
);
await page.click('.dock.left .pin');
await page.evaluate(() => localStorage.removeItem('camelot.dock.figures.library'));
await settle(300);
check('pinning puts it back in the layout', !(await box('.dock.left.floating').catch(() => null)));

// With a figure loaded, the inspector is a dock of the same family.
await page.fill('.dock.left input[type=search]', 'Boar.NIF');
await settle(900);
await page.click('.dock.left .row');
await page.waitForSelector('.dock.right .dock-head', { timeout: 90000 });
await settle(1500);
check(
	'the inspector is the same family',
	(await text('.dock.right .dock-head .name')) === 'Inspect' &&
		(await page.locator('.dock.right .pin').count()) === 1 &&
		(await page.locator('.dock-right .dock-grip, .dock.right .dock-grip').count()) >= 1,
	await text('.dock.right .dock-head .sub')
);
const inspectorBefore = await dockWidth('.dock.right');
await resize('.dock.right', 70);
check(
	'and it resizes from its inner edge',
	(await dockWidth('.dock.right')) > inspectorBefore + 40,
	`${inspectorBefore} → ${await dockWidth('.dock.right')}`
);
await resize('.dock.right', -200);

// --- the world page: the navigator, the inspector and the journal -------------

await page.goto(`${base}/world`, { waitUntil: 'load' });
await page.waitForFunction('document.querySelectorAll(".dock select option").length > 0', {
	timeout: 120000
});
await page.fill('.dock.left input[placeholder="zone name or id"]', 'Camelot');
await settle(600);
const zoneRow = page.locator('.dock.left .zones button', { hasText: zoneName }).first();
await zoneRow.click();
await page.waitForFunction('window.__worldEditor && window.__worldEditor().zone !== null', {
	timeout: 180000
});
await settle(1500);
await page.keyboard.press('x'); // leave View: the tools and the editor's dock want Edit
await settle(300);
const editing = await page.evaluate(() => !!window.__worldEditor().editing);
if (!editing) await page.getByTitle(/edit — the tools are armed/).click();
await page.waitForSelector('.dock.right .dock-tabs', { timeout: 60000 });
await settle(2500);
await shot('world-01-docks');

check(
	'the navigator is a dock of its own',
	(await text('.dock.left .dock-head .name')) === 'Navigator',
	await text('.dock.left .dock-head .name')
);
check(
	'the editor is a dock with two tabs',
	(await text('.dock.right .dock-tabs'))?.startsWith('Inspect Journal') ?? false,
	await text('.dock.right .dock-tabs')
);
check(
	'its head says where the session stands',
	/(saved|not saved)$/.test((await text('.dock.right .dock-head .sub')) ?? ''),
	await text('.dock.right .dock-head .sub')
);

const before = await journal();
check(
	'the journal tab counts what the daemon holds',
	((await text('.dock.right .dock-tabs')) ?? '').replace(/\D/g, '') ===
		(before.count ? String(before.count) : ''),
	`${before.count} entries, tab says “${await text('.dock.right .dock-tabs')}”`
);

// Select a fixture from the navigator: its own fields land in the inspector.
const fixtureRows = await page.locator('.dock.left .fixtures ul button').count();
await page.locator('.dock.left .fixtures ul button').first().click();
await settle(1500);
check('a fixture picked in the navigator is selected', fixtureRows > 0, `${fixtureRows} rows`);
const context = await text('.dock.right .context');
check(
	'and the inspector shows its own fields',
	/#\d+/.test(context ?? '') && (await page.locator('.dock.right .fields input').count()) >= 2,
	context?.slice(0, 80)
);
check(
	'a ground row explains its z rule',
	((await text('.dock.right .context')) ?? '').includes('z belongs to the terrain'),
	await text('.dock.right .context')
);

// Move it by typing: one op, and the journal says what changed.
const xField = page.locator('.dock.right .fields input[type=number]').first();
const xWas = Number(await xField.inputValue());
await xField.click();
await page.keyboard.press('Control+a');
await page.keyboard.type(String(Math.round(xWas) + 128));
await page.keyboard.press('Tab');
await settle(2500);
const afterType = await journal();
check(
	'typing a field journals one fixture op (and the rows it carries)',
	afterType.ops.length === before.ops.length + 1 &&
		afterType.patches.length > before.patches.length,
	`${before.ops.length}+${before.patches.length} → ${afterType.ops.length}+${afterType.patches.length}`
);
check(
	'the head turns to “not saved”, with the count',
	await page
		.waitForFunction(
			(n) =>
				new RegExp(`· ${n} not saved$`).test(
					document.querySelector('.dock.right .dock-head .sub')?.textContent ?? ''
				),
			afterType.count,
			{ timeout: 15000 }
		)
		.then(() => true)
		.catch(() => false),
	`daemon holds ${afterType.count}, head says “${await text('.dock.right .dock-head .sub')}”`
);
check(
	'the app bar carries the project chip and the two actions',
	/(\d+ edits? to publish|all published)$/.test((await text('.appbar .chip')) ?? '') &&
		(await page.locator('.appbar button:has-text("save")').count()) === 1 &&
		(await page.locator('.appbar button:has-text("undo")').count()) === 1,
	await text('.appbar .chip')
);

// The journal tab: the readable history, both sides of the entry.
await page.click('.dock.right .dock-tabs button:has-text("Journal")');
await settle(500);
const entry = await page.evaluate(() => {
	const li = document.querySelector('.dock.right .entry');
	return li
		? {
				kind: li.querySelector('.jkind')?.textContent?.trim(),
				target: li.querySelector('.jtarget')?.textContent?.trim(),
				diff: li.querySelector('.jdiff')?.textContent?.replace(/\s+/g, ' ').trim(),
				before: li.querySelector('.jdiff s')?.textContent?.trim()
			}
		: null;
});
check(
	'the journal reads as a history, with both sides',
	entry?.kind === 'move' &&
		/^#\d+$/.test(entry.target ?? '') &&
		!!entry.before &&
		entry.diff?.includes('→'),
	JSON.stringify(entry)
);
check(
	'the publish chain lives with it',
	(await page.locator('.dock.right .section:has-text("Publish") button').count()) === 3,
	await text('.dock.right .section:last-of-type h4')
);
await shot('world-02-journal');

// Delete the row the way the operator does: the key, with nothing focused.
await page.click('.dock.right .dock-tabs button:has-text("Inspect")');
await settle(300);
const doomedText = await text('.dock.right .context h3');
const doomed = Number((doomedText ?? '').match(/#(\d+)/)?.[1]);
await page.evaluate('document.activeElement && document.activeElement.blur()');
await page.keyboard.press('Delete');
await settle(3500);
const afterDelete = await journal();
check(
	'Del removes the selection and journals it',
	afterDelete.ops.some((op) => op.action === 'remove' && Number(op.id) === doomed),
	`removes: ${JSON.stringify(afterDelete.ops.filter((op) => op.action === 'remove').map((op) => op.id))}`
);
const editWarn = await page.evaluate(() => {
	// `p.warn`: the Journal tab's own badge wears `.warn` too (it colours the count).
	const node = document.querySelector('.dock.right p.warn');
	return node ? `${node.getAttribute('class')} → ${node.textContent.trim()}` : null;
});
check('the error line stays empty', editWarn === null, editWarn ?? '');

// The dock system works on this page too, and the tab survives a resize.
await resize('.dock.right', 80);
check(
	'the editor dock resizes',
	(await stored('camelot.dock.world.editor'))?.w > 320,
	JSON.stringify(await stored('camelot.dock.world.editor'))
);
check(
	'the tab is still the one we picked',
	(await text('.dock.right .dock-tabs button.on')) === 'Inspect'
);

// Put the session back the way it was found.
await page.click('.dock.right .dock-tabs button:has-text("Journal")');
await page.click('.dock.right button:has-text("revert")');
await page.waitForFunction(
	'window.__worldEditor().mops === 0 && window.__worldEditor().rows === 0',
	{
		timeout: 60000
	}
);
const cleaned = await journal();
check('the session is left clean', cleaned.count === 0, `${cleaned.count} entries`);
await shot('world-03-clean');

check('no console errors', errors.length === 0, errors.slice(0, 3).join(' | '));

await browser.close();
console.log(`\n${failures ? `${failures} FAILED` : 'all checks passed'} — shots in ${outDir}`);
process.exit(failures ? 1 : 0);

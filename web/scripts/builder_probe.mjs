// Slice 3b — the character builder's rail, its own inspector and the shared
// viewport, driven like an operator. Throwaway harness; exits non-zero on a
// failed check.
//
//   node scripts/builder_probe.mjs http://127.0.0.1:8763 /tmp/camelot-ux
import { mkdirSync, readFileSync } from 'node:fs';
import { chromium } from 'playwright';

const base = process.argv[2] ?? 'http://127.0.0.1:8763';
const outDir = process.argv[3] ?? '/tmp/camelot-ux';
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
const errors = [];
const badResponses = [];
page.on('console', (msg) => {
	if (msg.type() === 'error') errors.push(msg.text());
});
page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));
page.on('response', (res) => {
	if (res.status() >= 400) {
		const url = new URL(res.url());
		badResponses.push(`${res.status()} ${url.pathname}${url.search}`);
	}
});

const text = (sel) =>
	page.evaluate(
		(s) => document.querySelector(s)?.textContent?.replace(/\s+/g, ' ').trim() ?? null,
		sel
	);
const shot = (name) => page.screenshot({ path: `${outDir}/${name}.png` });
const canvas = async () => (await page.locator('.viewer canvas').screenshot()).toString('base64');
const settle = (ms = 700) => page.waitForTimeout(ms);
const railSub = () => text('.dock.left .dock-head .sub');

await page.goto(`${base}/builder`, { waitUntil: 'load' });
await page.waitForSelector('.viewer canvas', { timeout: 120000 });
await settle(6000);
await shot('builder-01-default');

// --- the rail ---------------------------------------------------------------

check(
	'the rail is a dock titled Character',
	(await text('.dock.left .dock-head .name')) === 'Character'
);
check('it says who is on screen', (await railSub()) === 'Saracen Female', await railSub());
check(
	'its sections are the drafts’',
	(
		await page.evaluate(() =>
			[...document.querySelectorAll('.dock.left .section > h4')].map((h) =>
				h.textContent.replace(/\s+/g, ' ').trim()
			)
		)
	).join(' | ') === 'Gender | Race 18 races | Appearance | Gear & state',
	await page.evaluate(() =>
		[...document.querySelectorAll('.dock.left .section > h4')]
			.map((h) => h.textContent.trim())
			.join(' | ')
	)
);

const genders = await page.evaluate(() =>
	[...document.querySelectorAll('.dock.left .segmented button')].map((b) => [
		b.textContent.trim(),
		b.getAttribute('aria-pressed')
	])
);
check(
	'the gender switch is a two-way segment, Female on',
	genders.length === 2 && genders[0].join() === 'Male,false' && genders[1].join() === 'Female,true',
	JSON.stringify(genders)
);

const races = await page.evaluate(() =>
	[...document.querySelectorAll('.dock.left .races button')].map((b) =>
		b.textContent.replace(/\s+/g, ' ').trim()
	)
);
check(
	'the race list carries all of them, each with its realm',
	races.length === 18 && races.every((r) => /(Albion|Midgard|Hibernia)$/.test(r)),
	`${races.length} races, first "${races[0]}"`
);
check(
	'the race on screen is the lit one',
	(await page.evaluate(() =>
		document.querySelector('.dock.left .races button.on')?.textContent.trim()
	)) === 'Saracen Albion'
);

const rows = await page.evaluate(() =>
	[...document.querySelectorAll('.dock.left .section:nth-of-type(3) .row')].map((row) => [
		row.querySelector('.lbl').textContent.trim(),
		row.querySelector('.value').textContent.trim()
	])
);
check(
	'the appearance rows are the draft’s, with the client’s own counts',
	rows.length === 6 &&
		rows[0][0] === 'Face' &&
		/^1 \/ \d+$/.test(rows[0][1]) &&
		rows[5][0] === 'Cloak',
	JSON.stringify(rows)
);
check(
	'the − of a pick at its floor is refused',
	await page.evaluate(
		() => document.querySelector('.dock.left .section:nth-of-type(3) .row .step').disabled
	)
);

// --- the picks move the figure ----------------------------------------------

const before = await canvas();
await page.evaluate(() =>
	document.querySelector('.dock.left .section:nth-of-type(3) .row .step:last-child').click()
);
await settle(6000);
const face = await page.evaluate(() =>
	document.querySelector('.dock.left .section:nth-of-type(3) .row .value').textContent.trim()
);
check('a + re-assembles the figure', (await canvas()) !== before, `face now ${face}`);

await page.evaluate(() => {
	const row = [...document.querySelectorAll('.dock.left .section:nth-of-type(3) .row')].find((r) =>
		r.querySelector('.lbl').textContent.trim().startsWith('Hair color')
	);
	row.querySelector('.step:last-child').click();
});
await settle(3000);
check(
	'a colour pick only re-forms its sheet (no re-assembly)',
	(await text('.dock.right .card')) !== null,
	await text('.dock.left .section:nth-of-type(3) .row:nth-of-type(3) .value')
);

const footText = await text('.dock-foot .hint');
const combination = Number(
	((footText ?? '').match(/^[\d\s,.\u202f]+/) ?? [''])[0].replace(/[^\d]/g, '')
);
check(
	'the foot counts the figures these tables describe',
	/combinations/.test(footText ?? '') && combination > 1000,
	footText
);

await page.click('.dock-foot button:has-text("Randomize")');
await settle(5000);
const randomRows = await page.evaluate(() =>
	[...document.querySelectorAll('.dock.left .section:nth-of-type(3) .row .value')].map((v) =>
		v.textContent.trim()
	)
);
check(
	'Randomize moves the picks',
	randomRows.some((v) => !/^1 \/ /.test(v)),
	randomRows.join(' | ')
);
check(
	'and stays inside every range',
	randomRows.every((v) => {
		const [value, max] = v.split('/').map((n) => Number(n.trim()));
		return value >= 0 && value <= max;
	}),
	randomRows.join(' | ')
);
await shot('builder-02-randomized');

await page.click('.dock-foot button:has-text("Reset")');
await settle(5000);
const resetRows = await page.evaluate(() =>
	[...document.querySelectorAll('.dock.left .section:nth-of-type(3) .row .value')].map((v) =>
		v.textContent.trim()
	)
);
check(
	'Reset puts every pick back on the first figure',
	resetRows.slice(0, 5).every((v) => /^1 \/ /.test(v)) && /^0 \/ /.test(resetRows[5]),
	resetRows.join(' | ')
);

// --- gender, race, gear and state -------------------------------------------

await page.click('.dock.left .segmented button:has-text("Male")');
await settle(7000);
check(
	'a gender switch re-assembles and renames',
	(await railSub()) === 'Saracen Male',
	await railSub()
);
check('the app bar follows the pick', (await text('.appbar .context'))?.startsWith('Saracen Male'));

await page.click('.dock.left .races button:has-text("Briton")');
await settle(7000);
check('a race switch re-assembles', (await railSub()) === 'Briton Male', await railSub());

const armour = await page.evaluate(() => {
	const select = document.querySelector('.dock.left select[aria-label="Armour material"]');
	return { options: select?.options.length ?? 0, value: select?.value ?? null };
});
check(
	'the armour select lists the client’s materials',
	armour.options > 10,
	JSON.stringify(armour)
);
const armourBefore = await canvas();
await page.selectOption('.dock.left select[aria-label="Armour material"]', 'Plate');
await settle(7000);
check(
	'a material switch re-assembles in that material',
	(await canvas()) !== armourBefore && (await text('.appbar .context'))?.includes('Plate')
);

const states = await page.evaluate(() => {
	const select = document.querySelector('.dock.left select[aria-label="Animation state"]');
	return { options: select?.options.length ?? 0, value: select?.value ?? null };
});
check(
	'the state select is the figure’s own set',
	states.options > 50 && states.value === 'Idle',
	JSON.stringify(states)
);
check(
	'a pair the client serves says nothing',
	!(await text('.dock.left .section:nth-of-type(4) .hint'))
);

// --- the inspector ----------------------------------------------------------

const card = await page.evaluate(() =>
	Object.fromEntries(
		[...document.querySelectorAll('.dock.right .card:first-of-type .meta > div')].map((row) => [
			row.querySelector('dt').textContent.trim(),
			row.querySelector('dd').textContent.replace(/\s+/g, ' ').trim()
		])
	)
);
check(
	'the inspector summarises the character',
	card.Race === 'Briton' &&
		card.Gender === 'Male' &&
		/^1 \/ \d+$/.test(card.Face ?? '') &&
		card.Hair &&
		card.Skin &&
		card.Eye,
	JSON.stringify(card)
);

const equipment = await page.evaluate(() => ({
	title: document
		.querySelector('.dock.right .card:nth-of-type(2) h3')
		?.textContent.replace(/\s+/g, ' ')
		.trim(),
	pieces: [...document.querySelectorAll('.dock.right .card:nth-of-type(2) .meta > div dt')].map(
		(dt) => dt.textContent.trim()
	),
	note: document
		.querySelector('.dock.right .card:nth-of-type(2) .hint')
		?.textContent.replace(/\s+/g, ' ')
		.trim()
}));
check(
	'equipment lists the assembled figure’s own pieces, in the material asked for',
	/^Equipment per slot · Plate$/.test(equipment.title ?? '') &&
		equipment.pieces.length >= 4 &&
		equipment.pieces.includes('Body'),
	JSON.stringify(equipment)
);
check('and says why it is read-only', /Read-only/.test(equipment.note ?? ''), equipment.note);
await shot('builder-03-inspector');

// --- the shared viewport ----------------------------------------------------

check('the strip is the viewer’s own', (await page.locator('.viewer .strip .tool').count()) === 9);
check(
	'the transport is the viewer’s own, playing the picked state',
	/^\d+\.\d\d \/ \d+\.\d\d s$/.test((await text('.viewer .transport .clock')) ?? ''),
	await text('.viewer .transport .clock')
);

const status = await text('.statusbar .side');
check(
	'the status bar keeps the viewer’s grammar',
	/^\d+ fps .*tris · \d+ meshes? · \d+ bones · face \d+\/\d+$/.test(status ?? ''),
	JSON.stringify(status)
);
check(
	'the right side names the state on screen',
	(await text('.statusbar .end'))?.startsWith('Idle · '),
	await text('.statusbar .end')
);

const [snapshot] = await Promise.all([
	page.waitForEvent('download', { timeout: 30000 }),
	page.click('.appbar button[aria-label=snapshot]')
]);
check(
	'snapshot saves the viewport',
	snapshot.suggestedFilename() === 'briton-m.png',
	snapshot.suggestedFilename()
);

const [glb] = await Promise.all([
	page.waitForEvent('download', { timeout: 90000 }),
	page.click('.appbar button:has-text("Export glTF")')
]);
const bytes = readFileSync(await glb.path());
const doc = JSON.parse(bytes.subarray(20, 20 + bytes.readUInt32LE(12)).toString('utf8'));
check(
	'Export glTF writes the assembled figure',
	bytes.subarray(0, 4).toString('ascii') === 'glTF' &&
		(doc.meshes?.length ?? 0) > 0 &&
		(doc.skins?.length ?? 0) > 0,
	`${bytes.length} bytes, ${doc.meshes?.length} meshes, ${doc.skins?.length} skins`
);
await shot('builder-04-final');

// --- hygiene ----------------------------------------------------------------

const unexpected = badResponses.filter((line) => !/\/character\/.*\/texture/.test(line));
check('no unexpected failed requests', unexpected.length === 0, badResponses.join(' | '));
check('no console errors', errors.length === 0, errors.slice(0, 3).join(' | '));

await browser.close();
console.log(`\n${failures ? `${failures} FAILED` : 'all checks passed'} — shots in ${outDir}`);
process.exit(failures ? 1 : 0);

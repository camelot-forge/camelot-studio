// Slice 3a — the figure viewer driven like an operator: the strip, the
// transport, the inspector's cards, the bones overlay, the snapshot and the
// glTF export. Throwaway harness; exits non-zero on a failed check.
//
//   node scripts/viewer_probe.mjs http://127.0.0.1:8763 /tmp/camelot-ux Boar.NIF
import { mkdirSync, readFileSync } from 'node:fs';
import { chromium } from 'playwright';

const base = process.argv[2] ?? 'http://127.0.0.1:8763';
const outDir = process.argv[3] ?? '/tmp/camelot-ux';
const figure = process.argv[4] ?? 'Boar.NIF';
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
const settle = (ms = 600) => page.waitForTimeout(ms);

await page.goto(base, { waitUntil: 'load' });
await page.waitForSelector('.library .row', { timeout: 60000 });
await page.fill('.library input[type=search]', figure);
await settle(900);
await page.click('.library .row');
await page.waitForSelector('.viewer canvas', { timeout: 60000 });
await page.waitForSelector('.dock.right .card', { timeout: 60000 });
await settle(5000);
await shot('viewer-01-loaded');

// --- the shell ---------------------------------------------------------------

const appbar = await text('.appbar');
check('app bar names the figure', appbar?.includes(figure), appbar);
check(
	'app bar carries snapshot, open and export',
	(await page.locator('.appbar button[aria-label=snapshot]').count()) === 1 &&
		(await page.locator('.appbar button:has-text("Open…")').count()) === 1 &&
		(await page.locator('.appbar button:has-text("Export glTF")').count()) === 1
);
check(
	'the app switch still sits in the middle',
	(await text('.appbar nav')) === 'FiguresBuilderWorld',
	JSON.stringify(await text('.appbar nav'))
);
check('the library dock is titled', (await text('.library .dock-head .name')) === 'Library');
check('the inspector dock is titled', (await text('.dock.right .dock-head .name')) === 'Inspect');

const stripTitles = await page.evaluate(() =>
	[...document.querySelectorAll('.strip .tool')].map((b) => b.getAttribute('title') ?? '')
);
check(
	'the strip is vertical with the drafts’ order',
	stripTitles.length === 9 &&
		stripTitles[0].includes('frame the figure') &&
		stripTitles[1].includes('turntable') &&
		stripTitles[2].includes('wireframe') &&
		stripTitles[3].includes('grid') &&
		stripTitles[4].includes('axes') &&
		stripTitles[5].includes('snapshot') &&
		stripTitles[6].includes('−90°') &&
		stripTitles[7].includes('which axis') &&
		stripTitles[8].includes('+90°'),
	stripTitles.join(' | ')
);
check(
	'the strip is 52 px wide (the drafts’ dock)',
	(await page.evaluate(() => document.querySelector('.strip').getBoundingClientRect().width)) === 52
);

// --- the transport ------------------------------------------------------------

const transport = await page.evaluate(() => {
	const input = document.querySelector('.transport input[type=range]');
	return {
		text: document.querySelector('.transport')?.textContent?.replace(/\s+/g, ' ').trim() ?? null,
		max: input ? Number(input.max) : null,
		ticks: document.querySelectorAll('.transport .tick').length,
		loopDisabled: document.querySelectorAll('.transport .chip[disabled]').length
	};
});
check(
	'the transport reads playhead / length',
	/^\d+\.\d\d \/ \d+\.\d\d s/.test(transport.text ?? ''),
	transport.text
);
check(
	'the scrub carries the clip’s key times',
	transport.ticks > 0,
	`${transport.ticks} ticks over ${transport.max}s`
);

const running = await page.evaluate(async () => {
	const read = () => document.querySelector('.transport .clock').textContent.trim();
	const before = read();
	await new Promise((r) => setTimeout(r, 900));
	return { before, after: read() };
});
check(
	'the clip plays on load',
	running.before !== running.after,
	`${running.before} → ${running.after}`
);

await page.click('.transport .play');
const pausedA = await text('.transport .clock');
await settle(800);
const pausedB = await text('.transport .clock');
check('pause holds the clock', pausedA === pausedB, `${pausedA} == ${pausedB}`);
check(
	'the play button says what a click will do',
	(await page.locator('.transport .play').getAttribute('title')) === 'play (space)'
);

const poseBefore = await canvas();
await page.evaluate(() => {
	const input = document.querySelector('.transport input[type=range]');
	input.value = String(Number(input.max) * 0.5);
	input.dispatchEvent(new Event('input', { bubbles: true }));
});
await settle(700);
const poseAfter = await canvas();
check('a scrub redraws the figure while paused', poseBefore !== poseAfter);
check(
	'the read-out follows the scrub',
	(await text('.transport .clock'))?.startsWith(((transport.max ?? 0) / 2).toFixed(2)),
	await text('.transport .clock')
);

// --- the inspector's cards ---------------------------------------------------

const cards = await page.evaluate(() =>
	[...document.querySelectorAll('.dock.right .card > h3')].map((h) =>
		h.textContent.replace(/\s+/g, ' ').trim()
	)
);
check(
	'the inspector holds the drafts’ cards',
	cards.length === 4 &&
		cards[0].startsWith('Model') &&
		cards[1].startsWith('Animation') &&
		cards[2].startsWith('Bones') &&
		cards[3].startsWith('Skins'),
	cards.join(' | ')
);

const model = await page.evaluate(() =>
	Object.fromEntries(
		[...document.querySelectorAll('.dock.right .card:first-of-type .meta > div')].map((row) => [
			row.querySelector('dt').textContent.trim(),
			row.querySelector('dd').textContent.trim()
		])
	)
);
check(
	'the Model card counts the figure',
	model.File === figure && Number(model.Meshes) > 0 && Number(model.Triangles) > 0,
	JSON.stringify(model)
);
check('the Model card counts the rig', Number(model.Bones) > 0, model.Bones);

const anim = await page.evaluate(() => ({
	meta: document
		.querySelectorAll('.dock.right .card')[1]
		.querySelector('.card-meta')
		.textContent.trim(),
	rows: [...document.querySelectorAll('.dock.right .states .state')].map((r) =>
		r.textContent.replace(/\s+/g, ' ').trim()
	),
	disclosure: document
		.querySelectorAll('.dock.right .card')[1]
		.querySelector('.link')
		?.textContent?.replace(/\s+/g, ' ')
		.trim()
}));
check(
	"the Animation card names the figure's own set",
	anim.meta === 'the figure’s own set',
	anim.meta
);
check(
	'the set opens on the client’s own order, capped',
	anim.rows.length === 8 && anim.rows[0].startsWith('Walk') && anim.rows[2].startsWith('Idle'),
	anim.rows.join(' | ')
);
check(
	'the rest of the set is one click away',
	/^Show all \d+ slots$/.test(anim.disclosure ?? ''),
	anim.disclosure
);
check(
	'a clamp slot says it plays once',
	anim.rows.some((r) => r.includes('once')),
	anim.rows.join(' | ')
);

const bones = await page.evaluate(() => ({
	meta: document
		.querySelectorAll('.dock.right .card')[2]
		.querySelector('.card-meta')
		.textContent.trim(),
	rows: document.querySelectorAll('.dock.right .bones .bone').length,
	first: document
		.querySelector('.dock.right .bones .bone')
		?.textContent?.replace(/\s+/g, ' ')
		.trim(),
	search: !!document.querySelector('.dock.right .search input'),
	eye: !!document.querySelector('.dock.right .eye')
}));
check(
	'the Bones card lists the rig with its channels',
	bones.rows > 0 && /^(–|\d+) Bip01/.test(bones.first ?? ''),
	`${bones.rows} rows, first "${bones.first}"`
);
check('the Bones card carries search and the overlay eye', bones.search && bones.eye);

const filtered = await page.evaluate(async () => {
	const input = document.querySelector('.dock.right .search input');
	input.value = '13';
	input.dispatchEvent(new Event('input', { bubbles: true }));
	await new Promise((r) => setTimeout(r, 300));
	return [...document.querySelectorAll('.dock.right .bones .bone')].map((b) =>
		b.textContent.replace(/\s+/g, ' ').trim()
	);
});
check(
	'searching a channel index answers for it',
	filtered.length > 0 && filtered.every((row) => row.startsWith('13 ') || row.startsWith('– ')),
	filtered.join(' | ')
);
await page.evaluate(() => {
	const input = document.querySelector('.dock.right .search input');
	input.value = '';
	input.dispatchEvent(new Event('input', { bubbles: true }));
});

const skins = await text('.dock.right .card:last-of-type');
check('the Skins card names the mesh slots', /skin 0 \S+\.\w{3,4}/.test(skins ?? ''), skins);

// --- playable / refused states ------------------------------------------------

const refused = await page.evaluate(async () => {
	document.querySelectorAll('.dock.right .card')[1].querySelector('.link').click();
	await new Promise((r) => setTimeout(r, 400));
	const rows = [...document.querySelectorAll('.dock.right .states .state')];
	const row = rows.find((r) => r.textContent.includes('EM Dance'));
	if (!row) return { rows: rows.length, found: false };
	row.click();
	await new Promise((r) => setTimeout(r, 5000));
	const item = [...document.querySelectorAll('.dock.right .states li')].find((li) =>
		li.textContent.includes('EM Dance')
	);
	return {
		rows: rows.length,
		found: true,
		marked: item.querySelector('.state').classList.contains('refused'),
		why: item.querySelector('.why')?.textContent?.trim() ?? null
	};
});
check('the whole set can be shown', refused.rows > 8, `${refused.rows} slots`);
check(
	'a pair the rig cannot bind is refused, and the card says why',
	refused.found && refused.marked && /no tracks for state: EM Dance/.test(refused.why ?? ''),
	refused.why
);
await shot('viewer-02-refused');

const play = () =>
	page
		.locator('.transport .play')
		.getAttribute('title')
		.then((title) => (title === 'play (space)' ? page.click('.transport .play') : null));

await play();
const played = await page.evaluate(async () => {
	const row = [...document.querySelectorAll('.dock.right .states .state')].find((r) =>
		r.textContent.trim().startsWith('Run')
	);
	row.click();
	await new Promise((r) => setTimeout(r, 6000));
	return {
		title: row.getAttribute('title'),
		clock: document.querySelector('.transport .clock').textContent.trim(),
		status: document.querySelector('.statusbar .end').textContent.replace(/\s+/g, ' ').trim(),
		active:
			document
				.querySelector('.dock.right .states .state.on')
				?.textContent?.replace(/\s+/g, ' ')
				.trim() ?? null,
		lit: document.querySelectorAll('.dock.right .states .state.on').length
	};
});
check(
	'a playable state is served and lit',
	played.active?.startsWith('Run') && played.title === 'play BOAR_RUN.kfa',
	`${played.active} · ${played.title}`
);
check('one row lights, not every alias of the clip', played.lit === 1, `${played.lit} lit rows`);
check(
	'the clock takes the slot’s own game timing',
	played.clock === '0.50 s / 0.50 s' || /^0\.\d\d \/ 0\.50 s$/.test(played.clock),
	played.clock
);
check(
	'the status bar carries the clip on screen',
	played.status.includes('BOAR_RUN.kfa'),
	played.status
);

await play();
const clampClip = await page.evaluate(async () => {
	const row = [...document.querySelectorAll('.dock.right .states .state')].find((r) =>
		r.textContent.trim().startsWith('Death')
	);
	row.click();
	await new Promise((r) => setTimeout(r, 7000));
	const chip = [...document.querySelectorAll('.transport .chip')][0];
	return {
		disabled: chip.disabled,
		title: chip.getAttribute('title'),
		clock: document.querySelector('.transport .clock').textContent.trim(),
		playTitle: document.querySelector('.transport .play').getAttribute('title')
	};
});
check(
	'a clamp clip refuses the loop switch (the client plays it once)',
	clampClip.disabled && /clamp/.test(clampClip.title ?? ''),
	clampClip.title
);
check(
	'a one-shot stops at its end instead of wrapping',
	/^1\.27 \/ 1\.27 s$/.test(clampClip.clock) && clampClip.playTitle === 'play (space)',
	`${clampClip.clock} · ${clampClip.playTitle}`
);
await shot('viewer-03-clamp-end');

const speed = await page.evaluate(async () => {
	const chip = [...document.querySelectorAll('.transport .chip')][1];
	const before = chip.textContent.trim();
	chip.click();
	await new Promise((r) => setTimeout(r, 200));
	return { before, after: chip.textContent.trim() };
});
check(
	'the speed chip cycles',
	speed.before === '1×' && speed.after === '2×',
	`${speed.before} → ${speed.after}`
);

// --- the strip as a tool you can move ----------------------------------------

const stripAt = () =>
	page.evaluate(() => {
		const r = document.querySelector('.strip').getBoundingClientRect();
		return [Math.round(r.x), Math.round(r.y)];
	});
// The strip's own coordinates are relative to the viewport (the viewer dock);
// the DOM rect is relative to the page, so the two only meet through this.
const viewerOrigin = await page.evaluate(() => {
	const r = document.querySelector('.viewer').getBoundingClientRect();
	return [Math.round(r.x), Math.round(r.y)];
});
const stripState = async () => {
	const [x, y] = await stripAt();
	return [x - viewerOrigin[0], y - viewerOrigin[1]];
};
const gripAt = async () => {
	const box = await page.locator('.strip .grip').boundingBox();
	return [box.x + box.width / 2, box.y + box.height / 2];
};

const stripFrom = await stripAt();
const [gx, gy] = await gripAt();
await page.mouse.move(gx, gy);
await page.mouse.down();
await page.mouse.move(gx + 300, gy + 150, { steps: 6 });
await page.mouse.up();
await settle(400);
const stripMoved = await stripAt();
const stripStored = await page.evaluate(() =>
	JSON.parse(localStorage.getItem('camelot.viewer.strip') ?? 'null')
);
check(
	'the strip drags by its grip',
	stripMoved[0] > stripFrom[0] + 250 && stripMoved[1] > stripFrom[1] + 100,
	`${stripFrom} → ${stripMoved}`
);
check(
	'where the tools were left is remembered',
	JSON.stringify([stripStored?.x, stripStored?.y]) === JSON.stringify(await stripState()),
	`stored ${JSON.stringify(stripStored)} vs ${await stripState()}`
);

const [bx, by] = await gripAt();
await page.mouse.move(bx, by);
await page.mouse.down();
await page.mouse.up();
await settle(400);
check(
	'a click on the grip puts it back',
	JSON.stringify(await stripState()) === '[12,12]',
	`${await stripState()}`
);
check('the spot survives a reload', JSON.stringify(await stripState()) === '[12,12]');
await shot('viewer-04-strip');

// --- the switches, the overlay, the keyboard ---------------------------------

const before3d = await canvas();
const overlay = await page.evaluate(async () => {
	const byTitle = (needle) =>
		[...document.querySelectorAll('.strip .tool')].find((b) =>
			(b.getAttribute('title') ?? '').includes(needle)
		);
	byTitle('wireframe').click();
	await new Promise((r) => setTimeout(r, 1200));
	const eye = document.querySelector('.dock.right .eye');
	const bonesOff = eye.getAttribute('aria-pressed');
	eye.click();
	await new Promise((r) => setTimeout(r, 1200));
	const bonesOn = eye.getAttribute('aria-pressed');
	return { bonesOff, bonesOn, wired: true };
});
const wired3d = await canvas();
check('the wireframe switch redraws the figure', wired3d !== before3d);
check('the eye overlays the skeleton', overlay.bonesOff === 'false' && overlay.bonesOn === 'true');
await page.evaluate(async () => {
	const byTitle = (needle) =>
		[...document.querySelectorAll('.strip .tool')].find((b) =>
			(b.getAttribute('title') ?? '').includes(needle)
		);
	byTitle('wireframe').click();
	document.querySelector('.dock.right .eye').click();
	await new Promise((r) => setTimeout(r, 600));
});

const keys = await page.evaluate(async () => {
	window.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', bubbles: true }));
	await new Promise((r) => setTimeout(r, 400));
	const afterSpace = document.querySelector('.transport .play').getAttribute('title');
	window.dispatchEvent(new KeyboardEvent('keydown', { key: 'f', bubbles: true }));
	await new Promise((r) => setTimeout(r, 400));
	return { afterSpace };
});
check('space plays and pauses', keys.afterSpace === 'pause (space)', keys.afterSpace);

const fps = await text('.statusbar .side');
check(
	'the status bar reads the frame rate and the counts',
	/^\d+ fps \S+ tris · \d+ mesh(es)? · \d+ bones$/.test(fps ?? ''),
	JSON.stringify(fps)
);

// --- the two downloads --------------------------------------------------------

const [snapshot] = await Promise.all([
	page.waitForEvent('download', { timeout: 30000 }),
	page.click('.appbar button[aria-label=snapshot]')
]);
check(
	'snapshot saves the viewport',
	snapshot.suggestedFilename() === `${figure.replace(/\.nif$/i, '')}.png`,
	snapshot.suggestedFilename()
);

const [glb] = await Promise.all([
	page.waitForEvent('download', { timeout: 60000 }),
	page.click('.appbar button:has-text("Export glTF")')
]);
const glbPath = await glb.path();
const bytes = readFileSync(glbPath);
const jsonLength = bytes.readUInt32LE(12);
const doc = JSON.parse(bytes.subarray(20, 20 + jsonLength).toString('utf8'));
check(
	'export glTF writes a real GLB of the figure',
	glb.suggestedFilename() === `${figure.replace(/\.nif$/i, '')}.glb` &&
		bytes.subarray(0, 4).toString('ascii') === 'glTF' &&
		(doc.meshes?.length ?? 0) > 0 &&
		(doc.nodes?.length ?? 0) > 0,
	`${bytes.length} bytes, ${doc.meshes?.length} meshes, ${doc.skins?.length} skins, ${doc.nodes?.length} nodes`
);
await shot('viewer-05-final');

// The one 4xx this run must produce is the refusal the card reports; a 404 for
// it is also the only console error Chromium raises here.
const unexpected = badResponses.filter((line) => !line.includes('/anim?state=EM'));
check(
	'no unexpected failed requests',
	unexpected.length === 0 && badResponses.length === 1,
	badResponses.join(' | ')
);
const realErrors = errors.filter((line) => !/Failed to load resource.*404/.test(line));
check('no console errors', realErrors.length === 0, realErrors.slice(0, 4).join(' | '));

await browser.close();
console.log(`\n${failures ? `${failures} FAILED` : 'all checks passed'} — shots in ${outDir}`);
process.exit(failures ? 1 : 0);

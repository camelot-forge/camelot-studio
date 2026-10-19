// P8 — the re-base, driven like an operator (`notes/project.md`
// §D16-§D18): a client that moved under a project, the row that says so, the
// panel that names what it touches, and the two decisions.
//
//   node scripts/rebase_probe.mjs http://127.0.0.1:8763 /tmp/camelot-ux
//
// The client it copies *from* is the one the daemon serves, or
// `$CAMELOT_CLIENT_SOURCE` when the daemon has been switched to a scratch of a
// previous run.
//
// The daemon this drives may serve any project: the probe builds a **scratch
// client** of its own under the second argument (a few MB copied out of the
// daemon's client — the operator's own copy is only ever read), creates a
// project against it through Home's own door, records one edit, and then moves
// that client under the project by swapping one zone's `csv` archive for
// another's. The edit's row then holds values it never expected: a conflict,
// which is the panel's whole point.
//
// Throwaway harness; exits non-zero on a failed check.
import {
	copyFileSync,
	cpSync,
	existsSync,
	mkdirSync,
	readdirSync,
	rmSync,
	statSync
} from 'node:fs';
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
page.setDefaultTimeout(120000);
const errors = [];
page.on('console', (msg) => {
	if (msg.type() === 'error') errors.push(msg.text());
});
page.on('pageerror', (error) => errors.push(`pageerror: ${error.message}`));

const shot = (name) => page.screenshot({ path: `${outDir}/${name}.png` });
const text = (sel) =>
	page.evaluate(
		(s) => document.querySelector(s)?.textContent?.replace(/\s+/g, ' ').trim() ?? null,
		sel
	);
const texts = (sel) =>
	page.evaluate(
		(s) =>
			Array.from(document.querySelectorAll(s)).map(
				(n) => n.textContent?.replace(/\s+/g, ' ').trim() ?? ''
			),
		sel
	);
async function until(probe, ms = 60000) {
	const started = Date.now();
	for (;;) {
		const value = await probe();
		if (value) return value;
		if (Date.now() - started > ms) return null;
		await page.waitForTimeout(200);
	}
}
const api = async (path) => {
	const res = await fetch(`${base}/api/v1${path}`);
	return { status: res.status, body: await res.json().catch(() => null) };
};
const post = async (path, payload) => {
	const res = await fetch(`${base}/api/v1${path}`, {
		method: 'POST',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify(payload)
	});
	return { status: res.status, body: await res.json().catch(() => null) };
};

// --- the scratch client --------------------------------------------------------
// What a project's base is made of: its own binary, the tables, the zone index,
// and the two archives one zone's data lives in. A few MB, copied — never the
// operator's client, which this probe only reads.
const home = await api('/home');
const served = process.env.CAMELOT_CLIENT_SOURCE || home.body?.daemon?.client || '';
check('the daemon names the client it serves', served.length > 0, served);
if (!served) {
	await browser.close();
	process.exit(1);
}
// The door switches the daemon to the scratch client, so a *second* run would
// copy the first run's scratch. Say it instead of building a client out of one.
if (served.startsWith(outDir)) {
	console.log(
		`\nFAIL the daemon serves ${served}, a scratch client of a previous run.\n` +
			'Restart it on a real client, or name one with CAMELOT_CLIENT_SOURCE.'
	);
	await browser.close();
	process.exit(1);
}

const zonesDir = `${served}/zones`;
const csvOf = (folder) =>
	readdirSync(`${zonesDir}/${folder}`).find((f) => /^csv\d{3}\.mpk$/i.test(f)) ?? '';
// Two zones whose `csv` archive is *there* and not empty: the real client ships
// a zero-byte `csvNNN.mpk` (ZONE009), which reads as absent, and this probe
// needs a donor it can actually put in place of the edited zone's own.
const zoneFolders = readdirSync(zonesDir)
	.filter((name) => /^zone\d{3}$/i.test(name))
	.filter((name) => {
		const csv = csvOf(name);
		return csv !== '' && statSync(`${zonesDir}/${name}/${csv}`).size > 0;
	})
	.sort();
check(
	'the client ships at least two zones with their own csv archive',
	zoneFolders.length >= 2,
	zoneFolders.slice(0, 4).join(', ')
);
if (zoneFolders.length < 2) {
	await browser.close();
	process.exit(1);
}

const scratch = `${outDir}/probe-client`;
rmSync(scratch, { recursive: true, force: true });
mkdirSync(`${scratch}/zones`, { recursive: true });
for (const name of ['game1127.dll', 'gamedata.mpk']) {
	if (existsSync(`${served}/${name}`)) copyFileSync(`${served}/${name}`, `${scratch}/${name}`);
}
copyFileSync(`${zonesDir}/zones.mpk`, `${scratch}/zones/zones.mpk`);
const [first, second] = zoneFolders;
for (const name of [first, second]) {
	cpSync(`${zonesDir}/${name}`, `${scratch}/zones/${name}`, { recursive: true });
}

// --- a project on it, and one real edit ---------------------------------------
const projectDir = `${outDir}/probe-rebase`;
rmSync(projectDir, { recursive: true, force: true });
const created = await post('/home/new', {
	name: 'Probe Rebase',
	dir: projectDir,
	client: scratch,
	staging: false,
	open: true
});
check(
	'the door creates a project whose base is the scratch client',
	created.status === 200 && created.body?.opened === true,
	`${created.status} · ${created.body?.resources} resources`
);

const index = await api('/world/index');
let edit = null;
for (const zone of (index.body?.zones ?? []).filter((z) => z.available)) {
	const payload = await api(`/world/zone/${zone.id}`);
	const fixture = (payload.body?.fixtures ?? [])[0];
	if (!fixture) continue;
	const answer = await post(`/world/zone/${zone.id}/edits`, {
		kind: 'move',
		id: fixture.id,
		x: fixture.x + 8,
		y: fixture.y
	});
	if (answer.status === 200) edit = { zone: zone.id, folder: zone.folder ?? '', id: fixture.id };
	break;
}
check('one edit lands in it', edit !== null, JSON.stringify(edit));
if (!edit) {
	await browser.close();
	process.exit(1);
}

const editedFolder =
	zoneFolders.find((name) => name.toLowerCase() === String(edit.folder).toLowerCase()) ??
	zoneFolders.find((name) => name.toLowerCase().endsWith(String(edit.zone).padStart(3, '0'))) ??
	first;
const otherFolder = editedFolder === first ? second : first;
const archiveOf = (folder) =>
	`${scratch}/zones/${folder}/${readdirSync(`${scratch}/zones/${folder}`).find((f) => /^csv\d{3}\.mpk$/i.test(f))}`;
const moved = archiveOf(editedFolder);
const donor = archiveOf(otherFolder);
const before = (await api(`/home/health?path=${encodeURIComponent(projectDir)}`)).body;
check('before the move the base reads ok', before?.state === 'ok', `${before?.state}`);

// The client moves under the project: the zone the edit targets now holds
// another zone's placements, so the op's row is not what it was written against.
copyFileSync(donor, moved);
const after = (await api(`/home/health?path=${encodeURIComponent(projectDir)}`)).body;
check(
	'the base moved, and the row says so',
	after?.state === 'changed' && (after?.findings ?? []).length >= 1,
	`${after?.state} · ${after?.detail}`
);

const view = (await api(`/home/rebase?path=${encodeURIComponent(projectDir)}`)).body;
check(
	'the re-base view names the resource that moved, with both fingerprints',
	view?.clean === false &&
		view.resources.some((item) => item.state === 'moved' && item.ours && item.theirs),
	`${view?.moved} moved · ${view?.conflicts} conflict(s)`
);
check(
	'the journal is replayed against it, and the op no longer fits',
	(view?.journal ?? []).some((entry) => entry.ops.some((op) => op.verdict === 'conflict')),
	JSON.stringify((view?.journal ?? []).flatMap((e) => e.ops.map((o) => o.verdict)))
);

// --- the page: the chip, the panel, the two decisions --------------------------
// Playwright locators here, not `querySelector`: `:has-text()` is Playwright's
// own, and the row is found by the project's name.
await page.goto(`${base}/home`, { waitUntil: 'domcontentloaded' });
const row = page.locator('li.row').filter({ hasText: 'Probe Rebase' });
const chip = row.locator('.chip.action');
const chips = async () =>
	(await row.locator('.chips').count()) ? await row.locator('.chips').innerText() : '';
check(
	'the row offers the re-base door once its client moved',
	(await until(async () => ((await chip.count()) === 1 ? true : null))) === true,
	await chips()
);
await shot('rebase-1-row');

await chip.click();
const panel = page.locator('dialog[aria-label^="re-base"]');
await panel.locator('.resources li').first().waitFor();
check('the panel opens on the row', (await panel.count()) === 1);
const listed = await panel.locator('.resources li').allInnerTexts();
check(
	"and lists what moved, in the client's own words",
	// the panel uppercases the state in CSS; `innerText` reads it as rendered
	listed.some((line) => line.toLowerCase().includes('moved') && line.includes('mpk')),
	listed.join(' | ')
);
const verdicts = (await panel.locator('.ops .verdict').allInnerTexts()).map((v) => v.toLowerCase());
check(
	'with the journal beside it, one verdict per op',
	verdicts.includes('conflict'),
	verdicts.join(', ')
);
const detail = await panel.locator('.ops .detail').allInnerTexts();
check(
	'and the two values the conflict is about',
	detail.some((d) => d.trim().length > 0),
	detail.join(' | ')
);
await shot('rebase-2-panel');

await panel.locator('button:has-text("Keep ours")').click();
await until(async () => ((await panel.count()) === 0 ? true : null));
check(
	'the page says the decision ran',
	(await text('body'))?.includes('kept ours'),
	(await text('.notice')) ?? ''
);
const pinned = await until(async () => {
	const label = await chips();
	return label.includes('kept ours') ? label : null;
});
check('and the row stops shouting, without hiding it', pinned !== null, pinned ?? '');
await shot('rebase-3-pinned');

const kept = (await api(`/home/rebase?path=${encodeURIComponent(projectDir)}`)).body;
check(
	'the pin is written, per resource and per value',
	kept?.pinned >= 1 && kept.resources.some((item) => item.state === 'pinned' && item.theirs),
	`${kept?.pinned} pinned`
);

await chip.click();
const adopt = panel.locator('button:has-text("Adopt theirs")');
await adopt.waitFor();
await adopt.click();
await until(async () => ((await panel.count()) === 0 ? true : null));
const adopted = await until(async () => {
	const label = await chips();
	return label.includes('client ok') ? label : null;
});
check('adopting theirs makes the row read ok again', adopted !== null, adopted ?? '');
const done = (await api(`/home/rebase?path=${encodeURIComponent(projectDir)}`)).body;
check(
	'and the base is the one the client holds now',
	done?.clean === true && done?.pinned === 0,
	JSON.stringify({ clean: done?.clean, pinned: done?.pinned })
);
check(
	'the chip is gone once there is nothing to decide',
	(await until(async () => ((await chip.count()) === 0 ? true : null))) === true
);
await shot('rebase-4-adopted');

check('the page kept a clean console', errors.length === 0, errors.slice(0, 3).join(' | '));

await browser.close();
console.log(failures === 0 ? '\nall checks passed' : `\n${failures} check(s) failed`);
process.exit(failures === 0 ? 0 : 1);

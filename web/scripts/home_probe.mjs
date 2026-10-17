// P2 — Home, driven like an operator (`notes/home.md`): what this daemon
// serves, the machine's projects with their health, the path field, the empty
// state, and the four doors — open (D3's switch), create (D4), import, clone —
// against the daemon's own client. Throwaway harness; exits non-zero on a
// failed check.
//
//   node scripts/home_probe.mjs http://127.0.0.1:8763 /tmp/camelot-ux /tmp/home-probe-config
//
// The daemon must serve a *project* (set it up with `camelot project new` so
// the health check has a real base to read back), and the third argument is
// the config directory that daemon was started with (`XDG_CONFIG_HOME`). The
// probe writes that machine's list itself, so the run never depends on what a
// previous one left behind — and the empty state can be exercised at the end
// without touching the operator's own list.
//
// Everything this probe creates lands under the second argument (the projects
// *and* the git clones). The client itself is only ever read.
import { execFileSync } from 'node:child_process';
import { mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { chromium } from 'playwright';

const base = process.argv[2] ?? 'http://127.0.0.1:8763';
const outDir = process.argv[3] ?? '/tmp/camelot-ux';
const configDir = process.argv[4] ?? '';
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
	const body = await res.json().catch(() => null);
	return { status: res.status, body };
};
const post = async (path, payload) => {
	const res = await fetch(`${base}/api/v1${path}`, {
		method: 'POST',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify(payload)
	});
	const body = await res.json().catch(() => null);
	return { status: res.status, body };
};

// The machine's list, written as a fixture (the probe owns this directory): a
// run never depends on what the previous one left behind, and the empty state
// also comes from here.
const registry = configDir ? `${configDir}/camelot-studio/recent.json` : '';
const seed = (entries) => {
	if (!registry) return;
	writeFileSync(registry, JSON.stringify({ format: 'camelot.recent/1', projects: entries }));
};
const entry = (path, name, opened = '2026-10-16T08:00:00Z') => ({ path, name, opened });

// --- the read half: what this daemon serves -----------------------------------
const home = await api('/home');
check('home answers', home.status === 200, `status ${home.status}`);
const daemon = home.body?.daemon ?? {};
const project = home.body?.project ?? null;
check(
	'it says what the daemon serves',
	daemon.client?.length > 0 && daemon.zones >= 1,
	`${daemon.client} · ${daemon.zones} zones`
);
check('a served project is named', !!project?.path, JSON.stringify(project));
check(
	'its pending work is counted',
	project?.pending?.fixtures === 0 && project?.pending?.rows === 0,
	JSON.stringify(project?.pending)
);
check(
	'it is not read-only (this daemon holds the lock)',
	project?.read_only === false && project?.holder === ''
);

const gonePath = `${outDir}/gone-project`;
rmSync(gonePath, { recursive: true, force: true });
seed([entry(project.path, project.name), entry(gonePath, 'gone-project', '2026-10-14T08:00:00Z')]);
const listed = await api('/home');
const rows = listed.body?.recents ?? [];
const mine = rows.find((row) => row.path === project?.path);
const gone = rows.find((row) => row.path === gonePath);
check('the served project is in the machine list', !!mine, rows.map((r) => r.path).join(', '));
check('the row says what the disk says now', mine?.exists === true && mine?.project === true);
check('a folder that is gone is said by its row', gone?.exists === false);

const health = await api(`/home/health?path=${encodeURIComponent(project.path)}`);
check(
	'health reads the base back',
	health.status === 200 && health.body?.state === 'ok',
	`${health.body?.state} · ${health.body?.detail}`
);
check(
	'health carries the work waiting too, so a row needs one call',
	health.body?.pending?.fixtures === 0 && health.body?.pending?.rows === 0,
	JSON.stringify(health.body?.pending)
);

const ghost = await api(`/home/health?path=${encodeURIComponent(outDir)}`);
check(
	'health refuses a folder that is not a project, by name',
	ghost.status === 400 && /not a project/.test(ghost.body?.detail ?? ''),
	ghost.body?.detail
);

const ghostBrowse = await api(`/home/browse?path=${encodeURIComponent(outDir + '/ghost')}`);
check(
	'browse refuses a path that is not there, by name',
	ghostBrowse.status === 400 && /no such directory/.test(ghostBrowse.body?.detail ?? ''),
	ghostBrowse.body?.detail
);

// browse: a fixture of folders, one of them a project, one hidden
const servers = `${outDir}/probe-servers`;
rmSync(servers, { recursive: true, force: true });
mkdirSync(`${servers}/avalon`, { recursive: true });
mkdirSync(`${servers}/hills`, { recursive: true });
mkdirSync(`${servers}/.hidden`, { recursive: true });
writeFileSync(`${servers}/hills/project.json`, '{"format":"camelot.project/1"}');
const browse = await api(`/home/browse?path=${encodeURIComponent(servers)}`);
const names = (browse.body?.dirs ?? []).map((entry) => entry.name);
check(
	'browse lists folders, not dotfolders',
	JSON.stringify(names) === '["avalon","hills"]',
	JSON.stringify(names)
);
check(
	'browse flags the projects',
	browse.body?.dirs?.[1]?.project === true && browse.body?.dirs?.[0]?.project === false
);
check('browse names the parent', browse.body?.parent === outDir, browse.body?.parent);

// --- the client check the modal runs before anything is written ---------------
const clientCheck = await api(`/home/client?path=${encodeURIComponent(daemon.client)}`);
check(
	'the client check reads the daemon\u2019s own client',
	clientCheck.status === 200 &&
		clientCheck.body?.state === 'ok' &&
		clientCheck.body?.zones === daemon.zones,
	`${clientCheck.body?.state} · ${clientCheck.body?.detail}`
);
const notAClient = await api(`/home/client?path=${encodeURIComponent(outDir)}`);
check(
	'and says when a folder is not one',
	notAClient.body?.state === 'not-a-client',
	notAClient.body?.detail
);

// --- D4: create records the whole client, and does not have to open -----------
const freshDir = `${outDir}/probe-fresh`;
rmSync(freshDir, { recursive: true, force: true });
const fresh = await post('/home/new', {
	name: 'Probe Fresh',
	dir: freshDir,
	client: daemon.client,
	staging: true,
	open: false
});
check(
	'create writes a project and records its client',
	fresh.status === 200 &&
		fresh.body?.opened === false &&
		fresh.body?.resources > 0 &&
		fresh.body?.zones === daemon.zones,
	`${fresh.status} · ${fresh.body?.resources} resources · ${fresh.body?.zones} zones`
);
check(
	'and the daemon still serves the project it served',
	(await api('/home')).body?.project?.path === project.path
);
const freshHealth = await api(`/home/health?path=${encodeURIComponent(freshDir)}`);
check(
	'a freshly created project reads ok, with nothing to publish',
	freshHealth.body?.state === 'ok' &&
		freshHealth.body?.pending?.fixtures === 0 &&
		freshHealth.body?.pending?.rows === 0,
	`${freshHealth.body?.state} · ${JSON.stringify(freshHealth.body?.pending)}`
);

// --- D3: the switch, and what it leaves behind --------------------------------
const opened = await post('/home/open', { path: freshDir });
check(
	'opening another project switches the daemon',
	opened.status === 200 &&
		opened.body?.opened === true &&
		opened.body?.state?.dir === freshDir &&
		(await api('/home')).body?.project?.path === freshDir,
	`${opened.status} · ${opened.body?.name}`
);
check(
	'the switch answers what it leaves behind',
	opened.body?.left?.name === project.name && opened.body?.left?.pending === 0,
	JSON.stringify(opened.body?.left)
);

// One real edit in the project we now serve: a fixture moved by one unit, in
// the first zone the client ships that carries one.
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
	if (answer.status === 200) edit = { zone: zone.id, id: fixture.id };
	break;
}
check('an edit lands in the project the daemon now serves', edit !== null, JSON.stringify(edit));
const edited = await api(`/home/health?path=${encodeURIComponent(freshDir)}`);
check(
	'the row now has one edit to publish',
	edited.body?.pending?.fixtures === 1 && edited.body?.pending?.rows === 0,
	JSON.stringify(edited.body?.pending)
);

// --- the import door: in place, and it does not have to open ------------------
const editsFolder = `${outDir}/probe-import`;
rmSync(editsFolder, { recursive: true, force: true });
mkdirSync(editsFolder, { recursive: true });
writeFileSync(`${editsFolder}/mod.json`, '{"id":"probe-import","version":"1"}');
writeFileSync(`${editsFolder}/notes.md`, 'mine');
const imported = await post('/home/import', {
	dir: editsFolder,
	client: daemon.client,
	open: false
});
check(
	'import makes an edits folder a project, in place',
	imported.status === 200 && imported.body?.name === 'probe-import',
	`${imported.status} · ${imported.body?.name}`
);
check('and the folder stays where it was', (await api('/home')).body?.project?.path === freshDir);

// --- the clone door, with a real git repository --------------------------------
// `probe-fresh` is committed *with* its journal (the edit above), so the clone
// must come back carrying the same pending work.
try {
	execFileSync('git', ['init', '-q', '--initial-branch=main', freshDir]);
	execFileSync('git', ['-C', freshDir, 'config', 'user.email', 'probe@example.invalid']);
	execFileSync('git', ['-C', freshDir, 'config', 'user.name', 'probe']);
	execFileSync('git', ['-C', freshDir, 'add', '-A']);
	execFileSync('git', ['-C', freshDir, 'commit', '-qm', 'the project']);
	const cloneDir = `${outDir}/probe-clone`;
	rmSync(cloneDir, { recursive: true, force: true });
	const cloned = await post('/home/clone', {
		url: freshDir,
		dir: cloneDir,
		client: daemon.client,
		open: true
	});
	check(
		'clone runs git and opens the copy',
		cloned.status === 200 && cloned.body?.opened === true && cloned.body?.name === 'Probe Fresh',
		`${cloned.status} · ${cloned.body?.name} · ${cloned.body?.warning}`
	);
	check(
		'the edits come with the repository',
		cloned.body?.state?.pending?.fixtures === 1,
		JSON.stringify(cloned.body?.state?.pending)
	);
	check(
		'and the daemon serves the copy',
		(await api('/home')).body?.project?.path === cloneDir,
		(await api('/home')).body?.project?.path
	);
	const backAway = await post('/home/open', { path: project.path });
	check(
		'leaving the copy says what it keeps',
		backAway.body?.left?.name === 'Probe Fresh' && backAway.body?.left?.pending === 1,
		JSON.stringify(backAway.body?.left)
	);
} catch (problem) {
	console.log(`skipped the clone checks — ${problem.message}`);
}

// A clone of something that is not a project says so, and succeeds anyway.
try {
	const plain = `${outDir}/probe-plain-repo`;
	const plainClone = `${outDir}/probe-clone-plain`;
	rmSync(plain, { recursive: true, force: true });
	rmSync(plainClone, { recursive: true, force: true });
	mkdirSync(plain, { recursive: true });
	writeFileSync(`${plain}/readme.md`, 'nothing to see');
	execFileSync('git', ['init', '-q', '--initial-branch=main', plain]);
	execFileSync('git', ['-C', plain, 'config', 'user.email', 'probe@example.invalid']);
	execFileSync('git', ['-C', plain, 'config', 'user.name', 'probe']);
	execFileSync('git', ['-C', plain, 'add', '-A']);
	execFileSync('git', ['-C', plain, 'commit', '-qm', 'plain']);
	const notProject = await post('/home/clone', {
		url: plain,
		dir: plainClone
	});
	check(
		'cloning a repository that is not a project says exactly that',
		notProject.status === 200 &&
			notProject.body?.opened === false &&
			/not a project/.test(notProject.body?.warning ?? ''),
		notProject.body?.warning
	);
} catch (problem) {
	console.log(`skipped the not-a-project clone check — ${problem.message}`);
}

// --- refusals name the thing refused, and write nothing -----------------------
const refusedNew = await post('/home/new', {
	name: 'Ghost',
	dir: `${outDir}/probe-ghost`,
	client: `${outDir}/not-a-client`
});
check(
	'create refuses a client that is not there, and writes nothing',
	refusedNew.status === 400 && /no client directory/.test(refusedNew.body?.detail ?? ''),
	refusedNew.body?.detail
);
const refusedOpen = await post('/home/open', { path: outDir });
check(
	'opening what is not a project is refused, by name',
	refusedOpen.status === 400 && /not a project/.test(refusedOpen.body?.detail ?? ''),
	refusedOpen.body?.detail
);
check(
	'and the daemon still serves what it served',
	(await api('/home')).body?.project?.path === project.path
);

// --- the page -----------------------------------------------------------------
// The rows the page must show, written as a fixture: the served project, the
// one with an edit, the import (nothing to publish) and a folder that is gone.
seed([
	entry(project.path, project.name),
	entry(freshDir, 'Probe Fresh'),
	entry(editsFolder, 'probe-import'),
	entry(gonePath, 'gone-project', '2026-10-14T08:00:00Z')
]);
await page.goto(`${base}/home`, { waitUntil: 'domcontentloaded' });
const title = await until(async () => ((await page.title()) === 'Camelot Studio' ? true : null));
check('the page is Home', title === true, await page.title());
const navHome = await until(async () => {
	const active = await text('nav .on');
	return active === 'Home' && ((await text('nav')) ?? '').startsWith('Home') ? active : null;
});
check('the nav leads with Home, and it is the active one', navHome === 'Home', await text('nav'));
check(
	'the app bar says which project (or none)',
	(await text('.appbar .sub'))?.length > 0,
	await text('.appbar .sub')
);
check(
	'the app bar says which client this daemon reads',
	(await text('.appbar .chip'))?.includes('zones') === true,
	await text('.appbar .chip')
);

const headline = await until(async () => (await text('h1')) ?? null);
check('the page names the projects', headline === 'Your projects.', headline);
const doors = await texts('.door .name');
check(
	'the three ways in are on the page, in the draft\u2019s words',
	JSON.stringify(doors) === '["New project","Import a folder","Clone a repository"]',
	JSON.stringify(doors)
);

const rowText = await until(async () => {
	const found = await texts('.row');
	// the rows paint first, their health follows (`home.md` §D2)
	return found.length === 4 && found.some((row) => row.includes('1 edit to publish'))
		? found
		: null;
});
check(
	'a project with one edit says so on its row',
	(rowText ?? []).some((row) => row.includes('Probe Fresh') && row.includes('1 edit to publish')),
	JSON.stringify(rowText)
);
check(
	'a project with nothing to publish says that',
	(rowText ?? []).some((row) => row.includes('probe-import') && row.includes('nothing to publish'))
);
check(
	'a vanished folder is a row too, and it says so',
	(rowText ?? []).some((row) => row.includes('gone-project') && row.includes('folder missing'))
);
const healthText = await until(async () => {
	const found = await text('.row .chip');
	return found?.includes('client ok') ? found : null;
});
check('the row fills in its health', healthText === 'client ok', healthText);
await shot('home-01-projects');

// The switch, driven by the page: leaving the edited project is one line, and
// the line is the draft's own words.
await page.fill('.opener input', freshDir);
await page.click('.opener button');
await until(async () => ((await text('.appbar .sub')) === 'Probe Fresh' ? true : null));
await page.fill('.opener input', project.path);
await page.click('.opener button');
const notice = await until(async () => {
	const found = await text('.opener .notice');
	return found && /keeps 1 edit to publish/.test(found) ? found : null;
});
check(
	'leaving a project with work says what it keeps, in one line',
	(notice ?? '').includes('Probe Fresh keeps 1 edit to publish'),
	notice
);
check(
	'the status bar carries the client and the build',
	((await text('.statusbar')) ?? '').includes(daemon.client) === true,
	await text('.statusbar')
);
await shot('home-02-notice');

// The create modal, as the draft drew it: the fields, the client's own check,
// the footer's rule — then a real create driven from the browser.
await page.click('.door:has-text("New project")');
const modal = await until(async () => {
	const found = await text('dialog');
	return found?.includes('The client is not modified.') ? found : null;
});
check(
	'the create modal names its fields and its rule',
	['NAME', 'FOLDER', 'CLIENT', 'Initialise a git repository', 'Add a staging environment'].every(
		(label) => (modal ?? '').includes(label)
	),
	(modal ?? '').slice(0, 140)
);
const modalCheck = await until(async () => {
	const found = await text('dialog .check');
	return found?.includes('client ok') ? found : null;
});
check(
	'the modal checks the client before anything is written',
	(modalCheck ?? '').includes(`client ok \u00b7 ${daemon.zones} zones`),
	modalCheck
);
const uiDir = `${outDir}/probe-from-the-browser`;
rmSync(uiDir, { recursive: true, force: true });
await shot('home-02b-modal');
await page.fill('dialog #door-name', 'From the browser');
await page.fill('dialog #door-folder', uiDir);
await page.uncheck('dialog .option:has-text("Open it in the World editor") input');
await page.click('dialog button:has-text("Create project")');
const uiRow = await until(async () => {
	const found = await texts('.row');
	// the row paints first, its health follows (`home.md` §D2)
	return (
		found.find((row) => row.includes('From the browser') && row.includes('nothing to publish')) ??
		null
	);
});
check(
	'a project created from the browser is in the list',
	(uiRow ?? '').includes('nothing to publish'),
	uiRow
);
check(
	'and the daemon keeps serving what it served',
	(await api('/home')).body?.project?.path === project.path
);
await shot('home-03-from-the-browser');

// "Open it in the World editor", checked: the switch, then the editor.
const straightDir = `${outDir}/probe-straight-to-the-editor`;
rmSync(straightDir, { recursive: true, force: true });
await page.click('.door:has-text("New project")');
await until(async () => ((await text('dialog'))?.includes('New project') ? true : null));
await page.fill('dialog #door-name', 'Straight to the editor');
await page.fill('dialog #door-folder', straightDir);
await page.check('dialog .option:has-text("Open it in the World editor") input');
await page.click('dialog button:has-text("Create project")');
const landed = await until(async () => (/\/world$/.test(page.url()) ? true : null));
check('a project opened in the World editor lands there', landed === true, page.url());

// --- the chip on the world page ------------------------------------------------
const chip = await until(async () => await text('.appbar .chip'));
check(
	'the world editor wears the project chip',
	(chip ?? '').includes('Straight to the editor') === true,
	chip
);
check('and it says everything is published', (chip ?? '').endsWith('all published') === true, chip);
await shot('home-04-chip');

// --- the empty state (only when the probe owns the config directory) ----------
if (registry) {
	rmSync(registry, { force: true });
	await page.goto(`${base}/home`, { waitUntil: 'domcontentloaded' });
	const empty = await until(async () => await text('.empty'));
	check(
		'an empty list says how it fills, instead of nothing',
		(empty ?? '').includes('No projects on this machine yet'),
		empty
	);
	await shot('home-05-empty');
}

check(
	'no uncaught page errors',
	errors.filter((line) => !/status of 400/.test(line)).length === 0,
	errors.join(' | ')
);

await browser.close();
console.log(failures === 0 ? '\nall checks passed' : `\n${failures} check(s) failed`);
process.exit(failures === 0 ? 0 : 1);

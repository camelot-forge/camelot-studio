// Slice 2a — Home, the read half, driven like an operator (`notes/home.md`):
// what this daemon serves, the machine's projects with their health, the path
// field's check, and the empty state. Throwaway harness; exits non-zero on a
// failed check.
//
//   node scripts/home_probe.mjs http://127.0.0.1:8763 /tmp/camelot-ux /tmp/home-probe-config
//
// The daemon must serve a *project* (the setup creates one with
// `camelot project new`, so the health check has a real base to read back), and
// the third argument is the config directory that daemon was started with
// (`XDG_CONFIG_HOME`). The probe writes that machine's list itself, so the run
// never depends on what a previous one left behind — and the empty state can be
// exercised at the end without touching the operator's own list.
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
page.setDefaultTimeout(60000);
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
async function until(probe, ms = 20000) {
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
	let body = null;
	try {
		body = await res.json();
	} catch {
		body = null;
	}
	return { status: res.status, body };
};

// --- the API: what Home paints from ------------------------------------------
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

// The machine's list, written as a fixture: the served project, and one whose
// folder is gone (the row must say so, not the registry).
const gonePath = `${outDir}/gone-project`;
rmSync(gonePath, { recursive: true, force: true });
const registry = `${configDir}/camelot-studio/recent.json`;
if (configDir) {
	writeFileSync(
		registry,
		JSON.stringify({
			format: 'camelot.recent/1',
			projects: [
				{ path: project.path, name: project.name, opened: new Date().toISOString() },
				{ path: gonePath, name: 'gone-project', opened: '2026-10-14T08:00:00Z' }
			]
		})
	);
}
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

// --- the page -----------------------------------------------------------------
await page.goto(`${base}/home`, { waitUntil: 'domcontentloaded' });
const title = await until(async () => ((await page.title()) === 'Camelot Studio' ? true : null));
check('the page is Home', title === true, await page.title());
const navHome = await until(async () => {
	const active = await text('nav .on');
	return active === 'Home' && ((await text('nav')) ?? '').startsWith('Home') ? active : null;
});
check('the nav leads with Home, and it is the active one', navHome === 'Home', await text('nav'));
check('the app bar says which project (or none)', (await text('.appbar .sub'))?.length > 0, await text('.appbar .sub'));
check(
	'the app bar says which client this daemon reads',
	(await text('.appbar .chip'))?.includes('zones') === true,
	await text('.appbar .chip')
);

const headline = await until(async () => (await text('h1')) ?? null);
check('the page names the projects', headline === 'Your projects.', headline);

const rowText = await until(async () => {
	const found = await texts('.row');
	return found.length ? found : null;
});
check(
	'the served project is a row',
	(rowText ?? []).some((row) => row.includes(project.name)),
	JSON.stringify(rowText)
);
check(
	'a vanished folder is a row too, and it says so',
	(rowText ?? []).some((row) => row.includes('gone-project') && row.includes('folder missing')),
	JSON.stringify(rowText)
);
const healthText = await until(async () => {
	const found = await text('.row .chip');
	return found?.includes('client ok') ? found : null;
});
check('the row fills in its health', healthText === 'client ok', healthText);
check(
	'the page says how many projects this machine has',
	/2 on this machine/.test((await text('.count')) ?? ''),
	await text('.count')
);
await shot('home-01-projects');

// the path field's check, driven like an operator
await page.fill('.opener input', project.path);
await page.click('.opener button');
const checked = await until(async () => {
	const found = await text('.opener .hint');
	return found && found !== 'Checks the folder, and the client it recorded.' ? found : null;
});
check(
	'checking a folder says what it is and how its client stands',
	(checked ?? '').includes(project.name) && (checked ?? '').includes('unchanged'),
	checked
);

await page.fill('.opener input', outDir);
await page.click('.opener button');
const refused = await until(async () => {
	const found = await text('.opener .hint');
	return found && /not a project/.test(found) ? found : null;
});
check('checking a folder that is not one says so', !!refused, refused);

check(
	'the status bar carries the client and the build',
	((await text('.statusbar')) ?? '').includes(daemon.client) === true,
	await text('.statusbar')
);

// --- the empty state (only when the probe owns the config directory) ----------
if (configDir) {
	rmSync(registry, { force: true });
	await page.reload({ waitUntil: 'domcontentloaded' });
	const empty = await until(async () => await text('.empty'));
	check(
		'an empty list says how it fills, instead of nothing',
		(empty ?? '').includes('No projects on this machine yet'),
		empty
	);
	await shot('home-02-empty');
}

// --- the chip on the world page ------------------------------------------------
await page.goto(`${base}/world`, { waitUntil: 'domcontentloaded' });
const chip = await until(async () => await text('.appbar .chip'));
check('the world editor wears the project chip', (chip ?? '').includes(project.name) === true, chip);
check('and it says the session is saved', (chip ?? '').endsWith('saved') === true, chip);
await shot('home-03-chip');

// The probe *asks* for that 400 (the refusal checks above), so the browser's own
// "Failed to load resource" line for it is the harness's noise, not the app's.
const unexpected = errors.filter((line) => !/status of 400/.test(line));
check('no unexpected console errors', unexpected.length === 0, unexpected.slice(0, 3).join(' | '));

await browser.close();
console.log(failures === 0 ? '\nall checks passed' : `\n${failures} FAILED`);
process.exit(failures === 0 ? 0 : 1);

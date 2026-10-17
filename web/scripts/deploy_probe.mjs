// P4 — Deploy, driven like an operator (`pipeline.md` §6, draft 5): the page
// reads the project's releases, the environments this machine names and the
// deploy log; the dry run opens before any push; the push writes and is
// recorded; a drift refuses until the operator adopts it. Throwaway harness;
// exits non-zero on a failed check.
//
//   node scripts/deploy_probe.mjs http://127.0.0.1:8763 /tmp/camelot-ux
//
// Run the scratch setup first — this probe mutates the two environments, so a
// second run needs a fresh one (it deploys, adopts a drift, and deploys again).
//
// The daemon must serve a *project* with one release and two environments, each
// with its own client (`local` and `production`, both named in `machine.json`).
// The synthetic scratch the agent used:
//
//   pj.new(dir, client=SYNTHETIC, name="Deploy Probe")  # + one edit + rel.cut
//   machine.json: {"environments": {"local": …, "production": …}}
//   each environment: a copy of the same synthetic client at <env>/client
//
// Nothing here touches the operator's own client: the probe reads the API and
// the browser, and corrupts one file it must corrupt (a deployed package, to
// make a drift).
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
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
page.setDefaultTimeout(60000);
const errors = [];
page.on('console', (msg) => {
	if (msg.type() !== 'error') return;
	// A refusal the page shows on purpose (the deliberate cut below answers 400)
	// also logs a network line; this list is about the page's own errors.
	if (msg.text().startsWith('Failed to load resource')) return;
	errors.push(msg.text());
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
const attr = (sel, name) =>
	page.evaluate(([s, n]) => document.querySelector(s)?.getAttribute(n) ?? null, [sel, name]);
async function until(probe, ms = 30000) {
	const started = Date.now();
	for (;;) {
		const value = await probe();
		if (value) return value;
		if (Date.now() - started > ms) return null;
		await page.waitForTimeout(150);
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

// --- the facts, before anything is drawn -------------------------------------
const facts = (await api('/deploy')).body;
check(
	'the daemon serves a project with one release',
	facts?.releases?.length === 1,
	JSON.stringify(facts?.releases?.map((r) => r.number))
);
const release = facts.releases[0];
const packName = release.server.pack.split('/').pop();
const local = facts.environments.find((e) => e.name === 'local');
const production = facts.environments.find((e) => e.name === 'production');
check(
	'two environments, each with a client',
	!!local?.client?.exists && !!production?.client?.exists
);
check('nothing is deployed anywhere yet', !local?.live && !production?.live);

// --- the page -----------------------------------------------------------------
await page.goto(`${base}/deploy`);
await until(async () => (await text('.head p'))?.includes('Deploy Probe'));
check(
	'the page names the project it serves',
	(await text('.head h1')) === 'Deploy',
	await text('.head p')
);
check(
	'the chip carries the project',
	(await text('.chip'))?.includes('Deploy Probe') ?? false,
	await text('.chip')
);
check(
	'the app bar carries Deploy as the fifth tool',
	(await texts('nav a.seg')).join(' ') === 'Home World Figures Builder Deploy',
	(await texts('nav a.seg')).join(' ')
);
check(
	'and it is the active one',
	(await attr('nav a.seg[aria-current="page"]', 'href')) === '/deploy' ||
		(await text('nav a.seg[aria-current="page"]')) === 'Deploy'
);

// --- releases -----------------------------------------------------------------
check('the releases panel lists the cut', (await texts('.rows .num'))[0] === '0001');
check(
	'with what it holds',
	(await texts('.rows .what'))[0]?.includes('world'),
	(await texts('.rows .what'))[0]
);
check(
	'and where it is live — nowhere, at first',
	(await texts('.rows .where'))[0] === 'not deployed'
);
check(
	'the panel says a cut is rebuilt, never stored twice',
	(await text('.card[aria-label="releases"] .hint'))?.includes('verify --rebuild')
);

// --- environments -------------------------------------------------------------
const cards = await texts('.env');
check('both environments are drawn', cards.length === 2, String(cards.length));
check(
	'each starts as nothing deployed',
	cards.every((line) => line.includes('nothing deployed'))
);
check(
	'each names its folder',
	(await texts('.env .path')).every((line) => line.includes('/tmp/p4-deploy/env-')),
	(await texts('.env .path')).join(' | ')
);
check(
	'and says its client is there',
	cards.every((line) => line.includes('client ok'))
);

// --- the dry run (opened by itself, newest release → first environment) -------
await until(async () => (await text('.select-line')) !== null);
check(
	'the dry run opened on its own',
	(await text('.select-line'))?.startsWith('0001 → local'),
	await text('.select-line')
);
const items = await texts('.item');
check(
	'it lists the client entry it would write',
	items.some((line) => line.includes('fixtures.csv') && line.includes('write')),
	items[0]
);
check(
	'with why, and the bytes on both sides',
	items[0]?.includes('the target') && items[0]?.includes('theirs') && items[0]?.includes('ours')
);
check(
	'it lists the install’s own state file',
	items.some((line) => line.includes('.camelot/state.json'))
);
check(
	'it lists the server package',
	items.some((line) => line.includes(packName))
);
check(
	'the count says three writes and nothing else',
	(await text('.card[aria-label="dry run"] .card-meta'))?.includes('3 write'),
	await text('.card[aria-label="dry run"] .card-meta')
);
const lines = await texts('.release-lines li');
check(
	'the release lines carry the order',
	lines.some((line) => line === 'order client-first'),
	lines.join(' | ')
);
check(
	'the ids checked against this environment',
	lines.some((line) => line.includes('id(s) checked against local'))
);
check(
	'the evidence the target carries',
	lines.some((line) => line.includes('evidence is not there yet'))
);
check(
	'the deploy button names the release and the environment',
	(await text('.acts.end .btn--primary'))?.includes('Deploy 0001 → local'),
	await text('.acts.end .btn--primary')
);

// --- the feed -----------------------------------------------------------------
check(
	'the feed panel says there is none yet, and names the path',
	(await text('.card[aria-label="feed"] .hint'))?.includes('No feed yet') &&
		(await text('.card[aria-label="feed"] .path'))?.endsWith('/feed'),
	await text('.card[aria-label="feed"] .path')
);

// --- the rail carries the same verbs the CLI runs -------------------------------
const verbs = await page.evaluate(() =>
	Array.from(document.querySelectorAll('.rail .tool')).map((b) => b.getAttribute('aria-label'))
);
check(
	'the rail carries the pipeline’s verbs',
	verbs.join(' · ') === 'Cut a release · Dry run · Deploy · Promote · Read it back · Publish',
	verbs.join(' · ')
);
check(
	'publish waits for P6',
	await page.evaluate(
		() => document.querySelector('.rail .tool[aria-label="Publish"]')?.disabled === true
	)
);
await page.click('.rail .tool[aria-label="Dry run"]');
await until(async () => (await text('.card[aria-label="dry run"] .card-meta'))?.includes('3 write'));
check('the dry run can be asked for from the rail', (await text('.card[aria-label="dry run"] .card-meta'))?.includes('3 write'), await text('.card[aria-label="dry run"] .card-meta'));

// --- the push -----------------------------------------------------------------
await page.click('.acts.end .btn--primary');
await until(async () => (await text('.env .pill')) === 'live');
check(
	'the local environment turns live',
	(await texts('.env .pill'))[0] === 'live',
	(await texts('.env .pill')).join(' ')
);
check(
	'the push says what it wrote',
	(await text('.card[aria-label="dry run"] .line'))?.includes('file(s) written'),
	await text('.card[aria-label="dry run"] .line')
);
check(
	'the status bar names the last attempt',
	(await text('.statusbar .mid'))?.includes('deployed 0001 local ok'),
	await text('.statusbar .mid')
);
check(
	'the releases row now says where it is live',
	(await texts('.rows .where'))[0]?.includes('local'),
	(await texts('.rows .where'))[0]
);
const log = (await api('/deploy')).body.log.filter((line) => line.kind === 'deploy');
check(
	'one deploy line was recorded',
	log.length === 1 && log[0].outcome === 'ok',
	JSON.stringify(log)
);

// --- the read-back -------------------------------------------------------------
await until(async () => (await texts('.env .btn')).some((line) => line.includes('Read it back')));
await page.click('.env .btn--ghost');
await until(async () => (await text('.env .line.ok'))?.includes('read back'));
check(
	'the environment reads back',
	(await text('.env .line.ok'))?.includes('read back: release 1'),
	await text('.env .line.ok')
);

// --- a second dry run: nothing would be written --------------------------------
await until(async () =>
	(await text('.card[aria-label="dry run"] .card-meta'))?.includes('3 unchanged')
);
check(
	'the second dry run says there is nothing to write',
	(await text('.card[aria-label="dry run"] .hint'))?.includes('Every destination already holds'),
	await text('.card[aria-label="dry run"] .hint')
);
check(
	'and the deploy button is disabled',
	await page.evaluate(() => document.querySelector('.acts.end .btn--primary')?.disabled === true)
);
const again = await post('/deploy/deploy', { release: 1, env: 'local' });
check(
	'the API’s second push answers unchanged, with nothing written',
	again.body?.outcome === 'unchanged' && again.body.wrote.length === 0,
	JSON.stringify(again.body?.outcome)
);
const after = (await api('/deploy')).body.log.filter((line) => line.kind === 'deploy');
check(
	'and the log keeps both attempts',
	after.length === 2 && after[0].outcome === 'unchanged',
	after.map((line) => line.outcome).join(',')
);

// --- the other environment -----------------------------------------------------
const dryHere = await page.evaluate(() => {
	const cards = Array.from(document.querySelectorAll('.env'));
	const found = cards.find((card) => card.textContent.includes('production'));
	const button = found?.querySelector('.btn--secondary');
	button?.click();
	return !!button;
});
check('an environment can be asked for a dry run from its own card', dryHere);
await until(async () => (await text('.select-line'))?.includes('production'));
check(
	'the dry run follows the choice',
	(await text('.select-line'))?.includes('0001 → production'),
	await text('.select-line')
);
await until(async () => (await text('.acts.end .btn--primary'))?.includes('production'));
await page.click('.acts.end .btn--primary');
await until(async () => (await texts('.env .pill')).every((line) => line === 'live'));
check('both environments are live now', (await texts('.env .pill')).join(' ') === 'live live');
check(
	'the release says it is live in both',
	(await texts('.rows .where'))[0]?.includes('local · production'),
	(await texts('.rows .where'))[0]
);

// --- a drift, and the operator's way through it --------------------------------
const packagePath = `${production.root}/server/camelot/releases/0001/${packName}`;
check('the deployed package is where the record says', existsSync(packagePath), packagePath);
writeFileSync(packagePath, 'not the pack any more');
const table = await post('/deploy/plan', { release: 1, env: 'production' });
check(
	'the dry run calls it a drift',
	table.body?.counts?.drift === 1,
	JSON.stringify(table.body?.counts)
);
check(
	'and refuses until it is adopted',
	(table.body?.refusals ?? []).some((line) => line.includes('does not record')),
	(table.body?.refusals ?? []).join(' | ')
);
await page.evaluate(async () => {
	await fetch('/api/v1/deploy/plan', {
		method: 'POST',
		headers: { 'content-type': 'application/json' },
		body: JSON.stringify({ release: 1, env: 'production' })
	});
});
const cardsBefore = await page.evaluate(() => {
	const cards = Array.from(document.querySelectorAll('.env'));
	const found = cards.find((card) => card.textContent.includes('production'));
	found?.querySelector('.btn--secondary')?.click();
	return true;
});
check('the page can be pointed at the drifted environment again', cardsBefore);
await until(async () => (await text('.verdict[data-v="drift"]')) !== null);
check('the drift row is drawn as a drift', (await text('.verdict[data-v="drift"]')) === 'drift');
check(
	'the deploy button waits for the operator',
	await page.evaluate(() => document.querySelector('.acts.end .btn--primary')?.disabled === true)
);
const adopt = await text('.acts.end .btn--ghost');
check('and offers to adopt what is there now', adopt?.includes('Adopt what is there now'), adopt);
await page.click('.acts.end .btn--ghost');
await until(async () => (await text('.card[aria-label="dry run"] .line'))?.includes('written'));
check(
	'adopting deploys it',
	(await text('.card[aria-label="dry run"] .line'))?.includes('file(s) written'),
	await text('.card[aria-label="dry run"] .line')
);
const read = await post('/deploy/verify', { env: 'production' });
check(
	'and the environment reads back clean',
	read.body?.ok === true,
	JSON.stringify(read.body?.problems)
);

await shot('deploy-page');

// --- the first verb: cutting, and its refusal ----------------------------------
await page.click('.rail .tool[aria-label="Cut a release"]');
await until(async () => (await text('.problem'))?.includes('nothing to release'));
check(
	'cutting with nothing new is refused, in the kernel’s own words',
	(await text('.problem'))?.includes('nothing to release'),
	await text('.problem')
);

// --- the console is quiet ------------------------------------------------------
check('no console errors', errors.length === 0, errors.slice(0, 3).join(' | '));

await browser.close();
console.log(failures === 0 ? '\nall checks passed' : `\n${failures} check(s) failed`);
process.exit(failures === 0 ? 0 : 1);

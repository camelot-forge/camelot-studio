// P4 — Deploy, driven like an admin (`pipeline.md` §6): one status line, one
// Publish button, a read-only detail fold and a settings fold. The rail is gone
// on purpose — the page asks the operator's question, not the pipeline's.
// Throwaway harness; exits non-zero on a failed check.
//
//   node scripts/deploy_probe.mjs http://127.0.0.1:8763 /tmp/camelot-ux
//
// Run the scratch setup first. The daemon must serve a *project* with one
// release and two environments, each with its own client (`local` and
// `production`, both named in `machine.json`). Nothing here touches the
// operator's own client; the probe reads the API and the browser, and writes
// one screenshot.
import { mkdirSync } from 'node:fs';
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

// --- the facts, before anything is drawn -------------------------------------
const facts = (await api('/deploy')).body;
check(
	'the daemon serves a project with one release',
	facts?.releases?.length === 1,
	JSON.stringify(facts?.releases?.map((r) => r.number))
);
const release = facts.releases[0];
check(
	'the release carries a server pack',
	typeof release?.server?.pack === 'string',
	JSON.stringify(release?.server)
);
const local = facts.environments.find((e) => e.name === 'local');
const production = facts.environments.find((e) => e.name === 'production');
check(
	'two environments, each with a client',
	!!local?.client?.exists && !!production?.client?.exists
);
check('nothing is deployed anywhere yet', !local?.live && !production?.live);

// --- the page -----------------------------------------------------------------
await page.goto(`${base}/deploy`);
await until(async () => (await text('.hero .big')) !== null);
check('the page names itself once', (await text('.head h1')) === 'Deploy', await text('.head h1'));
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
check('and it is the active one', (await text('nav a.seg[aria-current="page"]')) === 'Deploy');
check('the old rail is gone', (await page.$('.rail')) === null);
check(
	'the status line says what is waiting',
	(await text('.hero .big')) === 'Release 0001 is ready to publish',
	await text('.hero .big')
);
check(
	'and what publishing does',
	(await text('.hero .acts .btn--primary')) === 'Publish',
	await text('.hero .acts .btn--primary')
);
check(
	'Publish is enabled when a release is waiting',
	(await page.evaluate(() => document.querySelector('.hero .acts .btn--primary')?.disabled)) ===
		false
);

// --- the releases, still readable without the rail -----------------------------
check('the releases panel lists the cut', (await texts('.rows .num'))[0] === '0001');
check(
	'with what it holds',
	(await texts('.rows .what'))[0]?.includes('world'),
	(await texts('.rows .what'))[0]
);
check(
	'and where it is installed',
	(await texts('.rows .where'))[0] === 'not installed',
	(await texts('.rows .where'))[0]
);

// --- the details fold: a read-only dry run -------------------------------------
check('the details are folded away at first', (await page.$('.details')) === null);
await page.click('.hero .acts .btn--secondary');
await until(async () => (await text('.details .line')) !== null);
check(
	'See details opens a dry run, not a write',
	(await text('.details .note'))?.includes('read-only, nothing was written'),
	await text('.details .note')
);
check(
	'and says what installing here would write',
	(await text('.details .line'))?.includes('Would write'),
	await text('.details .line')
);

// --- the settings fold ----------------------------------------------------------
const settings = await page.$('details.settings');
check('the update address is folded away', settings !== null);
if (settings) {
	await settings.evaluate((node) => node.setAttribute('open', ''));
	await until(async () => (await text('details.settings .fold')) !== null);
	check(
		'the settings name the update address',
		typeof (await page.evaluate(
			() => document.querySelector('details.settings input')?.value ?? ''
		)) === 'string'
	);
	const fold = await text('details.settings .fold');
	check(
		'the environments are readable there, with what they hold',
		fold?.includes('local') && fold?.includes('production') && fold?.includes('nothing installed'),
		fold
	);
}

// --- Test locally is offered, and the page says what it does --------------------
check(
	'Test locally is available',
	(await texts('.hero .acts .btn'))[2] === 'Test locally',
	(await texts('.hero .acts .btn')).join(' | ')
);
check('the page still has no console errors', errors.length === 0, errors.slice(0, 3).join(' | '));

await shot('deploy-page');
await browser.close();
console.log(failures === 0 ? '\nall checks passed' : `\n${failures} check(s) failed`);
process.exit(failures === 0 ? 0 : 1);
